# Render host prototype

Four scripts toward [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md)'s render
host — a prototype toward that architecture, not the architecture itself.
See [What's real vs. deferred](#whats-real-vs-deferred) before assuming any
other part of the doc is implemented.

| Script | Shows | Layout source |
|---|---|---|
| `demo_blend.py` | Edge blending itself — can't see it any other way without real overlapping projectors | Invented (`--outputs`/`--overlap` args) |
| `demo_real_displays.py` | Real display detection driving real window geometry | `SDL_GetDisplayBounds` (RandR-backed under X11) |
| `demo_profile.py` | Rendering from a **saved config file**, not live args/detection | `profile.py` — a JSON file matching ARCHITECTURE.md §6's shape |
| `serve.py` | The above, but **tunable live from a browser**, with save-to-file | Same as `demo_profile.py`, mutated over a websocket |

All four share `correction_chain.py` (homography, crop, blend-ramp, lift —
all GLSL/math, no GL calls of its own), so the correction chain itself is
identical everywhere — only how outputs get their position/size/blend
params differs.

**Setting up the actual render-host hardware** (topology B, §2) is a
separate, mostly-manual process from running the prototype here — see
`setup-render-host.sh` in this directory (install steps, GL/display
verification, and templates for the network/systemd config, generated for
review rather than applied automatically). **Untested on real render-host
hardware** — everything in this directory has only run against a laptop's
own display plus one external monitor so far; see ARCHITECTURE.md §12 for
exactly what that does and doesn't prove.

## Run it

```bash
cd lightsaber   # repo root

# Simulated blending — see edge blending itself, invented layout
.venv/bin/python3 -m renderhost.demo_blend
.venv/bin/python3 -m renderhost.demo_blend --outputs 4 --overlap 0.2
.venv/bin/python3 -m renderhost.demo_blend --screenshot /tmp/blend.png

# Real display detection driving real window geometry
.venv/bin/python3 -m renderhost.displays                            # just print what SDL/RandR detects
.venv/bin/python3 -m renderhost.demo_real_displays                  # borderless dev window
.venv/bin/python3 -m renderhost.demo_real_displays --fullscreen     # venue-accurate real fullscreen
.venv/bin/python3 -m renderhost.demo_real_displays --overlap 0.15 --screenshot /tmp/real.png

# Config file: bootstrap from what's detected right now, validate, render from the FILE
.venv/bin/python3 -m renderhost.profile from-real /tmp/rig.json --overlap 200 --gamma 2.2 --lift 0.02
.venv/bin/python3 -m renderhost.profile show /tmp/rig.json
.venv/bin/python3 -m renderhost.profile validate /tmp/rig.json
.venv/bin/python3 -m renderhost.demo_profile /tmp/rig.json --group main
.venv/bin/python3 -m renderhost.demo_profile /tmp/rig.json --group main --screenshot /tmp/out.png

# Web control panel — same render, but tunable live from a browser + Save
.venv/bin/python3 -m renderhost.serve /tmp/rig.json --group main
# then open http://localhost:8090
```

All scripts with a `__main__` also run as plain files (`cd renderhost &&
python3 demo_blend.py ...`), not just via `-m` — a `__package__` check
falls back to an absolute import if run that way. This was a real bug hit
in practice (`ImportError: attempted relative import with no known parent
package`), not a speculative nicety.

Needs `moderngl` + `PySDL2` (+ `Pillow` for `--screenshot`, `aiohttp` for
`serve.py` — already a lightsaber dependency) — see `requirements.txt` in
this directory. Confirmed working here against a real GL 4.6 context
(AMD/Mesa) and real detected displays.

**Note on this repo's venv:** `.venv/bin/python3` and `.venv/bin/pip` have
been observed pointing at different Python versions in this same venv
before (3.12 vs a stray 3.14). If `import moderngl`/`sdl2` fails after a
`pip install`, install with `.venv/bin/python3 -m pip install ...`
explicitly rather than bare `pip install`.

## `demo_blend.py` — seeing edge blending

One window, split into 3 (or `--outputs N`) simulated adjacent projector
viewports with a configurable overlap. A test pattern (color bars + grid on
top, a flat near-black field on the bottom) renders once into a shared FBO;
every output samples it through the correction chain.

This is a deliberate simulation trick: real monitors/projectors never
overlap in desktop coordinate space, only their physical light does once
aimed at a shared surface — so this script draws overlapping viewports into
one window with **additive blending** to fake that physical light-summing,
purely so blending is visible on a single laptop screen with nothing else
plugged in. None of the other scripts do this — don't copy the
additive-blend trick into real-output code, it'd double-expose real
overlaps that don't exist yet.

**Keys** (interactive mode, all scripts): `B` toggle blend ramp, `L` toggle
black-level lift, `G`/`H` gamma −/+, `Esc`/`Q` quit (`demo_blend.py` also
has `[`/`]` for overlap width and `R` to reset). Toggle `B` and `L` off to
see the two failure modes the doc calls out separately, then back on to see
the fix. Verified numerically, not just by eye: with a synthetic 0.05 leak
per output, the seam-vs-background delta in the dark field drops from
**+21/255 to +5/255** with both corrections on.

## `demo_real_displays.py` — real geometry, no config file

`displays.py` enumerates real displays via SDL (RandR-backed under X11) —
name, position, size, refresh rate — fresh, every run. Window rects are the
real, non-overlapping desktop positions (X/xrandr has already composed real
projector outputs side-by-side into one virtual screen before this process
starts, per §4) — only the FBO crop each output samples is widened into its
neighbours. No additive window-space blending; each output draws its own
region normally.

**Verified against real 2-monitor hardware**: `--overlap 0` (the default —
correct for ordinary abutting monitors) produces a perfectly continuous
test pattern across both physical screens — no gap, no seam, no
misalignment. **`--overlap` > 0 on ordinary monitors is a deliberate stress
test, not a usable setting** — it produces a visible dark band at the seam,
which is *correct*: the ramp fades each edge expecting a second physical
light source to fill it back in, and two ordinary monitors have none.

## `profile.py` + `demo_profile.py` — config file (§6, §9 step 3)

`profile.py` is ARCHITECTURE.md §6's JSON shape as actual dataclasses:
`ProfileSpec` (canvas, groups) → `GroupConfig` (id, scene, blend, outputs)
→ `OutputConfig` (name, viewport, corners, mesh). One unit conversion
happens on load/save: the doc's example encodes `corners` in the output's
own **pixel space** (`[[0,0],[1920,0],...]` sized to its viewport), not the
`[0,1]` UV space `correction_chain.homography()` expects —
`OutputConfig.corners_uv()` converts. Empty `corners` (`[]`) means
identity/no keystone.

**CLI** (`python3 -m renderhost.profile ...`):
- `from-real OUT.json [--overlap PX] [--gamma G] [--lift L]` — bootstrap a
  one-group profile from currently-detected displays.
- `validate PATH` — checks a profile against currently-detected displays:
  duplicate output-in-two-groups, configured output not physically present,
  viewport not matching real detected geometry, detected display not
  assigned to any group. Never raises.
- `show PATH` — prints groups/outputs/blend params.

`demo_profile.py` loads a profile and renders ONE named group (`--group
ID`), containing no layout logic of its own — everything comes from the
file.

**Full round-trip verified on real 2-monitor hardware**: `from-real` →
`validate` (clean) → `demo_profile.py --screenshot` produced the same
seamless two-monitor image as `demo_real_displays.py`'s direct-detection
path. Also verified: `validate` genuinely catches problems (tested against
4 deliberately broken profiles — mode-changed viewport, output absent from
hardware, unassigned display, output duplicated across two groups — all
four flagged correctly), and keystone round-trips correctly (hand-edited
one output's `corners`, only that output warped on render).

## `serve.py` — the web control panel (§8)

The doc's actual intended end-state for control: "WebSocket, host ↔
laptop... aiohttp server on the render host, vanilla JS control surface in
the laptop browser" — the exact pattern lightsaber's own web app already
uses. `serve.py` loads a profile (same as `demo_profile.py`) and additionally
serves a browser page (`web/index.html`) that can tune overlap/gamma/lift
live and save the result back to the profile file.

**Threading, and the one real bug in it:** the SDL/GL render loop has to
stay on the main thread; aiohttp runs on a background thread with its own
asyncio event loop, and the two talk through a lock-protected queue drained
at the top of each render tick — the *exact* discipline lightsaber's own
`composite.py` already uses, for the same reason ("queued mutations,
applied at the top of `_loop`... so the render thread never races the
websocket thread"). The first version used `aiohttp.web.run_app()` in that
background thread, which crashed immediately: `run_app()` installs a SIGINT
handler, and Python's `signal` module only works on the main thread —
`RuntimeError: set_wakeup_fd only works in main thread of the main
interpreter`. Fixed by using the lower-level `AppRunner`/`TCPSite` API
instead, which doesn't try to install signal handlers.

**What the panel does**: shows the current group and its outputs
(read-only — switching which group renders isn't wired up), sliders for
overlap/gamma/lift + blend/lift toggles, **draggable corner-pin/keystone
editing** (one output at a time, selected from a dropdown), and a Save
button. The corner editor mirrors lightsaber's own `index.html` shape
editor — same drag-a-handle-with-hit-testing pattern, just one quad, no
zoom/pan, no multi-shape selection. Corners are dragged and transmitted in
the output's own real pixel space (matching `profile.py`'s on-disk
convention directly — what you drag is what gets saved, no unit surprises
at save time), converted to `correction_chain`'s `[0,1]` UV space only for
the live render. A generous overshoot past the nominal rect is allowed on
purpose, matching real keystone tools where a corner often needs to move
past the viewport's own edge.

**Verified end-to-end, not just that the page loads:**
- Page loads showing the actual profile's real state (path, canvas size,
  group, outputs, current blend params) — zero console errors.
- Slider/toggle changes propagate through the full path — browser → websocket
  → queue → render thread — confirmed with a **pixel-level before/after
  comparison**, not just checking the broadcast state: toggling blend off
  on a real 2-output profile visibly changes the rendered seam (a
  screenshot command over the same websocket protocol was used to capture
  both states for comparison). The very first attempt at this test used a
  single-real-display profile and showed byte-identical screenshots before
  and after toggling blend — correctly diagnosed as expected (a single
  output has no interior seam regardless of the overlap setting, so there
  was nothing to blend), not a bug, by rebuilding the test against a
  synthetic 2-output profile instead of assuming either way.
- **Corner drag verified pixel-level too**: a real simulated mouse drag on
  a corner handle (not a direct websocket message) produced visible
  keystone distortion in a live render screenshot, matching the dragged
  corner's new position exactly, and the same distortion appeared in the
  web UI's own editor canvas — confirming the visual feedback loop, not
  just the underlying data.
- **Save writes correctly**: changed gamma, lift, and corners live via the
  actual UI (button/drag, not raw websocket messages), and the file on disk
  showed the new values afterward, in the exact pixel-space format
  `profile.py` expects — confirmed by loading the saved file back through
  `profile.py show` (reported `corners=keystoned`), not just re-reading the
  raw JSON. `overlap` (untouched in that test) stayed unchanged, confirming
  `save()` doesn't clobber fields it didn't touch. **Reset restores exactly
  `profile.py`'s own identity convention** (`[]`, not literal identity
  pixel corners) — confirmed on disk after Save.

## The correction chain (§5.4)

1. **Crop** — a sub-rect of the shared FBO scene texture per output.
2. **Corner-pin homography** — a real per-pixel projective warp, same
   Heckbert construction as `homography()` in `index.html`/`output.html`
   though NOT the same coordinate convention (see `correction_chain.py`'s
   docstring: an earlier version mismatched [-1,1] identity corners against
   a shader expecting [0,1] UVs, which made the scene texture wrap/tile via
   `GL_REPEAT` instead of cropping correctly). Configurable via a profile's
   `corners`, either by hand or via `serve.py`'s draggable corner editor —
   both verified pixel-level.
3. **Mesh warp — NOT implemented.** See below.
4. **Edge blend** — gamma-corrected ramp + black-level lift, both
   independently toggleable (keyboard in three scripts, browser in
   `serve.py`), numerically and pixel-verified.

## What's real vs. deferred

**Real, working, tested (including the interactive event loop, not just
`--screenshot` — a real bug, `sdl2.byref` instead of `ctypes.byref`, made
it through an earlier round specifically because only the screenshot path
had been exercised; an overlap-math bug doubled at every seam, caught by
synthetic-display testing; and `serve.py`'s aiohttp-in-a-thread signal
handler crash, caught immediately on first run):**
- Two-stage FBO render (§5) — scene renders once, outputs sample it independently.
- Crop + overlap accounting: invented, from live-detected displays, and from a saved config file — all verified against real hardware.
- A true projective corner-pin homography per output, including config-file-driven keystone.
- Gamma-corrected blend ramp + black-level lift, independently toggleable, numerically AND pixel verified.
- N outputs, not hardcoded — every script scales.
- Real display enumeration via SDL/RandR (`displays.py`).
- Config file load/save + validation (`profile.py`) — round-tripped end-to-end on real hardware.
- **A working web control panel** (`serve.py`) — live tuning + save, verified pixel-level, not just that the UI reflects state.
- **Draggable corner-pin/keystone editing in the browser** — pixel-verified: a real simulated drag produced matching visible distortion in both the live render and the editor's own canvas, and round-trips correctly through Save/`profile.py show`.

**Deferred, on purpose, not because it's hard:**
- **Mesh warp (§5, step 3)** — the doc lists this after homography for a
  reason, and tuning a warp mesh without a physical curved surface to test
  against is guesswork. `OutputConfig.mesh` is carried through profiles as
  an opaque dict, unread by the renderer.
- **KMSDRM** — everything runs windowed under X11, the doc's own
  recommended dev path. No headless direct-to-hardware path yet.
- **Group switching / multi-group simultaneous rendering** — `serve.py` and
  `demo_profile.py` both render one group per process (`--group`).
  Switching live, or rendering several groups (potentially on different
  real displays) at once, would need either multiple GL contexts sharing
  the FBO texture or per-group FBOs — a real architectural piece.
- **EDID model/serial, hardware fingerprinting (§6)** — `displays.py` uses
  SDL's display API, which doesn't expose EDID.
- **Video layer, libmpv (§5)** — the FBO scene is a procedural test
  pattern. `GroupConfig.scene` is carried through but unread by the renderer.
- **Auth/access control on `serve.py`** — the control panel has none; it
  binds `0.0.0.0` by default like lightsaber's own server. Fine for an
  isolated show-network link (§3), not for an open network.

## If you want to keep going

Roughly the doc's own §9 order, not implemented speculatively here:

1. Mesh warp, once a real curved/irregular surface exists to test against.
2. Multi-group simultaneous rendering, once more than one group is actually
   needed at once (e.g. a blended wall + independent side pillars, per §5's
   own example).
3. EDID-based profile auto-matching (§6), once real hardware swaps/moves
   between venues is an actual workflow being exercised.
4. Group switching from the web panel (currently one `--group` per process).
