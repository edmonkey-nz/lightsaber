# Lightsaber — technical reference

Implementation detail behind [README.md](../README.md): how the renderer is put
together, the file formats, and the extension points.

- [Architecture](#architecture)
- [Module map](#module-map)
- [Canvas and shapes](#canvas-and-shapes)
  - [Masks](#masks)
  - [Per-shape overrides](#per-shape-overrides)
- [Effectors](#effectors)
- [Visualisers](#visualisers)
- [Media](#media)
  - [Linked media and media roots](#linked-media-and-media-roots)
  - [Video playback](#video-playback)
- [Sequencer](#sequencer)
- [Outputs](#outputs)
- [Multi-projector output](#multi-projector-output)
  - [Topologies](#topologies)
  - [Why one host, not a render node per projector](#why-one-host-not-a-render-node-per-projector)
  - [The correction chain](#the-correction-chain)
  - [Groups](#groups)
  - [Profiles and shows](#profiles-and-shows)
  - [Hardware constraints](#hardware-constraints)
  - [Prototype status](#prototype-status)
- [Performance](#performance)
- [Files on disk](#files-on-disk)

---

## Architecture

```
       CompositeRenderer  ── own thread and clock (composite.py)
                 │  per-shape layers: corners after effectors, compositing
                 │  settings, and a video playhead (JSON over one websocket)
                 ▼
   Browser: warp each shape's video/image/webcam/text into its quad,
            then mask, key, filter and composite
     (the canvas editor, and every open Output window)
```

Two decisions shape everything else:

**The server never touches pixels.** `CompositeRenderer` owns time, state and
effector resolution, and publishes one list of layer dicts per tick. Video is
decoded and drawn in the browser; the server only says which frame each clip
should be on (see [Video playback](#video-playback)).

**The warp happens client-side.** The browser does the unit-square → quad
homography. The same payload drives the in-page editor and every Output
window, so what you see while editing is what the projector gets.

Mutations from the websocket never touch renderer state directly — they're
queued and drained at the top of each render tick, so the network thread
can't race the render thread.

## Module map

| Module | Role |
|---|---|
| `canvases.py` | `PolygonSpec` / `CanvasSpec` / `CanvasManager` |
| `effectors.py` | Modulation matrix for canvas shapes — sources, routes, anchors |
| `sequencer.py` | `Step` / `Sequence` / `Sequencer` — canvas playback over time |
| `projects.py` | `ProjectSpec` / `ProjectManager` + `OutputMonitorConfig` |
| `media_roots.py` | Linked-media allowlist, path resolution, directory browsing |
| `media_meta.py` | Per-asset duration and default trim |
| `composite.py` | `CompositeRenderer` — the render loop, Start/Stop, playhead |
| `settings.py` | Local settings (media roots, current project) — gitignored |
| `engine.py` | Thin coordinator wiring the above for the web server |
| `web/` | aiohttp server, control UI (`index.html`), output (`output.html`) |

## Canvas and shapes

A `CanvasSpec` is an ordered list of `PolygonSpec`. A shape is four corners
(clockwise from top-left, `+y` up) plus what fills it — `source_type` is one
of `media`, `webcam`, `text`, or `knockout`. A media shape with no file set
draws nothing; that is what a new shape starts as.

Shapes saved while vector scenes existed (`source_type: "scene"`) load as
empty media shapes: corners, mask, z-order and effector routes are kept, and
the old scene name becomes the label if the shape had none. Scene-only
overrides (`glow`, `layer0`, `camera`, `disable_plane`) are dropped on load.

`knockout` is the odd one out: an arbitrary-point shape with no content of
its own, there purely to act on what it overlaps. Use `z_index`
(1 = frontmost, 10 = backmost) to place it. It has two modes:

- **Blackout** (`knockout_punch: false`, the default) — an opaque black fill
  painted at its own z, hiding everything behind it all the way to the
  output.
- **Punch-through** (`knockout_punch: true`) — nothing is painted; the
  shape's outline is *erased* from the layers behind it, so content further
  back shows through the hole. `knockout_depth` says how far back the erase
  reaches, counted in **z-levels**: a cutout at `z_index` k erases layers
  whose `z_index` is in `[k+1, k+knockout_depth]` and leaves anything deeper
  alone. Since z only runs to 10, the default depth of 10 always reaches the
  back of the stack. Shapes at the *same* z are never punched — order within
  a z-level is array order, so punching there would depend on creation
  order.

The renderers implement punch-through as a Canvas2D `destination-out` fill.
A punch whose band already reaches z 10 covers everything behind it, so it
is applied straight onto the frame buffer at its own place in the paint
order — "erase everything painted so far" is exactly what that means, one
extra fill and no extra surface. A *bounded* punch can't work that way (by
then the frame buffer has merged the layers it should erase with the ones it
should spare), so each layer inside the band is drawn into a side buffer,
punched there, and blitted back. Punching each layer separately then
stacking is identical to stacking then punching *only* while the erase is
fully opaque, which is why a cutout's own opacity is deliberately not used
as a partial-erase amount.

**Feather** (`feather`, 0-20) softens whatever edge a shape presents: its
mask edge on a content shape, its cutout edge on a knockout (either mode).
0 is the hard edge that was the only behaviour before. The value is in
PROJECT-CANVAS pixels, converted to each surface's own pixels at draw time,
so one number looks the same in the editor preview and on an output window
of any size — Canvas2D's blur filter is a device-pixel measurement and
ignores the current transform, so this conversion has to be explicit.

The ramp is symmetric about the authored edge, the way feather works in an
image editor: content bleeds up to ~feather px *outside* the mask at falling
alpha. Worth knowing when a mask is aligned to a physical surface. A shape
being feathered is drawn into a side buffer with its hard clip suppressed
(the blurred mask does the masking instead — clipping first would cut the
content off at the edge and leave the outer half of the ramp with nothing to
fade), then masked by one blurred fill through `destination-in`.

This replaces an earlier feathered clip that was removed for cost: that one
allocated an offscreen canvas per shape per tick and blurred the *content*.
Cost on hardware is ~0.45-0.66ms per feathered shape (measured with a GPU
sync; an unsynced measurement understates it roughly tenfold). The buffer is also clipped to the shape's bounding
box, which is a large win only under software rasterisation and neutral on a
GPU; it is kept for the software case. See ARCHITECTURE.md's GPU-cost list,
items 4-5, including why a headless browser cannot measure any of this.

**Key** (`key_mode`, raster sources only — media and webcam) removes a colour.
`colour` keys a hue; `luma` keys everything darker than a threshold, for
footage on a black field. `key_tolerance` sets where the matte breaks (UP keys
MORE — a pixel drops once its resemblance to `key_color` passes
`1 - tolerance`), `key_softness` is the ramp above that, `key_spill` pulls the
key channel down toward the mean of the other two, and `key_feather` blurs the
matte in project-canvas px.

It is one SVG filter applied through `ctx.filter` — declarative, GPU-accelerated,
no WebGL context and no per-pixel JavaScript, riding the same filter mechanism
`buildCssFilter` already uses. Three things in that chain are load-bearing:

- The filter carries `color-interpolation-filters="sRGB"`. Filters default to
  linear light, which silently skews every matrix in the chain.
- The matte is computed on a branch straight off `SourceGraphic`, **not** off
  the spill-suppressed colour — suppressing the key hue first stops the key
  colour reading as key at all.
- That branch zeroes its own RGB and is used only for its alpha. Driving alpha
  to 0 on the *colour* branch destroys the colour, because the pipeline is
  premultiplied, and raising alpha again afterwards cannot recover it: every
  kept pixel comes out black.
- Spill uses `feBlend mode="darken"` (a per-channel min) against a "key channel
  := mean of the other two" version, so it can only ever pull that channel
  down. A plain weighted blend raises it on colours that have none of it —
  pure red picked up a green cast.

The key runs **after** the quad warp, over the shape's side buffer, alongside
the feather. Keying before the warp would match pristine source pixels and give
slightly cleaner edges, but it would push per-pixel alpha through the
72-triangle mesh, whose 1.5px overlap skirts composite twice — turning any soft
matte edge into a visible grid (the same artifact class as the per-primitive
opacity bug). Cost on hardware is ~0.68ms per keyed shape, measured with a GPU sync —
see ARCHITECTURE.md's GPU-cost list, item 4, on why an unsynced number is
about ten times too optimistic.

**FX** are post-warp treatments applied over the shape's side buffer next to
the key, in a fixed order: `fx_pixelate`, then `fx_solarize` -> `fx_posterize`
-> `fx_invert` as one tone curve, then `fx_duotone` mapping the result onto
`fx_duotone_dark`/`fx_duotone_light`. They run after the warp, so they apply to
any fill — media, webcam or text — and only a cutout is excluded.

`fx_pixelate` is a block size in project-canvas px and is **not** a filter: it
is a downscale and redraw with `imageSmoothingEnabled=false`, which measured
cheaper than every filter primitive tried. The other four are one
`feComponentTransfer` plus, for duotone, a luminance matrix and a mix — see
ARCHITECTURE.md's GPU-cost list for why they share a single lookup table and
why edge-detect and emboss are deliberately absent (`feConvolveMatrix` and
`feMorphology` are CPU fallbacks in Chromium, at +108ms and +61ms per
full-size shape).

One trap worth naming: an `feFunc*` takes **`tableValues`**, not `values`.
`values` is `feColorMatrix`'s attribute; setting it on an `feFunc` fails
silently, which presents as the tone curve doing nothing and duotone coming
out greyscale.

**Kaleidoscope** (`mirror_kaleido`, an `overrides` key alongside the mirror
family) repeats the wedge between 0 and 360/n degrees of the already-drawn
shape around its own centre. 0/1 = off, 2-10 = segment count.

An **even** count alternates mirrored wedges, which is what lets a kaleidoscope
close seamlessly all the way round. An **odd** count cannot — the first and
last wedge would meet with the same handedness — so odd counts repeat by
rotation only rather than leaving one visibly mismatched seam. The mapping is:
even wedge `i` shows `source(t)` rotated by `i*step`; odd wedge `i` shows
`source(step - t)`, reached by rotating `(i+1)*step` and reflecting.

It runs on the side buffer after the key and FX, and is clipped to the shape's
own quad — rotating a wedge otherwise throws content into the bbox corners a
rotated quad does not cover, painting past the surface the shape was aligned
to. It needs a snapshot canvas because the wedges are drawn back over the same
buffer they are read from.

**Fit** controls how content of one aspect fills a quad of another —
`stretch`, `fit` (letterbox) or `crop`.

**Lock** (`locked`) makes a shape inert to the mouse: no corner drag, no
body move, and clicks pass through to whatever is underneath. It stays
selectable from the Shapes list, and effectors still animate it — the lock
is about the mouse, not about freezing the shape.

### Masks

Two independent mechanisms, easy to confuse:

**Preset clip shapes** (circle, hexagon, triangle, square) are computed from
the quad's screen-space bounding box, with a size multiplier. They do *not*
follow the corner pins — a circle clip stays circular however hard the quad
is keystoned. Hard-edged only; feathering was removed because a CSS blur per
shape per tick visibly stalled the render loop.

**Custom polygon masks** (`clip_points`) are an editable N-point silhouette
stored in the quad's own UV space and mapped through the same homography as
the content. So the mask warps *with* the corner pins — the shape keeps its
silhouette as you pin it to a surface. This is the After Effects model: a
mask lives in layer space, and a corner-pin applied afterwards warps both.

The editor is modal, since both geometries want click-and-drag on the same
pixels. **Warp** drags the four corner pins; **Mask** drags the silhouette
(double-click an edge to add a point, a point to remove it). The inactive
one's handles are drawn demoted rather than hidden. `Esc` returns to Warp.
Mode is editor state and is never persisted.

### Per-shape overrides

Everything below is canvas-local — it never modifies the source file, so the
same clip can look different in two shapes.

- **Colourize** — trails, brightness, contrast, hue, saturation, and a
  colourize amount/hue pair. Trails are frame-wide: the output fades its
  previous frame instead of clearing it, and the strongest setting on any
  shape wins.
- **Transform** — mirror x/y with adjustable fold points, pre- or
  post-distort, plus static rotate and scale about a chosen anchor.

A colour filter on a distorted shape is applied once, to the finished warp,
not per mesh triangle — see [Performance](#performance).

Compositing note: shapes are drawn into a full-frame scratch buffer at their
own opacity, and master gain is applied once to the finished frame. Applying
gain per-primitive instead made the ~1.5px anti-seam overlap between warped
triangles double-blend into a visible grid at fractional opacity.

## Effectors

A per-canvas modulation matrix (`effectors.py`), saved with the canvas.

**Sources** are `lfo` (sine, triangle, saw, ramp, square, sample & hold —
free-running in Hz or synced to tempo) or `audio` (live mic, split into
`low` / `mid` / `high` / `master` bands). Routes can reference an audio band
directly without first creating a source card.

**Targets** are per-shape: `hue`, `saturation`, `brightness`, `opacity`,
`position_x`, `position_y`, `scale`, `rotation`.

Geometry targets are *deltas*, not absolutes — a quad is four corners with no
decomposed position/scale/rotation basis, so they're applied around a chosen
anchor from a 3×3 grid (`transform_anchor`). Static rotate/scale set in the
Transform drawer act as the base an effector composes on top of: add for
rotation, multiply for scale.

Routes have a `depth`, an `offset`, a `curve` (linear / exponential /
logarithmic) and a `mode` (add / multiply / replace).

While stopped, sources still meter live so you can tune an LFO before
pressing Start, but shapes hold their unmodulated pose.

## Visualisers

A `visual` shape is filled by a GLSL fragment shader instead of a file or a
camera. Presets and their parameters (label, range, default) are defined once,
in `lightsaber/visuals.py`; the shaders live in `web/static/visuals.js`, shared
by both pages and reading each parameter as a `u_<key>` uniform. The editor
builds its sliders from `GET /visuals/presets`. Per-shape tweaks are stored
sparse and per preset (`visual_params: {"clouds": {"hue": 0.3}}`), so switching
a shape between presets and back keeps each one's settings.

**Every output must draw the same picture**, because each window renders its
own copy. So a preset is a pure function of the canvas clock (`visual_t`), the
audio levels (`state.audio_bands`) and its parameters, with no frame-to-frame
state. Audio-driven *speed* is the exception that needs care: position is the
integral of speed, so the compositor integrates it each tick and publishes it
as `visual_phase` (with its current rate, `visual_rate`, for windows to
extrapolate between messages), the same way it publishes a video playhead.

**No mesh.** A visualiser is semi-transparent almost everywhere, and the warp
mesh's overlap skirts showed through it as a grid (blending twice, or with
`copy` compositing, as faint dark lines). Instead the shader inverts the
shape's corner-pin per output pixel and renders the shape already warped; the
page places it with one `drawImage` under the shape's usual mask clip,
opacity and filter. Each frame is rendered at the shape's on-screen size,
capped at 1280 px on its long side.

Presets: `starfield`, `clouds`, `turbulence`, `oil`, `ink`, `aurora`. All
are stateless by design — the ink drops, for instance, are timed events seeded
by drop index and cycle rather than a fluid simulation, which is what keeps
every output in step. Six on one 1080p output hold 30fps on the AMD iGPU.

**Adding a preset:** add its parameters to `PRESETS` (and, if speed should
follow audio, a branch in `phase_rate`) in `visuals.py`, and a
`vec4 shade(vec2 uv, float aspect)` in `PRESET_FS` in `visuals.js` returning
premultiplied colour (`emit()` turns emitted light into that).

## Media

### Linked media and media roots

A shape can reference a file where it already lives (`media_link`) instead of
a copy uploaded into `media/` (`media`). Linking is the default; a
multi-gigabyte video costs no extra disk.

**Media roots are a security boundary, not bookkeeping.** The server binds
`0.0.0.0` by default, so it's reachable from whatever network it's on. A
route that served arbitrary absolute paths would be a remote file read for
anyone who could reach the port. So:

- Only files under a folder you explicitly registered are served.
- Containment is checked after `realpath()`, so neither `../` nor a symlink
  planted inside a root can escape it.
- Only media file extensions resolve, so a broad root can't be turned into a
  general file read.
- Directory browsing enforces the same rule, and hides symlinked
  subdirectories that lead outside.

Register with `--media-root NAME=PATH` (persistent, repeatable) or from
Settings.

Links are stored as `root-name::relative/path`, never an absolute path. The
UI shows the resolved absolute path, but the stored form means a project
opened on another machine — the render host, whose media is on its own disk —
resolves as long as a root of that name exists there.

A link whose file is missing is **kept**, not dropped. An unplugged drive
mid-set must not let an unrelated edit silently destroy the link; it shows
as "file not found — Replace… to relink" instead. Only a link naming an
unregistered root is refused.

**Relink** repoints every shape using an asset at a different file, rewriting
saved canvases on disk — a relink that fixed 1 of 12 shapes would be worse
than none — and carries the trim across.

### Video playback

Position is derived from the canvas clock and published per layer by
`composite.py`, rather than letting each `<video>` free-run, the same way
effectors are pure functions of the canvas clock.

This is not tidiness. Every output window is a separate page with its own
elements; left alone they drift apart within seconds, so one clip spanning
two projectors through a viewport crop would show a different frame on each.
Clients converge on the published time, correcting only when drift exceeds
~250 ms — seeking a 30-minute file is expensive, so it's a nudge, not a
per-frame seek. Measured drift between two independent outputs: 1–5 ms.

Freeze and Stop shift the media clock by `dt` exactly like the effector
clock, so video halts with everything else.

An output window repaints on its own clock while video or webcam is on screen
(at the project frame rate), not only when a state message arrives, so the
picture isn't tied to the server's tick and network jitter. It converges on the
published playhead only on the first paint after a message: between messages
that playhead is stale, and converging on it would drag every video back.

**Trim** is per instance — `media_in`, `media_out`, `media_mode`,
`media_rate`, `media_offset` on the shape. `in`/`out` default to `null`,
meaning *inherit the asset's default trim* (`media_meta.py`), so re-trimming
a file in the Media tab moves every shape that hasn't overridden it.

Modes are `loop`, `once` (hide after) and `once_hold` (hold the last frame).
**There is no ping-pong**: browsers have no reverse playback, and faking it
with a backward seek per frame stutters badly on exactly the long files this
is built for.

The `<video>` cache is keyed by asset **plus playback config**, so shapes
trimmed identically share one decoder and stay frame-identical, while
differing trims get their own playhead — bounding decoder count to distinct
configurations rather than shape count. Orphaned elements (every trim edit
mints a new key) are evicted after ~3s unused.

Duration is measured by the browser and reported back, so there's no
`ffprobe` dependency and the server never opens media files.

## Sequencer

Plays saved canvases in order with per-step duration and optional crossfade,
looping or one-shot. Output windows follow whatever the sequencer is playing,
or the canvas open in the editor if it isn't running.

Crossfade dips through black rather than cross-dissolving: a true dissolve
would mean rendering both canvases simultaneously, which is a real
architectural change rather than a rendering tweak.

## Outputs

An Output is `output.html` in a chrome-less window. It's just another
websocket client watching the same broadcast the control page does, so there
is no second render implementation to keep in sync.

Per output (`OutputMonitorConfig`, stored on the project so it travels with
the show rather than living in one browser's storage):

- **flip x/y** — physical mount correction, reverses the finished frame
- **keystone h/v** — angled-throw correction
- **viewport** — a crop rect into the full canvas as `[0,1]` fractions, so
  one wide canvas can be sliced across several projectors. Off by default;
  when off every output shows the full canvas.
- **duplicate of** — mirror another output's crop while keeping independent
  flip/keystone
- **test pattern** — a keystone-aware alignment grid, drawn through the same
  transform as real content. Deliberately runtime-only, never saved.

A cropped output letterboxes against the *cropped slice's* aspect, not the
full canvas's — otherwise a 2048×768 canvas split in two stretches each half.

**Frame rate** is per project and paces the render loop *and* the state
broadcaster together. They must stay tied: a broadcaster slower than the
render loop silently caps what every output sees regardless of how fast
frames are produced.

## Multi-projector output

The per-output correction above is the laptop-scale version of a larger
design. `renderhost/` holds a Python/moderngl prototype of the full thing.

### Topologies

Two supported configurations, **same config format and same browser UI** —
they differ only in where the render process runs.

**A — laptop only (1–2 outputs).** Render process and server both local,
browser at `localhost:8080`. Small gigs, and the daily development setup.
This is what the app does today.

**B — laptop plus render host (3–5 outputs).** A headless machine drives the
projectors; the laptop renders *nothing* and is a control surface only.
Needed once projector count exceeds the laptop's display engines.

```
  ┌─────────────────┐        ┌──────────────────────────┐
  │  CONTROL LAPTOP │  Cat6  │  RENDER HOST (headless)  │
  │  browser UI     │◄──────►│  render process          │
  │  ssh            │ direct │  GPU ─┬─ HDMI1 → PJ 1    │
  └─────────────────┘  link  │       ├─ HDMI2 → PJ 2    │
                             │       ├─ DP1   → PJ 3    │
                             │       └─ DP2   → PJ 4    │
                             └──────────────────────────┘
```

**Topology A is never a debug mode.** The server binds a configurable
address (default `0.0.0.0`), the control surface connects to the page's own
origin by default, and no host address is hardcoded anywhere. If A breaks,
the development loop breaks with it.

Laptop caveats for A: USB-C DP Alt Mode is the same display engine on a
different connector and adds no heads; disable sleep and lid-close suspend
for a set; laptop GPUs throttle, so watch frame times over two hours rather
than peak performance; hybrid graphics can put the GL context on one GPU and
scanout on another.

### Why one host, not a render node per projector

An earlier design put one render node behind each projector and shipped
vector strokes over the network. That is right for a purely vector show —
strokes are tiny on the wire — and it was rejected here because the system
has to carry a live **raster** canvas. 7680×1024 uncompressed is ~1.4 GB/s,
which isn't shippable over ethernet without an encode stage whose latency
defeats the point.

Single host, single framebuffer, single GL context. This is why the app's
output model is *one canvas sliced by viewports* rather than independent
feeds — the viewport crop is the same concept the render host uses, just
executed in a browser instead of a GL pipeline.

### The correction chain

The scene renders once into an offscreen buffer; each output then samples
that buffer through its own transform.

```
  scene ──▶ FBO 7680×1024
                 ├── viewport 0 ─▶ crop₀ → homography₀ → mesh warp₀ ─▶ DP1
                 ├── viewport 1 ─▶ crop₁ → homography₁ → mesh warp₁ ─▶ DP2
                 └── …
```

**The FBO is mandatory, not an optimisation.** Warping requires *sampling*
the source; pixels already scanned out to a window can't be distorted.
Rendering direct-to-window forecloses the entire correction stage.

1. **Crop** — a sub-rect of the FBO. Overlapping crops give the source side
   of edge blending for free.
2. **Corner-pin homography** — keystone correction for flat surfaces.
3. **Mesh warp** — a grid of control points for curved or irregular
   surfaces. Not built; it needs a real curved surface to tune against.
4. **Edge blend** — gamma-corrected ramps **plus black-level lift** in
   overlap regions. The lift is routinely forgotten and is why untreated
   blends show grey bands.

The blend stage is always present and never conditionally compiled out: at
ramp width 0 and lift 0 it's an identity operation, no branch and no
measurable cost. A separate non-blending path would duplicate the config
format, correction chain, control protocol and alignment UI, and the two
would drift.

Two findings from building it, both easy to repeat in any language:

- **The naive way to split an overlap doubles it.** Padding each neighbour's
  crop by the *full* configured overlap rather than half makes every interior
  seam twice as wide as configured.
- **A blend ramp only helps where light actually overlaps.** On two ordinary
  abutting monitors it produces a visible *dark* band, because the ramp fades
  each edge expecting a second light source to fill it back in. Overlap
  should be 0 unless the throws genuinely overlap.

### Groups

A global spanned/independent flag can't express a mixed rig — three
projectors blended into one wall plus two standalone surfaces. The unit of
organisation is a **group**: one logical canvas, one scene source, one or
more outputs. Spanned is one group containing every output; independent is N
groups of one. There is no mode enum, which removes a special case rather
than adding one.

Blend ramps exist only at interior seams *within* a group, and are derived
from group membership in the config loader rather than authored per output —
hand-setting eight ramp values and getting one wrong is a classic load-in
bug.

**Overlap shrinks the logical canvas rather than extending it.** Three
1920-wide outputs with two 288px overlaps give 5760 − 576 = **5184** logical
pixels. Scenes must be authored against the reduced width or geometry is
subtly wrong across the whole group.

```
group_width = Σ output_widths − Σ interior_overlaps
```

Group membership is explicit config, never inferred from geometry: two
projectors that merely abut are not a blended pair.

### Profiles and shows

Two separately versioned artifacts that should not be merged:

- **Profile** — the rig. Outputs, EDID identities, groups, overlaps,
  corners, meshes, blend parameters. Slow-changing, expensive to recreate.
- **Show** — the content. Scene source per group and parameters; references
  a profile by id. Cheap and disposable.

This is the same split the app already makes between machine-level state
(media roots, in `settings.json`) and portable content (projects). It's also
why a linked media path stores a root *name* rather than an absolute path.

**Detected geometry is truth; the profile is intent.** The engine enumerates
RandR at startup and already knows what's connected, so the UI never asks how
many projectors exist — it shows what was detected and asks you to arrange
it. Outputs present in hardware but absent from the profile appear as
unassigned; outputs in the profile but missing from hardware are flagged
rather than silently dropped.

**Do not key calibration to connector names alone.** Store identity as name
*plus* EDID model/serial. Connector naming differs between machines, and
silently applying the wrong warp mesh to the wrong surface is the worst
available failure — it looks like a calibration bug rather than a mapping
bug. On a mismatch, present an explicit mapping step.

### Hardware constraints

Facts that have already cost investigation; don't design around them being
false.

- **Head count is a display-engine limit, not a port count.** No hub, dock,
  splitter or MST device increases it. NVIDIA consumer caps at 4; Polaris
  (RX 580) is rated for 5.
- **MST hubs do not add heads.** They multiplex one DP link into several
  sinks; each sink still consumes a display engine.
- **DisplayLink is disqualified** for any output path — USB compression with
  CPU-side encode. Latency and artefacts are incompatible with realtime
  visuals.
- **Passive DP→HDMI adapters only work on DP++ ports.** Verify port
  capability or buy active. This is the single most common failure point in
  these builds.
- **Passive HDMI copper is unreliable beyond ~15m.** Under 5m is safe, 5–10m
  fine for 1080p, beyond that use AOC fibre. The failure mode is sparkle or
  total dropout, not gradual degradation.
- **EDID/DDC fails before video does.** The symptom is a projector not being
  detected at all, plus the desktop reshuffling when one powers down. EDID
  emulators at the source fix both; budget for them on long runs.
- **DPMS and screensaver must be disabled** (`xset -dpms; xset s off`) or the
  show blanks mid-set.
- Within a blended group, use identical projector models where possible —
  differing lamp ages, colour temperatures and black levels show at the seam.

### Prototype status

Verified in `renderhost/` against real hardware (a laptop display plus one
external monitor — **not** a real multi-projector rig):

- The two-stage FBO render: scene once, each output crops and warps its own
  view independently
- Crop and overlap accounting, from both invented and RandR-detected geometry
- A true projective corner-pin homography per output, editable by hand or by
  dragging in a browser panel — both pixel-verified
- Gamma-corrected blend ramp plus black-level lift, independently toggleable
- The config format, round-tripped: bootstrap from detected hardware →
  validate → render, with `validate` catching four deliberately broken
  profiles
- A websocket control protocol, confirmed via actual pixel changes rather
  than message round-trips, using the same queued-mutation threading
  discipline as `composite.py`

Not built: mesh warp, KMSDRM or any headless path, the video layer,
simultaneous multi-group rendering, EDID fingerprinting, and everything
requiring the actual rig.

**Python-specific gotcha** if the render host stays Python:
`aiohttp.web.run_app()` installs a SIGINT handler, which only works on the
main thread — since the render loop owns the main thread there, `run_app()`
crashes immediately in a background thread. Use the lower-level
`AppRunner`/`TCPSite` API, which doesn't touch signal handling.

## Performance

Measured findings, and what they mean for running a show, are in
[ARCHITECTURE.md](ARCHITECTURE.md) §12 ("Measured on the laptop, 2026-11").
The short version for a single-output show on the dev laptop:

- **One output window.** Each output window does all of the video work for
  its own slice, so the same 3-projector canvas ran at 5fps per window with
  three windows and 16-17fps as one window showing the whole canvas. Several
  projectors are the render host's job (ARCHITECTURE.md §12, "Correction").
- **Video runs on the AMD iGPU, not the GTX 1650, in a Wayland session.**
  Chrome on the NVIDIA card through PRIME offload either can't show video at
  all (OpenGL/GLX: `<video>` white, canvas copies black) or loses GPU
  compositing (EGL, Vulkan). `run.py --open nvidia` therefore falls back to the
  default GPU under Wayland. Untested but likely to work: log in to "Ubuntu on
  Xorg", where `prime-select nvidia` makes the 1650 the primary GPU. Video-free
  content (visualisers) does render correctly on the 1650 under Wayland
  (`LIGHTSABER_FORCE_NVIDIA=1`). By hand, the NVIDIA launch is:

  ```bash
  __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia \
    google-chrome --ozone-platform=x11 \
    --user-data-dir="$HOME/.config/lightsaber-chrome" http://localhost:8080
  ```

  Check that a video is visible before trusting it. `--user-data-dir` is not
  optional: Chrome runs one process per profile, so
  with your everyday Chrome already open, launching without it only opens a
  window in that existing process — on the iGPU. `--ozone-platform=x11` is
  needed under Wayland, where Chrome otherwise ignores the PRIME variables.
  `prime-select nvidia` doesn't help on Wayland (verified 2026-11: set, and
  Chrome still got the iGPU). A `dri_gbm.so: Permission denied` line from
  Chrome's sandbox is harmless — confirmed on the 1650 with the sandbox on.

  The whole browser shares one GPU process, so the editor and every output
  window it opens move together. The **GPU** dot in the header shows which one
  each output window got: green for the dedicated card, amber for integrated,
  red for software rendering. Hover it for every window's paint rate.
- **Don't set the project fps above what the content needs.** 30 is plenty
  for 25fps footage. At 60, the iGPU with 12 filtered distorted layers
  collapsed to 9fps rather than settling at 30.
- **Turn off the editor's live preview** (the screen icon in the canvas
  toolbar) during a show. The editor is a full renderer with its own copy of
  every video; off, it draws outlines only.
- **Plug the laptop in** — on battery the CPU runs at its power-saving clock.
- The **Output Preview** tab is a real renderer per output. Its tiles exist
  only while the tab is open; leave it closed during a show.

The server-side diagnostics (Settings › Diagnostics, or `--diag`) time the
compositor's tick, which is now well under a millisecond for ten shapes. They
do **not** measure what an output window paints; tick "show output FPS" in
the Project tab for that.

## Troubleshooting

**An output window opens white / blank, with an empty console.** Check the
window's URL: if it says `about:blank`, no page ever loaded and nothing in
lightsaber is at fault. Chrome could not spawn a renderer process, which in
practice means the machine is out of memory. Each output window costs
420-770MB because it decodes every media layer independently — see
ARCHITECTURE.md's GPU-cost list, item 7, for the measurements and the
mitigations. Typically the first one or two windows open and later ones do
not.

**Output windows are black but the editor looks fine.** Look at `active` in
the state broadcast: media, webcam and text layers deliberately draw nothing
when the engine is stopped, so an output goes fully black on Stop. That is by
design, not a fault.

**The editor stops responding after a server restart.** It should reconnect on
its own within a second or two (`connectWS`). If it does not, the engine dot
in the header stays out — that is the only signal, so watch it.

**One shape vanishes but the rest of the frame is fine.** A layer that throws
while drawing is skipped and named in the console once (see `safeLayer` in
both renderers). Before that guard existed, one bad layer abandoned the whole
frame and the window simply went black.

**Video shapes draw black (or a `<video>` shows white) but everything else
is fine.** Check the GPU dot: on the NVIDIA card in a Wayland session, Chrome
can't display video. Use the default GPU (see Performance).

**Outputs all stutter at the same rate.** Count the output windows: each one
repeats every video's work, and they share one Chrome GPU process.

**Output windows are 1-indexed.** `?out=1` is the first output; `?out=0` is
falsy and silently falls back to output 1, which makes it easy to think you
are testing a window you are not.

## Relationship to promptwaver

Lightsaber is a fork of [promptwaver](https://github.com/edmonkey-nz/promptwaver),
which is still developed alongside it. Both are on the `upstream` remote:

```
origin    edmonkey-nz/lightsaber
upstream  edmonkey-nz/promptwaver
```

**They are not merged, and shouldn't be.** Promptwaver drives a vector
*laser*; lightsaber drives *projectors*. Until 2026-11 they still shared the
generated vector-scene engine and its Claude scene director. Lightsaber
removed that whole path (`scenes.py`, `scene3d.py`, `generators/`,
`director/`, `primitives.py`, `shapes.py`, `geometry.py`, `modulation.py`) to
become a video tool, so **there is no longer any shared engine code** —
nothing from promptwaver's scene pipeline, camera or director applies here.

| Diverged heavily | Lightsaber-only |
|---|---|
| `engine.py`, `web/server.py`, `composite.py`, both HTML renderers | `projects.py`, `sequencer.py`, `media_roots.py`, `media_meta.py`, `canvases.py`, `effectors.py`, `renderhost/` |

So changes flow **selectively, by hand, one feature at a time** — never as a
merge or a rebase. A merge would drag laser assumptions into the projector
path and vice versa.

### Porting a feature across

What can still come across is web/UI-side or server plumbing (say, a websocket
or reconnect fix), since the engines no longer overlap.

1. `diff -u lightsaber/<file> ../promptwaver/promptwaver/<file>` to see the
   whole delta, then decide which hunks are the feature and which are
   divergence. They are always mixed together.
2. Strip laser-specific parts. Promptwaver assumes an on/off beam and a
   few-hundred-stroke budget; lightsaber has neither constraint.
3. Reword ported comments and docstrings. "a dark laser" is "an empty frame";
   `print("[promptwaver] ...")` is `[lightsaber]`.
4. Check any new knob **defaults to the old behaviour**, so existing saved
   canvases are unaffected. Verify it, don't assume it.

### Ported so far

The camera `path` mode, `camera.wander` and the director's `massive` scene
size were ported before the scene engine was removed, and went with it.

## Files on disk

```
projects/<name>/
  project.json      # resolution, outputs, fps, notes
  canvases/*.json   # this project's canvases (+ .thumb.jpg)
  sequence.json     # this project's sequence
media/              # uploaded copies (linked media stays where it lives)
settings.json       # current project, media roots — gitignored
media_meta.json     # per-asset duration + default trim — gitignored
```

Projects, `settings.json` and `media_meta.json` are all gitignored: they're
either machine-local state or your own content, and `run.py` bootstraps an
empty default project on first run.
