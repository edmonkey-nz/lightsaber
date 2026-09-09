# Lightsaber — technical reference

Implementation detail behind [README.md](../README.md): how the renderer is put
together, the file formats, and the extension points.

- [Architecture](#architecture)
- [Module map](#module-map)
- [Scenes](#scenes)
  - [Scene spec format](#scene-spec-format)
  - [3D scenes and the camera](#3d-scenes-and-the-camera)
  - [Claude authors the geometry](#claude-authors-the-geometry)
  - [Adding a generator](#adding-a-generator)
- [Canvas and shapes](#canvas-and-shapes)
  - [Masks](#masks)
  - [Per-shape overrides](#per-shape-overrides)
- [Effectors](#effectors)
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
- [The scene director](#the-scene-director)
- [Performance](#performance)
- [Files on disk](#files-on-disk)

---

## Architecture

```
keyword ─▶ Scene director (Claude, ~1 call per scene, cached)
                 │  scene spec (JSON)
                 ▼
        Scene library  ── plain files on disk
                 │
     one live Scene per canvas shape, each with its own modulation matrix
                 │
                 ▼
       CompositeRenderer  ── own thread and clock
                 │  per-shape stroke lists + corners (JSON over websocket)
                 ▼
   Browser: homography-warp each shape into its quad, composite, mask, filter
     (the canvas editor, and every open Output window)
```

Two decisions shape everything else:

**There is no single "active scene".** Every shape is its own live `Scene`
with its own `ModMatrix` (routes are matrix-global, so simultaneous shapes
can't share one), all rendered by `CompositeRenderer` on one thread.

**The warp happens client-side.** The server ships raw per-shape stroke lists
plus each shape's current corners; the browser does the unit-square → quad
homography. The same payload drives the in-page editor and every Output
window, so what you see while editing is what the projector gets.

Mutations from the websocket never touch renderer state directly — they're
queued and drained at the top of each render tick, so the network thread
can't race the render thread.

## Module map

| Module | Role |
|---|---|
| `geometry.py` | `Path` (normalized polyline + colour) — the unit everything speaks |
| `modulation.py` | `LFO`, `Envelope`, `Value` sources and `ModMatrix` routing |
| `generators/` | `flow_field`, `attractor`, `ripples`, `world`, `forest`, `ground` |
| `scenes.py` | `SceneSpec`, live `Scene`, `SceneManager` (on-disk library) |
| `director/` | `SceneDirector` (Claude + cache) and the local `fallback` |
| `scene3d.py` | `Camera` + projection: near-clip, frame-clip, depth cueing |
| `primitives.py` | Ready-made low-poly kit (planet, ring, jellyfish…) |
| `shapes.py` | Shape-grammar interpreter for Claude-authored geometry |
| `canvases.py` | `PolygonSpec` / `CanvasSpec` / `CanvasManager` |
| `effectors.py` | Modulation matrix for canvas shapes — sources, routes, anchors |
| `sequencer.py` | `Step` / `Sequence` / `Sequencer` — canvas playback over time |
| `projects.py` | `ProjectSpec` / `ProjectManager` + `OutputMonitorConfig` |
| `media_roots.py` | Linked-media allowlist, path resolution, directory browsing |
| `media_meta.py` | Per-asset duration and default trim |
| `composite.py` | `CompositeRenderer` — the render loop, Start/Stop, playhead |
| `settings.py` | Local settings (API key, current project) — gitignored |
| `engine.py` | Thin coordinator wiring the above for the web server |
| `web/` | aiohttp server, control UI (`index.html`), output (`output.html`) |

## Scenes

### Scene spec format

```json
{
  "name": "water_flowing",
  "layers": [{"generator": "flow_field", "params": {"turbulence": 0.3, "speed": 0.18}}],
  "palette": ["#0a3d62", "#3c9dd0", "#c8f0ff"],
  "modulation": [
    {"source": "lfo_slow", "dest": "visual.speed",      "depth": 0.05},
    {"source": "lfo_mid",  "dest": "visual.turbulence", "depth": 0.4}
  ]
}
```

Generators are pure functions of `t`, which is what makes playback
reproducible: freeze, stop and sequencer scrubbing all work by manipulating
the clock rather than by pausing anything.

### 3D scenes and the camera

A scene is 3D when any layer uses a 3D generator (`ground_grid`, `forest`,
`world`). The world stays as 3D polylines; a slow-drifting `Camera` projects
them into the same 2D `Path`s everything downstream consumes.

Each shape draws only a few hundred strokes per frame (a browser-repaint
budget), so the camera's `far` plane culls distant geometry — and *is* the
fog — with per-object LOD dropping detail at distance.

```json
"camera": {
  "fov": 62, "near": 0.4, "far": 14.0, "speed": 0.6, "max_strokes": 90,
  "depth": {"mode": "hue", "near_color": [0.3,0.95,0.5], "far_color": [0.08,0.15,0.5]}
}
```

`camera.depth.mode`:

- `"hue"` — lerp `near_color` → `far_color` by distance
- `"cull"` — hard-drop past `far`, no colour change
- `"both"` — hue and cull

Fly-through speed is a modulation destination (`camera.speed`) and is also
exposed per-shape, so different shapes can move through the same scene at
different rates.

**Disable scene plane** is a per-shape override that hides floor/backdrop
geometry, applied in the `World` generator before the frame is built. It
matches the node's authored *name* — anything containing `floor`, `ground`,
`plane` or `grid`, with a guard so `plane` doesn't catch `planet`. The
scene-authoring prompt asks Claude to name backdrop shapes this way; older or
oddly-named scenes may have nothing that matches.

### Claude authors the geometry

The director isn't limited to a fixed vocabulary of objects. For a prompt
like *"inside a painter's studio"* it authors the geometry itself: a `defs`
block defines each object as line art built from a small shape grammar, and
nodes reference those defs. `shapes.py` is a general interpreter, not a
noun list.

Ops: `line`, `polyline` (raw escape hatch), `circle`, `rect`, `box`, `arc`,
`grid`, `lathe` (revolve a profile — jars, vases, lamps, planets). Adding an
op means adding a function in `shapes.py`.

```json
"layers": [{"generator": "world", "params": {
  "defs": {
    "easel": [{"op":"line","a":[0,2.4,0],"b":[-0.9,-2,0.7]},
              {"op":"line","a":[0,2.4,0],"b":[0.9,-2,0.7]}],
    "jar":   [{"op":"lathe","profile":[[0,0],[0.4,0.25],[0.3,0.7]],"meridians":4}]
  },
  "nodes": [
    {"shape":"easel","pos":[0,0.5,-1],"scale":1.2,"color":[0.95,0.8,0.5]},
    {"shape":"jar","pos":[4.5,0.2,-2],"scale":1.2,"color":[0.7,1.0,0.9]}
  ]
}}]
```

Nodes may reference an authored `shape` **or** a ready-made `primitive` — mix
freely. Geometry built from defs is cached per object (defs are static;
motion comes from the node transform). A full example ships in
`scenes/painters_studio.json`.

**Complexity budget.** Settings › Scene complexity › point budget is the
stroke-length ceiling sent with every generation, so scenes are authored
within a budget that stays smooth in the browser rather than guessed at.

### Adding a generator

Drop a file in `lightsaber/generators/`, subclass `Generator`, `@register`
it, and import it in `generators/__init__.py`:

```python
from ..geometry import Path, Frame
from .base import Generator, register

@register("lissajous")
class Lissajous(Generator):
    defaults = dict(a=3, b=2, speed=0.1, hue=0.5, points=300)

    def render(self, t, p) -> Frame:
        import numpy as np
        th = np.linspace(0, 2*np.pi, int(p["points"]))
        x = np.sin(p["a"]*th + t*p["speed"]); y = np.sin(p["b"]*th)
        pts = np.stack([x, y], axis=1) * 0.9
        return [Path(pts, (1, 1, 1))]
```

It's immediately available to the director and to the shape's Motion
controls.

## Canvas and shapes

A `CanvasSpec` is an ordered list of `PolygonSpec`. A shape is four corners
(clockwise from top-left, `+y` up) plus what fills it — `source_type` is one
of `scene`, `media`, `webcam`, `text`, or `knockout`.

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
Cost on hardware is ~0.04ms per feathered shape, flat in blur radius — it
does not need rationing. The buffer is also clipped to the shape's bounding
box, which is a large win only under software rasterisation and neutral on a
GPU; it is kept for the software case. See ARCHITECTURE.md's GPU-cost list,
items 4-5, including why a headless browser cannot measure any of this.

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

Everything below is canvas-local — it never modifies the underlying scene
file, so the same scene can look different in two shapes.

- **Colourize** — glow, trails, brightness, contrast, hue, saturation, and a
  colourize amount/hue pair.
- **Transform** — mirror x/y with adjustable fold points, pre- or
  post-distort, plus static rotate and scale about a chosen anchor.
- **Motion** — flow speed / turbulence / hue, for a 2D scene's first layer.
- **Shape camera** — mode, speed, orbit, fov, far, max strokes, disable
  plane. Mutually exclusive with Motion by the scene's own type.

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
`composite.py`, rather than letting each `<video>` free-run. Generated scenes
were already pure functions of `t`; video now matches.

This is not tidiness. Every output window is a separate page with its own
elements; left alone they drift apart within seconds, so one clip spanning
two projectors through a viewport crop would show a different frame on each.
Clients converge on the published time, correcting only when drift exceeds
~250 ms — seeking a 30-minute file is expensive, so it's a nudge, not a
per-frame seek. Measured drift between two independent outputs: 1–5 ms.

Freeze and Stop shift the media clock by `dt` exactly like every scene clock,
so video halts with everything else.

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

## The scene director

At most **one structured call per new keyword**, using a low-cost
(Haiku-class) model by default. Successful results are cached to
`scenes/generated/`; fallback scenes are never cached, so fixing a broken key
takes effect immediately. Between generations there is zero API traffic.

**Model and effort** are set in the Generate panel and persist. Model picks
the brain (Haiku fast/cheap, Sonnet better, Opus best); effort scales the
token budget (4k / 8k / 14k) and asks for a simpler or richer scene (≈5 vs
≈13 objects). Preset model IDs live in `MODEL_PRESETS`
(`director/claude_director.py`) and change over time — verify against
<https://docs.claude.com/en/docs/about-claude/models>. Override the ceiling
with `LIGHTSABER_MAX_TOKENS`.

**Prompt detail matters.** "swimming with jellyfish" leaves count, scale and
colour to the model's default guess. "exploring underwater with dozens of
large jellyfish, long pink trailing tentacles, shafts of light from above"
gives it concrete things to place and colour. The keyword field is multiline
for exactly this.

**The progress bar is a proxy, not an ETA.** The API doesn't know its final
response length in advance, so there's no true percentage — the bar compares
streamed output against the effort tier's token budget. Read it as "working,
this far into the budget".

The director line reports where the last scene came from: composed by Claude,
from cache, or local fallback with a reason. A status dot in the header
reflects live director state on every broadcast, so a key that stops working
mid-session is visible without re-testing.

**Key storage.** The key persists to a gitignored `settings.json` in
plaintext. That's a local-use convenience; move it to the OS keyring or an
env-only flow before any public deployment.

## Performance

Per-tick render timing and dropped-tick tracking, so "is this falling behind
its target frame rate" is a number rather than a guess. **Off by default** —
the instrumentation has a small real cost. Enable live in Settings ›
Diagnostics or launch with `--diag`; no relaunch needed either way.

## Relationship to promptwaver

Lightsaber is a fork of [promptwaver](https://github.com/edmonkey-nz/promptwaver),
which is still developed alongside it. Both are on the `upstream` remote:

```
origin    edmonkey-nz/lightsaber
upstream  edmonkey-nz/promptwaver
```

**They are not merged, and shouldn't be.** Promptwaver drives a vector
*laser*; lightsaber drives *projectors*. That single difference has pushed the
two apart everywhere output is concerned, while leaving the scene engine
largely shared. Roughly, by divergence:

| Shared, drifting slowly | Diverged heavily | Lightsaber-only |
|---|---|---|
| `scene3d.py`, `modulation.py`, `geometry.py`, `generators/`, `director/` | `engine.py`, `web/server.py`, `scenes.py` | `projects.py`, `sequencer.py`, `media_roots.py`, `media_meta.py`, `canvases.py`, `renderhost/` |

So changes flow **selectively, by hand, one feature at a time** — never as a
merge or a rebase. A merge would drag laser assumptions into the projector
path and vice versa.

### Porting a feature across

1. `diff -u lightsaber/<file> ../promptwaver/promptwaver/<file>` to see the
   whole delta, then decide which hunks are the feature and which are
   divergence. They are always mixed together.
2. Strip laser-specific parts. Promptwaver assumes an on/off beam and a
   few-hundred-stroke budget; lightsaber has neither constraint. `ttl_quantize`
   on `DepthCue` is the clearest example — it snaps colour channels to 0/1 for
   TTL RGB units and is meaningless here.
3. Reword ported comments and docstrings. "a dark laser" is "an empty frame";
   `print("[promptwaver] ...")` is `[lightsaber]`.
4. Check any new knob **defaults to the old behaviour**, so existing saved
   scenes are unaffected. Verify it, don't assume it: `camera.wander` defaults
   to 0 and was checked bit-identical against the previous drift before
   shipping.
5. Check whether the feature needs UI, prompt text, or a token budget on this
   side. `path` mode needed all three, and without the budget it would have
   truncated into a silent fallback.

### Ported so far

- **Camera `path` mode + `PathSpline`** — a closed, arc-length-parameterised
  Catmull-Rom circuit with `waypoints` / `look_at` / `lookahead`.
- **`camera.wander`** on drift.
- **The `massive` scene size** in the director, which specifies a route camera
  *and* route-shaped geometry together, plus its 32k token floor.

### Deliberately not ported

- **`DepthCue.ttl_quantize`** — laser-only.
- **Promptwaver's `aspect` projection fix.** It found its perspective divide
  had `* aspect` where it should be `/ aspect`. Lightsaber has the same line,
  but **never sets `Camera.aspect`** — `make_camera` doesn't pass it, so it
  stays 1.0, where both conventions are identical. Latent, not live. If camera
  aspect is ever wired through here, take promptwaver's version; output aspect
  is currently handled per-shape in the client instead (see *Outputs*).

## Files on disk

```
projects/<name>/
  project.json      # resolution, outputs, fps, notes
  canvases/*.json   # this project's canvases (+ .thumb.jpg)
  sequence.json     # this project's sequence
scenes/             # shared scene library
  generated/        # Claude results, cached
media/              # uploaded copies (linked media stays where it lives)
settings.json       # API key, current project, media roots — gitignored
media_meta.json     # per-asset duration + default trim — gitignored
```

Projects, `settings.json` and `media_meta.json` are all gitignored: they're
either machine-local state or your own content, and `run.py` bootstraps an
empty default project on first run.
