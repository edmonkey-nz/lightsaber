# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# run (venv Python 3.12; always `python -m pip`, never bare `pip` —
# .venv/bin/pip has previously targeted a different interpreter)
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --web            # then http://localhost:8080
.venv/bin/python run.py --web --open default  # same, plus the editor in its own Chrome profile
```

Useful `run.py` flags: `--web-port`, `--fps` (startup compositor rate; a project's
own saved fps overrides it), `--media-root NAME=PATH` (repeatable; only files under
a registered root are ever served), `--diag`, `--host`.

Ruff and Black are configured in `pyproject.toml` (line-length 100, `py310`) but are
**not installed in the venv** — install them before claiming a lint pass.

**Regression suites live in `test-scripts/`** and drive the real app in a real
browser — there is no unit-test layer, because what breaks here are render paths.
Run them after any change to a draw path, the model, or the inspector:

```bash
.venv/bin/python test-scripts/run.py              # render, ui, output, perf, resilience; ~2 min
.venv/bin/python test-scripts/run.py --suite render
```

They build their own throwaway sandbox, so they never touch real projects. Failures
print an expectation and a hint naming where to look. There is no CI. Do not report a
change as verified on the strength of `node --check` or a Python import alone —
neither exercises a render path.

## Verifying a change

`node --check` on the extracted `<script>` blocks catches syntax errors only. It will
happily pass a listener bound to a deleted element, which throws at load and kills the
entire script. Actually run the app.

For anything the suites don't cover, drive it with Playwright against a **sandbox
copy**, never the user's live server — `harness.build_sandbox()` does this, and
`test-scripts/README.md` explains why it is not optional.

Three traps the harness already handles, each of which has cost real time here:

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

## Symptoms that are not what they look like

Three of these have each cost an hour. `docs/TECHNICAL.md`'s Troubleshooting
section has the full list; the ones worth knowing before you start digging:

- **An output window opens white with an empty console** — check its URL. If
  it is `about:blank`, no page loaded: Chrome could not spawn a renderer,
  i.e. the machine is out of memory. Each output window costs 420-770MB
  because it decodes every media layer independently. Measure free memory and
  swap before reading any code (ARCHITECTURE.md, GPU-cost item 7).
- **Outputs black, editor fine** — check `active` in the broadcast. Outputs go
  fully black on Stop by design.
- **"The outputs stopped"** may be the EDITOR having lost its socket, since it
  is the control surface driving them. It reconnects on its own now; the
  engine dot is the only signal.

## Architecture

**The compositor runs server-side; the browser draws.** `CompositeRenderer`
(`composite.py`) owns a render loop on its own thread and broadcasts, over one
websocket, a `state` (project/canvas document, authoritative shape geometry) plus a
`composite` payload (per-shape layer dicts: corners after effectors, compositing
settings, a video playhead). The server never touches pixels: video, images, webcams
and text are all decoded and drawn in the browser. `Engine` (`engine.py`) wires the
libraries into it. Canvas polygons — "shapes" — are the only thing that ever gets
drawn; there is no other render path.

**There are no vector scenes any more.** The promptwaver scene pipeline (generators,
3D camera, the Claude scene director, the scene library) was removed in 2026-11 —
lightsaber is a video tool. A saved shape with `source_type: "scene"` loads as an
empty media shape (`PolygonSpec.from_dict`). Don't reintroduce server-side rendering.

**Two renderers, synced by hand.** `web/static/index.html` (editor preview, canvas
`cv-edit`) and `web/static/output.html` (the real projector window, `/output?out=N`)
define **~40 identically-named functions** — `homography`, `drawMediaWarped`, `polyClipPath`, `applyPunch`, `applyMirrorFoldPost`… These are
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

**Output paint pacing.** An output window repaints the newest state on its own
rAF clock (capped at the project fps) while video or webcam is on screen, not only
when a message arrives, and converges on the server's playhead only on the first
paint after a message (`_paintIsFresh`). Don't put playhead sync back on every
paint: between messages the playhead is stale and would drag every video back.

**Visualisers are the one shared renderer file.** `web/static/visuals.js` is
loaded by both pages rather than duplicated (shaders are long). It renders a
"visual" shape already warped — no triangle mesh, because the mesh's overlap
skirts show as a grid through semi-transparent content — and each page places
it with one `drawImage`. Presets and parameter ranges live only in
`lightsaber/visuals.py`. See TECHNICAL.md's "Visualisers".

**Projects vs media.** Uploaded media and media roots are global and shared; a
project owns its canvas library, sequence, output resolution, output count and fps.

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

`projects/**` is deny-listed for Edit/Write and `settings.json` for Read (an old
install's copy may still hold an Anthropic API key from the removed scene director) in
`.claude/settings.local.json`. Both are gitignored, along with `media/` and
`sequence.json`. Commit only when asked, and don't push.

## Performance — measured 2026-11

Full findings are in `docs/ARCHITECTURE.md` §12, "Measured on the laptop, 2026-11".
What to know before touching performance:

- **The GTX 1650 can't show video in this Wayland session.** Chrome on it via
  PRIME offload draws `<video>` white and canvas copies black (OpenGL/GLX), or
  loses GPU compositing (EGL, Vulkan). Video work runs on the AMD iGPU. An
  earlier "1650 is 1.5-5x faster" finding was measured with the video black
  and is withdrawn. **Whenever measuring video, screenshot it.** An Xorg
  session (`prime-select nvidia` is already set) is the untested way out.
- **One output window per laptop.** Each window repeats all video work for its
  slice: a 3-projector canvas ran 5fps per window with three windows, 16-17fps
  as one window. Multi-projector is the render host's job.
- **RAM matters per output window**, not for one: each window is its own renderer
  with its own decoders (the white `about:blank` window).
- **The server is no longer a factor.** Building every layer is ~0.5ms a tick and
  one broadcast payload ~0.8ms, since the vector scenes went. Keep it that way:
  assume anything in the broadcast path runs at frame rate forever, competing for
  the GIL — build it once per tick, never per client.
- **A WebGL warp inside the Canvas2D pipeline was tried and was no faster.**
  Don't redo it; the real fix is the render host's single GL compositor.
- **Measuring video:** Playwright's bundled Chromium has no H.264 (an .mp4 layer
  silently draws black), so use `channel="chrome"`; the harness makes a VP8 WebM
  fixture instead. `getImageData` readback permanently halves an iGPU canvas's
  throughput, so for absolute numbers paint every rAF and count frames; readback
  timing is only good for same-run deltas.
- **If a paced thread ever starves**, promptwaver's proven fix was
  `sys.setswitchinterval(0.0005)` (36% missed → 0.0%). Real-time thread priority
  was noise — it doesn't beat the GIL.

**Before trusting any timing on the dev laptop,** check the CPU isn't capped:
compare `scaling_max_freq` with `cpuinfo_max_freq` under
`/sys/devices/system/cpu/cpu*/cpufreq/`. On 2026-09-16, 11 of 12 cores were
pinned at 2.1GHz of 4.06GHz; the fix does not survive a reboot. Read the real
clock from `/proc/cpuinfo` MHz, not `scaling_cur_freq`. Absolute numbers taken
before that date — `docs/ARCHITECTURE.md`'s "10 distorted 1080p layers across 4
outputs ≈113ms/frame" among them — may be up to 2x pessimistic for their
CPU-side share. `suite_perf.py`'s same-run deltas with a 3x factor are
unaffected as regression tripwires.
