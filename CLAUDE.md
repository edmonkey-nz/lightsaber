# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# run (venv Python 3.12; always `python -m pip`, never bare `pip` —
# .venv/bin/pip has previously targeted a different interpreter)
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --web            # then http://localhost:8080
```

Useful `run.py` flags: `--web-port`, `--fps` (startup compositor rate; a project's
own saved fps overrides it), `--media-root NAME=PATH` (repeatable; only files under
a registered root are ever served), `--diag`, `--model`.

Ruff and Black are configured in `pyproject.toml` (line-length 100, `py310`) but are
**not installed in the venv** — install them before claiming a lint pass.

**There is no test suite.** No pytest, no test files, no CI. Changes are verified by
driving the running app (see below). Do not report a change as verified on the
strength of `node --check` or a Python import alone — neither exercises a render path.

## Verifying a change

`node --check` on the extracted `<script>` blocks catches syntax errors only. It will
happily pass a listener bound to a deleted element, which throws at load and kills the
entire script. Actually run the app.

Drive it with Playwright against a **sandbox copy**, never the user's live server:
`run.py` resolves `scenes/`, `projects/`, `media/` and `settings.json` relative to the
repo root, so copying `lightsaber/` + `run.py` into a scratch dir with empty
`projects/ scenes/ media/` and a `{}` settings.json gives full isolation on another port.

Three traps, each of which has cost real time here:

- **The server is long-lived and holds canvas state in memory.** Successive test runs
  accumulate shapes on the same canvas, and stale shapes silently corrupt pixel
  measurements. Delete every polygon at the start of each run and confirm the count
  reached zero.
- **Headless Chromium is always SwiftShader**, even with `--ignore-gpu-blocklist
  --enable-gpu-rasterization`. Only `headless=False` on the real display gets the GPU.
  Any performance number taken headless is software rendering and can be off by ~12×.
  Confirm with `WEBGL_debug_renderer_info` → `UNMASKED_RENDERER_WEBGL`.
- **Python changes need a server restart.** A stale process presents as a confusing UI
  bug, not an error.

## Architecture

**The compositor runs server-side; the browser draws.** `CompositeRenderer`
(`composite.py`) owns a render loop on its own thread and broadcasts, over one
websocket, a `state` (project/canvas document, authoritative shape geometry) plus a
`composite` payload (per-shape layer dicts carrying strokes and effector-resolved live
values). `Engine` (`engine.py`) wires the libraries into it. Canvas polygons — "shapes" —
are the only thing that ever gets drawn; there is no other render path.

**Two renderers, synced by hand.** `web/static/index.html` (editor preview, canvas
`cv-edit`) and `web/static/output.html` (the real projector window, `/output?out=N`)
define **~45 identically-named functions** — `homography`, `drawWarpedStrokes`,
`drawMediaWarped`, `polyClipPath`, `applyPunch`, `applyMirrorFoldPost`… These are
duplicated source, not shared imports. **Any change to a draw path must be made in
both files**, and their comments cross-reference each other deliberately. They differ
in coordinate mapping only: the editor works in artboard pixels (`normToPx` +
`_viewToDevice`, with a zoom/pan view transform) while output maps straight to window
pixels (`mapWin`, with viewport crop, keystone and flip folded in).

**Adding a per-shape field follows a fixed chain** — miss a link and the value silently
never reaches the renderer:

1. `canvases.py` — `PolygonSpec` dataclass field **and** `from_dict` (clamp there)
2. `composite.py` — `set_polygon`'s key dispatch, **and** `_poly_layer_base` (the one
   place that builds the broadcast layer payload)
3. both HTML renderers
4. `index.html` — inspector markup, `renderPolyInspector` wiring, and an `oninput`/
   `onchange` handler that `send`s a `poly_set`

New knobs must default to the old behaviour, so saved canvases render unchanged.

**Coordinates and paint order.** Shape corners are normalized `[-1,1]`, `+y` up,
clockwise from top-left. `z_index` runs 1 (frontmost) to 10 (backmost) and layers are
painted in **descending** z — back to front. Chrome drawn inside the editor's view
transform must divide by `_view.zoom` to stay a screen-space measurement.

**Compositing discipline.** Content draws at full opacity into a scratch buffer and is
blitted once; per-primitive alpha double-blends where the warp mesh's overlap skirts
meet, which shows as a visible grid at fractional opacity. Side buffers (punch runs,
feather) follow the same rule.

**Scene pipeline.** `director/` (Claude, or `fallback.py` keyword mapping with no API
key) emits a `SceneSpec` → `generators/` + `primitives.py` build 3D polylines →
`scene3d.py` flies a camera and projects to normalized 2D paths → the compositor
broadcasts them as strokes. Add a primitive with a `@register` function in
`primitives.py` and it is available to both the director and the fallback.

**Projects vs scenes.** Scenes are global and shared; a project owns its canvas library,
sequence, output resolution and output count.

**`about.md` is read at runtime** by the `/about` route, resolved relative to the repo
root — it is app content, not a developer doc, which is why it stayed at root when the
others moved into `docs/`. Moving it means editing `web/server.py`.

## Two deployment topologies, and the render host

`docs/ARCHITECTURE.md` §1 defines two configurations that share the same binary, config
format and browser UI, differing only in where pixels are produced:

- **A — laptop only (1-2 outputs).** Render process and aiohttp server both local, one
  browser window per output. **This is what ships today and the daily dev loop**, so it
  has to stay a first-class path, not a debug mode.
- **B — laptop + headless render host (3-5 outputs)** over a direct Cat6 link. The
  laptop renders *nothing* and is a control surface only.

The argument for B is not warp quality, it is **decode and warp multiplicity**. In
topology B content resolves into a shared FBO *before* the per-output stage, so a clip
spanning three projectors is decoded once and read three times as three crops of one
texture. Topology A cannot do that — each output is a separate browser page with its own
`<video>` elements, so N clips on M outputs costs N×M decoders and N×M mesh warps for
one picture's worth of image (measured: 10 distorted 1080p layers across 4 outputs
≈113ms/frame, about 2fps). Anything that reintroduces per-output *content* work gives
that property up, so it belongs in the per-output correction chain, not upstream of the
FBO.

`renderhost/` is a **prototype toward that design, not the production host** — four demo
scripts over a shared `correction_chain.py` (homography, crop, blend ramp, lift; pure
math, no GL calls of its own), plus `profile.py` for the §6 config format and
`displays.py` for real display enumeration. It has only ever run against a laptop
display plus one external monitor; `renderhost/README.md` states what is real vs
deferred, and `docs/ARCHITECTURE.md` §12 what that does and doesn't prove. Its source
comments cite the design by section number (`§5`, `§6`) — keep that convention.

`docs/ARCHITECTURE.md` and `renderhost/` are **lightsaber-only**: promptwaver has no
equivalent, so there is no upstream prior art here and nothing to port back.

## Documentation map

- **docs/ARCHITECTURE.md** — render-host design and **measured** GPU/performance findings,
  including several counter-intuitive results (full-surface ops are cheap; the wall is
  draw-call count, not fill rate; bbox region-bounding has now failed to pay twice).
  Read the relevant numbered item before hand-optimising anything on the Canvas2D path.
- **docs/TECHNICAL.md** — working reference for the data model, masks/cutouts, effectors,
  and the promptwaver relationship. `docs/PROMPT-effectors.md` is the effector
  system's design brief, cited by name from `effectors.py` and `composite.py`.
- **docs/future.md** — numbered backlog. Code comments cite these numbers (`(42)`, `(8)`);
  keep that convention.
- **CHANGELOG.md** — pre-1.0, entries under `[Unreleased]`.

## Conventions

Comments here explain **why**, not what — the tradeoff considered, the bug the line
prevents, the thing measured. Match that density; it is unusually high and deliberate.

Lightsaber is a hand-ported fork of the sibling project **promptwaver** (vector laser
rather than projectors), present as the `upstream` remote. **Never merge or rebase from
it** — port one feature at a time by hand. Procedure and divergence map are in
docs/TECHNICAL.md's "Relationship to promptwaver".

`projects/**` is deny-listed for Edit/Write and `settings.json` for Read (it holds the
API key) in `.claude/settings.local.json`. Both are gitignored, along with `media/`,
`scenes/generated/` and `sequence.json`. Commit only when asked, and don't push.
