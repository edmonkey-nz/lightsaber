# Lightsaber

![version](https://img.shields.io/badge/version-0.33.0-33e0d0)
![status](https://img.shields.io/badge/status-pre--release-orange)
![platform](https://img.shields.io/badge/platform-Ubuntu-informational)

> **Pre-release, active development.** Version stays 0.x until things settle;
> scene/canvas JSON shape and internal APIs may still change between minor
> versions. See [CHANGELOG.md](CHANGELOG.md) for what's landed.

A realtime **live canvas/shape visual instrument** for browser-driven monitor
and projector output — a procedural 3D scene sculptor whose output is
composited from scene-filled shapes on a canvas, rendered straight in the
browser (the control page, and any number of chrome-less **Output** windows
dragged onto real screens/projectors).

Claude acts as an offline **scene director**: prompting it for a 3D scene
("water flowing", "aurora over a still lake") becomes a scene spec, which
renders locally at full framerate with no further API calls. That keeps it
cheap enough to run for hours — the network is touched only when a new scene
is created. Scenes are saved locally as JSON, and dropped onto canvas shapes
to build a look.

Note: You'll need a paid Claude API account if you want to generate any
scenes; scenes cost ~5-40 cents (NZD) each, depending on size, detail and
model used. You can buy a $5 credit which should last a while (unless you use
Sonnet/Opus and make big scenes).

Lightsaber is a visuals-only fork of [PromptWaver](https://github.com/edmonkey-nz/promptwaver),
which pairs the same visual engine with a polyphonic ambient synth. This fork
dropped the audio layer, and later the physical laser/DAC output it
originally targeted, to focus on browser/projector-driven visuals — see
`ARCHITECTURE.md` for where multi-projector output is headed.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # numpy + aiohttp (required)
pip install anthropic                    # optional: Claude scene director
```

## Run

```bash
# browser control surface, offline (local) director
python run.py --web

# use Claude as the scene director
ANTHROPIC_API_KEY=sk-... python run.py --web

# register a folder that linked media may be referenced from (repeatable,
# and persistent — a one-off per folder, also editable in Settings)
python run.py --web --media-root videos=/home/you/Videos
```

Open <http://localhost:8080>. You'll land in a default project with an empty
canvas holding one square shape:

1. **Generate** a scene from a keyword (Input > Scene library), or pick one
   from the library.
2. Drop it onto a shape — click a scene-library row while a shape is
   selected (or use its **+**) to assign it, or **+ Add shape** to create a
   new one with it.
3. Drag the shape's corners to distort it; adjust Shape settings (opacity,
   mask), Monitor filters, and Motion/Shape camera on the right.
4. Open **Output 1** (header) and drag that chrome-less window onto a second
   screen or projector — that's the actual show output.

## Architecture

```
keyword ─▶ Scene director (Claude, async, ~1 call/scene, cached)
                 │  scene spec (JSON)
                 ▼
        Scene library  ── save · load (no "current scene" — see below)
                 │
     one live Scene per canvas shape, each its own modulation matrix
     (LFOs · envelopes · routes)
                 │
                 ▼
       CompositeRenderer  ── per-shape render, own thread/clock
                 │  per-shape stroke lists + corners (JSON over websocket)
                 ▼
   Browser: homography-warp each shape into its quad, composite, mask
     (control page's canvas editor, and any open Output window)
```

Projects (`projects.py`) are the base-level container: each owns its own
canvas library and sequencer document. A canvas (`canvases.py`) is an
ordered list of shapes (polygons) — each a 4-corner quad you can drag-warp,
holding a scene or media reference, opacity, an optional mask, and sparse
per-shape overrides (glow/trail/mirror/motion/camera). The sequencer
(`sequencer.py`) plays saved canvases back in order with per-step duration —
that's the actual "monitor output" a projector watches.

There is deliberately no single "active scene" any more: every shape is its
own live `Scene` with its own modulation matrix (routes are matrix-global,
so simultaneous shapes can't share one), rendered by `CompositeRenderer` on
its own thread. The warp (unit square → shape's quad) happens client-side —
the server ships raw per-shape stroke lists plus each shape's current
corners, and the browser does the homography, both in the in-page canvas
editor and in each Output window.

Module map:

- `lightsaber/geometry.py` — `Path` (normalized polyline + colour), the unit everything speaks.
- `lightsaber/modulation.py` — sources (LFO, ADSR `Envelope`, `Value`) + `ModMatrix` routing.
- `lightsaber/generators/` — `flow_field`, `attractor`, `ripples`, `world`, `forest`, `ground`; `@register` to add more.
- `lightsaber/scenes.py` — `SceneSpec`, live `Scene`, `SceneManager` (a pure on-disk library).
- `lightsaber/director/` — `SceneDirector` (Claude + cache) and the local `fallback`.
- `lightsaber/scene3d.py` — `Camera` + projection (near-clip, frame-clip, depth cueing) for 3D scenes.
- `lightsaber/primitives.py` — ready-made low-poly primitive kit (planet, ring, jellyfish…).
- `lightsaber/shapes.py` — the shape-grammar interpreter that expands Claude-authored geometry `defs`.
- `lightsaber/canvases.py` — `PolygonSpec`/`CanvasSpec`/`CanvasManager` — shapes and their canvas.
- `lightsaber/sequencer.py` — `Step`/`Sequence`/`Sequencer` — canvas playback over time.
- `lightsaber/projects.py` — `ProjectSpec`/`ProjectManager` — the base-level container.
- `lightsaber/composite.py` — `CompositeRenderer` — the render loop; also owns Start/Stop and perf diagnostics.
- `lightsaber/settings.py` — local settings store (API key, current project), gitignored.
- `lightsaber/engine.py` — thin coordinator wiring the above together for the web server.
- `lightsaber/web/` — `aiohttp` server + control UI (`index.html`) + output window (`output.html`).

## 3D immersive scenes

A scene is 3D when any of its layers uses a 3D generator (`ground_grid`,
`forest`, `world`). The world stays as 3D polylines; a slow-drifting `Camera`
projects them to the same 2D `Path`s everything downstream speaks. Fly-through
speed is a modulation destination (`camera.speed`), so an LFO can steer your
drift, and it's also exposed per-shape in the **Shape camera** group.

Because each shape only draws a few hundred strokes per frame (a browser-
repaint budget), the camera's `far` plane culls distant geometry (and *is*
the fog), with per-object LOD dropping detail strokes in the distance.
Configure via the scene's `camera` block:

```json
"camera": {
  "fov": 62, "near": 0.4, "far": 14.0, "speed": 0.6, "max_strokes": 90,
  "depth": {"mode": "hue", "near_color": [0.3,0.95,0.5], "far_color": [0.08,0.15,0.5]}
}
```

Depth is shown with **colour**, via `camera.depth.mode`:

- `"hue"` — lerp `near_color` → `far_color` by distance.
- `"cull"` — hard-drop anything past `far` (pure depth culling, no colour change).
- `"both"` — hue *and* cull.

Try a keyword like *"float through a forest"* to build one.

### Disable scene plane

A per-shape override (Shape camera group) that hides a scene's
floor/ground/backdrop geometry — applied in the `World` generator before the
frame is even built. It matches on the node's authored *name*, not its
appearance: anything with `floor`, `ground`, `plane`, or `grid` in its name
(e.g. `floor`, `cave_floor`, `ocean_grid`) — with a deliberate guard so
`plane` doesn't also catch `planet` (a common primitive). Claude's
scene-authoring prompt asks it to name floor/backdrop shapes this way, so new
generations reliably support the toggle; older or oddly-named scenes may not
have anything that matches.

## Claude authors the geometry (shape grammar)

The director isn't limited to a fixed bucket of objects. For a prompt like
*"inside a painter's studio"* Claude **authors the geometry itself** and stores
it in the scene JSON: a `defs` block defines each object as line-art built from a
small, open-ended **shape grammar**, and nodes reference those defs. The app is a
general interpreter (`shapes.py`), not a noun-list — so the vocabulary of scenes
is unbounded and nothing is baked into the local tool.

Shape ops: `line`, `polyline` (raw escape hatch), `circle`, `rect`, `box`, `arc`,
`grid`, `lathe` (revolve a profile — jars, vases, lamps, planets). Add an op =
add a function in `shapes.py`.

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
freely. Built geometry from defs is cached per object (defs are static; motion is
applied via the node transform). This costs more tokens per generation than the
old fixed-primitive approach, but every scene is genuinely composed to the
prompt. Try *"inside a painter's studio"* (a full defs example ships in
`scenes/painters_studio.json`).

## Scene complexity budget

**Settings > Scene complexity > point budget** is the stroke-length ceiling
sent to Claude with every generation, so scenes are authored (object count,
stroke density) within a budget that stays smooth to render in the browser,
rather than guessed. Persists in `settings.json`.

## Canvas, shapes, and per-shape overrides

The **Canvas** panel (column 2) is the editor: pick/create/save/delete a
canvas, set its target resolution (plain width/height in pixels — the doc
`ARCHITECTURE.md` treats this as authored/derived output geometry, not a
picked-from-a-list mode), and drag shapes around on it. **+ Add shape**
creates a new 4-corner quad; drag its corners to distort it; **Shape
settings** assigns a scene (or, for later, a media file — storage/labelling
only right now) and opacity, plus an optional **mask** (circle / hexagon /
triangle / square with a 0-50px feathered edge).

Selecting a shape enables three groups in column 3, each editing that
shape's own canvas-local overrides (never the underlying scene file):

- **Monitor filters** — glow, trails, mirror x/y. Pure client-side rendering
  effects.
- **Motion** — flow speed / turbulence / hue, for a 2D scene's first layer.
- **Shape camera** — mode/speed/orbit/fov/far/max-strokes/disable-plane, for
  a 3D scene's camera. Mutually exclusive with Motion by the shape's own
  scene type.

## Sequencer

The **Sequencer** pane (column 3) plays saved canvases back in order — drag
to reorder, set each step's duration, ▶/⏸ transport, loop toggle. This is
the actual monitor/projector output: Output windows render whatever canvas
is currently playing (or the one open in the editor, if the sequencer isn't
running).

## Media

The **Media** tab is every file the project can draw from. Two kinds:

- **Linked** (🔗) — referenced where the file already lives. Nothing is
  copied, so a multi-gigabyte video costs no extra disk. This is the default.
- **Uploaded** (📁) — a copy inside the app's own `media/`. Fine for small
  logos and stings.

Linked files must sit under a registered **media root**. That's a security
boundary, not just bookkeeping: the server binds `0.0.0.0` by default, so
only files underneath a root you registered are ever served — keep roots as
narrow as you can. Links are stored as `root-name::relative/path`, so a
project opened on another machine resolves as long as a root of the same
name exists there.

**Trimming.** Set a file's default in/out in the Media tab — useful when a
30-minute recording is only interesting for 30 seconds of it. Every shape
using that file inherits the default; any shape can override it with its own
in/out, playback mode (loop / once / once-and-hold), speed and start offset.
The same clip can appear several times on a canvas, each with its own trim
and playhead.

Video position is derived from the canvas clock rather than left to each
`<video>` element, so every output window shows the same frame — which is
what lets one clip span two projectors through a viewport crop. Freeze and
Stop halt video along with everything else.

## Projects

A **Project** (column 2, above Canvas) owns its own canvas library and
sequencer document — scenes and uploaded media stay shared across every
project. Use it to keep different shows/venues cleanly separated;
**Save as…** duplicates a whole project (canvases, sequence, and all) under
a new name.

## Performance diagnostics

Per-tick render timing and dropped-tick tracking for the compositor's render
loop — so "is this falling behind its target FPS" is something you can read
off real numbers instead of guessing. Lives in a **Performance** accordion
(sidebar).

**Off by default** — the timing instrumentation itself has a small real cost.
Turn it on live in **Settings > Diagnostics**, or launch with `--diag`; no
relaunch needed either way. When off, the Performance panel hides entirely
rather than sitting open empty.

## Connecting the API key (in-app)

Open the app and use the **Connection** panel: paste your key, **Save key**
(persists to a gitignored `settings.json` and exports it to the process), then
**Test connection** for a pass/fail using one tiny call. The `director` readout
flips to your model id when it's live. This is a convenience for local use — for
a public release, move the key to the OS keyring or an env-only flow.

A status dot next to **Connection** reflects the director's actual live state
(green "connected" / grey "offline") on every state broadcast — not just
right after you click Test — so a key that stops working mid-session (package
missing, network down, key revoked) is visible without re-testing.

## Adding a scene generator

Drop a file in `lightsaber/generators/`, subclass `Generator`, `@register` it,
and import it in `generators/__init__.py`:

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

It's now available to the director and the shape's Motion sliders.

## Scene spec format

```json
{
  "name": "water_flowing",
  "layers": [{"generator": "flow_field", "params": {"turbulence": 0.3, "speed": 0.18}}],
  "palette": ["#0a3d62", "#3c9dd0", "#c8f0ff"],
  "modulation": [
    {"source": "lfo_slow", "dest": "visual.speed",       "depth": 0.05},
    {"source": "lfo_mid",  "dest": "visual.turbulence",  "depth": 0.4}
  ]
}
```

## Cost control

The director makes at most **one structured call per new keyword** using a
low-cost model (Haiku-class) by default, and caches **successful Claude results**
to `scenes/generated/` (fallback scenes are never cached, so fixing your key
takes effect immediately). Between scene changes there is zero API traffic.

**Model & effort** are set in the UI (Generate panel) and persist in
`settings.json`. Model chooses the brain — Haiku (fast/cheap), Sonnet (better),
Opus (best); effort chooses how hard it works — low / medium / high scales the
token budget (4k / 8k / 14k) and asks for a simpler or richer scene (≈5 vs ≈13
objects). Bigger model + higher effort = better environments, more tokens. The
preset model IDs (`MODEL_PRESETS` in `director/claude_director.py`) change over
time — verify at <https://docs.claude.com/en/docs/about-claude/models>.

Every generation is **added to the scene library** and dropped straight onto
the canvas as a new shape. Override the response ceiling with
`LIGHTSABER_MAX_TOKENS`.

**Naming**: give a scene an explicit name in the Generate panel and it's used as
the library title; leave it blank and Claude's own name (or the keyword) is used.

**Prompt detail**: a semi-detailed prompt beats a bare keyword. "swimming with
jellyfish" leaves count, scale, and colour to the model's default guess;
"exploring underwater with dozens of large jellyfish, long pink trailing
tentacles, shafts of light from above" gives Claude concrete things to place and
colour, so the composition is closer to what you pictured. The keyword field is
multiline for exactly this — write a sentence or two, not just a noun.

**Progress bar**: the Claude API has no notion of overall completion — it
doesn't know the final response length in advance, so there's no true
percentage. The bar is a proxy: a streaming call reports output as it's
generated, compared against the effort tier's token budget. It climbs steadily
during generation and lands on 100% at completion; treat it as "working, this
far into the budget" rather than an exact ETA.

The UI's director line reports the source of the last scene: *composed by Claude*,
*from cache*, or *local fallback* (with the reason).

## Roadmap

- **Multi-projector output** — see `ARCHITECTURE.md` for the design, and
  `renderhost/` for a prototype (crop/homography/edge-blend, RandR display
  detection, a config-file format, and a web control panel — all verified
  against real hardware, see `ARCHITECTURE.md` §12 for exactly what that
  does and doesn't prove). Separate from the browser app in this repo; the
  data model here (projects/canvases/shapes) is meant to grow toward it
  without a rewrite, not be replaced by it.
- **Media shapes**: uploaded video/image files can be assigned to a shape
  and play, warped into its quad the same way scenes do. Text-input and
  webcam shapes are still stubbed the same way media used to be.
- **More visual effects**: additional generators, post-processing, richer
  colour/motion treatments.
- **More shape ops**: revolve-with-caps, sweep-along-path, mirror/array helpers,
  bezier — widen what Claude can author cheaply.
- **Node-list readout in the UI**: show (and hand-tweak) what Claude placed.
- **Waterfall / cave / canyon** environments; richer camera paths (banking, look-at).
- **Canvas-to-canvas crossfade at sequencer step boundaries** — a new
  compositor-level feature (cross-dissolve two canvases' layers), not a
  revival of the old single-scene crossfade.
- **Per-shape modulation route editing** in the UI — each shape already owns
  its own `ModMatrix`; there's no control surface for it yet.
- **Key storage**: move `settings.json` key to OS keyring before public release.

## Development

Opens straight into VSCode: `.vscode/settings.json` points the Python
interpreter at `.venv`, and `.vscode/launch.json` has F5-ready configs.
`.vscode/extensions.json` recommends Pylance and Ruff; `pyproject.toml` holds
Ruff/Black config (line length 100).

See [CHANGELOG.md](CHANGELOG.md) for the version history inherited from
PromptWaver up to the fork point.

MIT — see [LICENSE](LICENSE).
