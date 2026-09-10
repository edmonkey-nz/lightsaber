# Multi-Projector Render Rig — Architecture

Status: design, partially prototyped. A Python/moderngl prototype in
`renderhost/` (see `renderhost/README.md`) has implemented and verified —
against real hardware, not just in theory — the correction chain, the
config file format, and a working control-protocol implementation
(§5, §6, §8, §9 items 1–4 and 6). The production render host (KMSDRM,
video layer, an actual multi-projector rig) is **not** built. See §12 for
exactly what's proven, what changed, and suggested next steps.
Audience: Claude Code sessions working on this repo. Read this before proposing changes to display, network, or render-loop code.

**This document, and everything under `renderhost/`, is lightsaber-only.**
Lightsaber is a fork of promptwaver (still developed alongside it, on the
`upstream` remote) and the two share a scene engine — but promptwaver drives a
vector laser and has no render host, no projector rig, and no equivalent of
any of this. So: don't look upstream for prior art on §2-§10, and don't try to
port this work back. What *does* flow between them is scene/generator/director
code, selectively and by hand — see TECHNICAL.md's "Relationship to
promptwaver" for how, and for what has already crossed.

---

## 1. What this system is

A two-machine setup for driving four projectors from a single wide raster canvas, with live VJ-style control.

```
  ┌─────────────────┐         ┌──────────────────────────┐
  │  CONTROL LAPTOP │         │  RENDER HOST (headless)  │
  │                 │  Cat6   │                          │
  │  browser UI     │◄───────►│  render process          │
  │  ssh            │ direct  │  RX 580 ─┬─ HDMI1 → PJ 1 │
  │                 │  link   │          ├─ HDMI2 → PJ 2 │
  │  (wifi → net)   │         │          ├─ DP1   → PJ 3 │
  └─────────────────┘         │          ├─ DP2   → PJ 4 │
                              │          └─ DVI-D → PJ 5 │
                              └──────────────────────────┘
```

The laptop renders **nothing**. It is a control surface only. All pixels are produced on the render host.

### Deployment topologies

Two supported configurations. **Same binary, same config format, same browser UI** — they differ only in where the render process runs and what address the control surface connects to.

**A — Laptop only (1–2 outputs).** Laptop drives its internal display plus one external projector via HDMI or USB-C DP Alt Mode. Render process and aiohttp server both local; browser at `localhost:8080`. No render host, no cable, no venue rack. This covers small gigs and is the daily development configuration.

**B — Laptop + render host (3–5 outputs).** The topology diagrammed above. Required once projector count exceeds what the laptop's display engines can drive.

**Design implication — never assume the render host is remote.** The server binds to a configurable address (default `0.0.0.0`), the control surface connects to a configurable host (default the page's own origin), and nothing hardcodes `192.168.50.1`. Topology A must remain a first-class path, not a debug mode; if it breaks, the dev loop breaks with it.

**Laptop-specific caveats in topology A:**
- USB-C DP Alt Mode is the same display engine on a different connector — it does not add heads.
- Do not route through a DisplayLink dock (see §7).
- Disable sleep and lid-close suspend for the duration of a set.
- Laptop GPUs throttle. A two-hour set is a sustained load the machine was not specced for; watch frame times, not just peak performance.
- Hybrid graphics (iGPU + discrete) can put the GL context on one GPU and scanout on another. Check with `DRI_PRIME` / `glxinfo` before trusting it at a venue.

### Design rationale

An earlier design distributed one render node per projector, sending vector strokes over the network. That is the right architecture for a purely vector show, because strokes are tiny on the wire. It was rejected here because this system must carry a **live raster canvas** — 7680×1024 uncompressed is ~1.4 GB/s, which is not shippable over ethernet without an encode stage whose latency defeats the purpose.

Single host, single framebuffer, single GL context. Multi-node is out of scope. Do not reintroduce it.

---

## 2. Hardware

| Part | Spec | Notes |
|---|---|---|
| Render host | Desktop, Sapphire NITRO+ RX 580 8G | 1× DVI-D, 2× HDMI, 2× DP 1.4. **Max 5 displays.** ~NZ$150 used. |
| Control laptop | Any | Browser + ssh only. |
| Link | Cat6, direct | No switch. Auto-MDIX; straight cable is fine. |
| Adapters | 2× DP→HDMI (+1 DVI-D→HDMI for a 5th) | 2 projectors use the native HDMI ports directly. See §7. |
| PSU | 500W+, 1× 8-pin + 1× 6-pin | Card is <235W. Verify both connectors exist. |
| Cable runs | AOC fibre HDMI if >10m | See §7. |

**Why AMD:** `amdgpu` is in-kernel, `radeonsi` is mature, no proprietary blob. Easier than NVIDIA for a headless KMS/X setup.

**Physical:** 2.2 slots, 260×135×43mm. Long and thick; blocks the adjacent PCIe slot. Measure the case. It is a hot card by modern standards — a two-hour set in a closed rack is not the workload a desktop case assumes, so plan airflow.

**Used-card risk:** RX 580s were the dominant Ethereum mining card and many cheap units ran continuously for years. Ask about history, inspect fans, and load-test before committing.

**Control monitor on the render host:** at 4 projectors one head remains free and can drive a local screen. At 5 projectors all heads are consumed — use the motherboard iGPU with "iGPU Multi-Monitor" enabled in BIOS. Either way the laptop is the preferred control surface, which is why it exists.

---

## 3. Network

Isolated point-to-point link. No DHCP on this segment, so both ends are static.

```
render host   192.168.50.1/24   (netplan profile, persists across reboot)
laptop        192.168.50.2/24
```

Laptop keeps wifi up for internet. Ensure the wifi route has the lower metric so the default route does not go down the show link.

**Traffic on this link is control only** — websocket state, parameter changes, scene selection. Kilobytes per second. Gigabit is deliberately overspecced for headroom and zero contention.

**Optional later:** downscaled preview frames (≤480p, JPEG or VP8) pushed host→laptop so the operator can work from the back of the room. Bandwidth is available. Not required for v1.

**Headless recovery risk:** if the link fails there is no way into the host. Mitigations, in order of preference:
1. Static IPs (no DHCP lease to expire)
2. Second NIC on a normal switch as a fallback path
3. Keyboard + monitor physically available at load-in

Never ship a netplan change to the host without a tested rollback.

---

## 4. Display configuration

### OS layer (setup-time, once per venue)

`xrandr` composes the four DP outputs left-to-right into one framebuffer:

```
7680 × 1024   (4 × 1920×1024)
9600 × 1024   (5 × 1920×1024)
```

Canvas width follows projector count. Nothing in the renderer hardcodes either figure.

From the application's perspective this is one very wide screen. The app opens one fullscreen window across it.

**Do not assume 1920×1024 exactly.** Projectors misreport native resolution; some negotiate 1920×1200 or non-standard modes. The app must **read actual geometry from RandR at startup** and lay out from that. Output names come from config so the correct heads are targeted.

Required at startup:

```bash
xset -dpms
xset s off          # otherwise the show blanks mid-set
```

### KMSDRM alternative

X is not required. SDL2's `kmsdrm` backend gives a GLES context with no display server:

```bash
SDL_VIDEODRIVER=x11     ./render      # dev, windowed on laptop
SDL_VIDEODRIVER=kmsdrm  ./render      # venue, direct to hardware
```

Same binary, same shaders. KMSDRM saves ~750MB RAM and a frame of compositor latency, but multi-output requires manually enumerating connectors and assigning a CRTC each — X gives framebuffer spanning for free. **Start on X.** Revisit KMSDRM only if frame timing proves inadequate.

If using KMSDRM: X must not be running (single DRM master), and the process user needs `video` and `render` group membership.

---

## 5. Render architecture

Two-stage. The scene renders once into an offscreen buffer; each projector then samples that buffer through its own transform.

```
  scene (vectors + video layers)
        │
        ▼
  FBO  7680×1024
        │
        ├── viewport 0 ──► crop₀ → homography₀ → mesh warp₀ ──► DP1
        ├── viewport 1 ──► crop₁ → homography₁ → mesh warp₁ ──► DP2
        ├── viewport 2 ──► crop₂ → homography₂ → mesh warp₂ ──► DP3
        └── viewport 3 ──► crop₃ → homography₃ → mesh warp₃ ──► DP4
```

```
render_scene_to_fbo()
for i, out in enumerate(outputs):
    gl.viewport(out.x, out.y, out.w, out.h)
    draw_warped_quad(fbo_texture, out.crop, out.corners, out.mesh)
```

**The FBO is mandatory, not an optimisation.** Warping requires *sampling* the source; pixels already scanned out to the window cannot be distorted. Rendering direct-to-window forecloses the entire correction stage.

### Per-output correction chain

1. **Crop** — sub-rect of the FBO. Not necessarily an even quarter; overlapping crops give the source side of edge blending for free when it is wanted.
2. **Corner-pin homography** — keystone correction for flat surfaces. Port the existing implementation from the laser project; the maths is identical.
3. **Mesh warp** — grid of control points, bilinear or bezier, for curved/irregular surfaces.
4. **Edge blend** — gamma-corrected blend ramps **plus black-level lift compensation** in overlap regions. The lift is routinely forgotten and is why untreated blends show grey bands.

**The blend stage is always present and is never conditionally compiled out.** With ramp width 0 and lift 0 it is an identity operation — a multiply by 1 and an add of 0, no branch, no measurable cost. Do not create a separate non-blending code path or a second binary; that duplicates the config format, the correction chain, the control protocol and the alignment UI, and the two will drift.

**Prototype status (§12):** all four steps except mesh warp have a working
reference implementation — `renderhost/correction_chain.py`. Two things
worth knowing before reimplementing this in any language:
- **The naive way to split an overlap doubles it.** Padding each
  neighbour's crop by the *full* configured overlap (not half) makes every
  interior seam twice as wide as configured — an easy mistake in any
  language, caught here by testing the layout math against synthetic
  multi-display data before real hardware was available.
- **A blend ramp only helps where light actually overlaps.** Tried at
  nonzero on two ordinary abutting monitors (no physical overlap) on real
  hardware, it produces a visible *dark* band, not a fix — the ramp fades
  each edge expecting a second light source to fill it back in, and
  ordinary monitors have none. Overlap should be 0 unless the outputs are
  genuinely overlapping projector throws.

### Groups

A global spanned/independent flag cannot express a mixed rig (e.g. 3 projectors blended into one wall plus 2 standalone surfaces). The unit of organisation is therefore a **group**: one logical canvas, one scene source, one or more outputs.

- **Spanned** = a single group containing every output.
- **Independent** = N groups of one output each.
- **Mixed** = whatever combination the venue needs.

There is no mode enum. Dropping it removes a special case rather than adding one.

**Blend ramps exist only at interior seams within a group.** A group's outermost left and right edges get zero ramp, as do all edges of single-output groups. **Derive ramps from group membership in the config loader** rather than accepting them per-output — hand-setting eight ramp values and getting one wrong is a classic load-in bug.

**Overlap shrinks the logical canvas; it does not extend it.** Three 1920-wide outputs with two 288px overlaps give 5760 − 576 = **5184** logical pixels, not 5760. Scenes must be authored against the reduced width or geometry will be subtly wrong across the whole group. Compute this in the loader; never hand-calculate it.

```
group_width = Σ output_widths − Σ interior_overlaps
```

**Group membership is explicit config, never inferred from geometry.** Two projectors that merely abut are not a blended pair, and inference will guess wrong.

Everything downstream of the FBO — crop, homography, warp, blend, output — is identical regardless of grouping. This is content routing, not architecture.

**Physical caveat:** within a blended group, use identical projector models where possible. Differing lamp ages, colour temperatures and black levels are visible as banding at the seam. Across groups it does not matter.

### Content layers

Both composite into the same FBO before the correction chain:

- **Vector** — the existing stroke engine. Primary content source.
- **Video** — decoded via VA-API, imported as a GL texture through DMA-BUF (zero-copy), drawn as a textured quad. Because it lands in the FBO, it inherits crop/warp/blend for free.

For video, `libmpv`'s render API is worth preferring over raw GStreamer: it draws into *your* GL context rather than owning a window, and brings seeking, gapless playback and format handling at low integration cost.

**One decode per clip, not one per output — and that, not the warp, is the whole argument for this design.** Because content resolves into the shared FBO *before* the per-output stage, a clip spanning three projectors is decoded once, drawn once, and then read three times as three different crops of the same texture. Topology A cannot do this: each output is a separate browser page with its own `<video>` elements, so N clips on M outputs means N×M decoders and N×M warps for 1× the picture. Measured on the laptop app (see §12's lessons): 10 distorted 1080p layers across 4 outputs cost ~113ms/frame — about 2fps — because every window was decoding and mesh-warping all ten. Adding outputs here is close to free by construction; there, it multiplies. Anything that reintroduces per-output *content* work (a per-output content filter, a per-output source selection) gives that property back up, so it belongs in the correction chain — which is per-output by definition — and not upstream of the FBO.

**RX 580 (Polaris, UVD 6.3)** decodes H.264, HEVC and VP9 in hardware. VP9 and HEVC are fine here — unlike the earlier thin-client candidates. AV1 is software-only; transcode if it appears.

---

## 6. Configuration format

All projector setup is **data, not code**. This is what makes one binary run on a desk with one screen and in a venue with four.

```json
{
  "canvas": { "height": 1024 },
  "groups": [
    {
      "id": "wall",
      "scene": "main",
      "blend": { "overlap": 288, "gamma": 2.2, "lift": 0.012 },
      "outputs": [
        { "name": "HDMI-A-1", "viewport": [0, 0, 1920, 1024],
          "corners": [[0,0],[1920,0],[1920,1024],[0,1024]],
          "mesh": { "cols": 5, "rows": 5, "points": [] } },
        { "name": "HDMI-A-2", "viewport": [1920, 0, 1920, 1024], "corners": [], "mesh": {} },
        { "name": "DP-1",     "viewport": [3840, 0, 1920, 1024], "corners": [], "mesh": {} }
      ]
    },
    {
      "id": "pillar-left",
      "scene": "loops-a",
      "outputs": [
        { "name": "DP-2", "viewport": [5760, 0, 1920, 1024], "corners": [], "mesh": {} }
      ]
    },
    {
      "id": "pillar-right",
      "scene": "loops-b",
      "outputs": [
        { "name": "DVI-D-1", "viewport": [7680, 0, 1920, 1024], "corners": [], "mesh": {} }
      ]
    }
  ]
}
```

**Loader responsibilities** — computed, never authored by hand:

- `group_width` per group, from output widths minus interior overlaps
- per-output crop rects within the group's logical canvas
- per-edge blend ramps, zero at group boundaries and on single-output groups
- validation that viewports match real RandR geometry, and that no output appears in two groups

**Prototype status (§12):** this exact shape is implemented in
`renderhost/profile.py` (`from-real`/`validate`/`show` CLI) and round-tripped
end-to-end on real hardware — bootstrap from detected displays, save,
reload, render, matches the direct-detection path exactly. `validate` was
tested against four deliberately broken profiles (resized/mode-changed
viewport, output absent from hardware, display left unassigned, output
duplicated across two groups) and caught all four. One implementation
detail, not a spec change: this example's `corners` are pixel space, sized
to the output's own viewport — the renderer's internal math uses `[0,1]`
UV space instead (an early version conflated the two, causing the source
texture to wrap instead of crop), so any implementation needs to convert on
load/save, same as `OutputConfig.corners_uv()` does.

### Detection and reconciliation

**The engine enumerates RandR at startup and already knows what is connected** — output names, real modes, positions, EDID model and serial. The UI must never ask the user how many projectors exist; it shows what was detected and asks them to arrange it.

**Detected geometry is truth; the profile is intent.** The loader reconciles them:

- Outputs present in hardware but not in the profile appear as **unassigned** in the UI. This is how scaling works: plug in more projectors, restart, drag the new heads into groups. Existing calibration for unchanged outputs is untouched.
- Outputs in the profile but absent from hardware are flagged, not silently dropped.
- Modes that differ from the profile are a warning, since it usually means a projector renegotiated.

**Do not key calibration to connector names alone.** Store output identity as name **plus EDID model/serial** where available. Connector naming differs between machines — the laptop's `eDP-1` has no counterpart on the render host, and DP ports enumerate differently across GPUs. When a saved profile does not match detected hardware, present an explicit **mapping step**.

This matters most in the exact case the system is designed for: someone moves from topology A to topology B, or swaps two projectors between ports. Silently applying the wrong warp mesh to the wrong surface is the worst available failure, and it looks like a calibration bug rather than a mapping bug. A one-time "these outputs don't match — map them" dialog prevents an entire class of load-in confusion.

### Profiles

Two separate, independently versioned artifacts. **Do not merge them.**

```
profiles/
  warehouse-5pj.json      # physical: outputs, EDID keys, groups, overlaps,
                          #           corners, meshes, blend params
  laptop-1pj.json
shows/
  aurora-set.json         # creative: scene source per group, parameters,
                          #           references profile by id
  quiet-set.json
```

- **Profile** = the rig. Changes when projectors physically move or the hardware changes. Slow-changing, expensive to recreate, worth backing up.
- **Show** = the content. Changes between sets at the same venue. Cheap, disposable, experimental.

Launch names a show; the show names its profile.

**Profiles carry a hardware fingerprint** (the set of EDID identities). On startup the engine can match detected hardware against saved profiles and auto-select, so a known rig comes up correctly with no prompting. Ambiguous or partial matches fall through to the mapping step above rather than guessing.

---

## 7. Hard constraints

Facts that have already cost investigation. Do not re-derive; do not design around them being false.

- **Head count is a display-engine limit, not a port count.** NVIDIA consumer caps at 4. Polaris has six display pipes; **this specific card is rated for 5 simultaneous displays** and fits five connectors to match. No hub, dock, splitter or MST device increases whatever the limit is.
- **MST hubs (e.g. StarTech MSTMDP124DP) do not add heads.** They multiplex one DP link into several sinks; each sink still consumes a display engine. Bandwidth is adequate for 4× 1080p on DP 1.2, but they solve a single-output-laptop problem, not this one. Direct connection is fewer failure points. Rejected.
- **5 projectors is the ceiling on this card, and it is reachable without architecture change** — the 5th is a passive DVI-D→HDMI adapter, since DVI-D carries the same TMDS signalling as HDMI. DVI-D tops out at 2560×1600@60Hz, comfortably above 1080p. A **6th** projector is the architecture change (second GPU with separate X screens, or a Datapath FX4 fan-out).
- **DisplayLink is disqualified** for any output path — USB compression with CPU-side encode. Latency and artefacts are incompatible with realtime visuals.
- **Passive DP→HDMI adapters only work on DP++ ports.** Verify port capability; otherwise buy active. This is the single most common failure point in these builds. Only the two DP ports are affected — the two HDMI ports take plain cables, and DVI-D→HDMI is always passive.
- **Passive HDMI copper is unreliable beyond ~15m.** Under 5m is safe, 5–10m fine for 1080p, 10–15m unpredictable. Beyond that use AOC fibre (30–100m, thin, ground-loop immune). Failure mode is sparkle or total dropout, not gradual degradation.
- **EDID/DDC fails before video does.** Symptom is a projector not being detected at all, plus X reshuffling the `xrandr` layout when a projector powers down. EDID emulators at the source end fix both. Budget for them on long runs.
- **DPMS/screensaver must be disabled** or the show blanks mid-set.

---

## 8. Control protocol

WebSocket, host ↔ laptop. Reuses the established pattern: aiohttp server on the render host, vanilla JS control surface in the laptop browser.

**Messages carry state plus a master timestamp**, not deltas. Render loop reads the latest state each frame.

**Invariants:**

- **Never queue frames or block the render loop on network I/O.** A stalled or dropped websocket must not stall rendering. Latest-wins; drop rather than buffer.
- **Reconnect must be transparent.** Laptop sleeps, cable gets kicked, wifi adapter resets — the render process holds its state and keeps drawing. The control surface reconnects and resyncs.
- **ssh on the same link** for process restart, logs, and `xrandr` adjustment: `ssh user@192.168.50.1`.

**Prototype status (§12):** working in `renderhost/serve.py`, verified
pixel-level (not just that state messages round-trip): a browser slider
change was confirmed to alter the actual rendered output via a screenshot
command sent over the same socket. Implementation invariant enforced the
same way `composite.py` already does elsewhere in this repo — the render
loop stays on its own thread, the websocket server on another, mutations
cross via a lock-protected queue drained at the top of each render tick,
never applied directly from the network thread. **Python-specific gotcha,
worth knowing if the render host stays Python:** `aiohttp.web.run_app()`
installs a SIGINT handler, which only works on the main thread — since the
render loop owns the main thread here, `run_app()` crashes immediately in a
background thread (`set_wakeup_fd only works in main thread of the main
interpreter`). Use the lower-level `AppRunner`/`TCPSite` API instead, which
doesn't touch signal handling.

---

## 9. Build order

Status column reflects `renderhost/`'s Python prototype, verified against
real hardware — see §12. A checked item means proven and tested, not
production-hardened; none of this has run on the actual multi-projector rig
in §2, only on a laptop's own display plus one external monitor.

1. ✅ Single-window GL app, one output, FBO + one warped quad. Prove the correction chain. **This is topology A with one projector — a shippable configuration, not throwaway scaffolding.**
2. ✅ RandR enumeration; drive viewport layout from real geometry. (`displays.py`, SDL's display API — RandR-backed under X11, but no EDID; see §6.)
3. 🟡 RandR detection, reconciliation, profile/show load-save. Load/save/validate done (`profile.py`); reconciliation is a CLI report (`validate`'s warnings), not the interactive mapping UI §6 describes for a mismatched profile.
4. 🟡 **Alignment UI — corner drag, test grid, group assignment, overlap regions highlighted.** Corner drag is done, in a browser panel (`serve.py` + `web/index.html`), pixel-verified. No test grid, no group-assignment UI, no overlap-region highlighting yet. Still true: don't defer this further — it's the thing used at every load-in.
5. ⬜ Video layer via libmpv into the FBO. Not started — the prototype's FBO holds a procedural test pattern only.
6. ✅ Edge blend — done ahead of this order, deliberately, since seeing it work was the actual first ask. Gamma-corrected ramp + black-level lift, independently toggleable, numerically and pixel verified.

---

## 10. Process launch

**One binary, selected behaviour via config argument.** There is no separate blended build, no separate independent build, and no separate laptop build — topology A and B run the same executable.

```bash
render --show shows/aurora-set.json      # show references its profile
```

Launched from the laptop over the show link. Prefer a systemd user template on the render host so the process outlives the ssh session and restarts on crash:

```bash
ssh user@192.168.50.1 systemctl --user start render@aurora-set
ssh user@192.168.50.1 systemctl --user status render@aurora-set
journalctl --user -u render@aurora-set -f      # over ssh, live logs
```

**Do not launch with bare ssh.** Closing the laptop lid sends SIGHUP and the show stops. If launching by hand for development, wrap in `tmux` or `systemd-run --user --scope`.

Switching between a blended wall and separate surfaces is a different profile; switching content at the same venue is a different show. Neither is a codebase split.

---

## 11. Open questions

- ~~Blended or separate surfaces?~~ **Resolved: both, and mixtures.** Single binary, groups in config, blend stage always present and identity when ramps are 0. See §5.
- ~~Is a 5th projector plausible?~~ **Resolved: the card supports 5.** The 5th is a cable and an adapter, not a redesign. A 6th remains an architecture change. See §7.
- Preview-to-laptop: needed for v1, or later?
- Should shows be switchable live without restarting the render process, or is relaunch acceptable between sets?
- Do scenes in separate groups need a shared clock (so they can be rhythmically related), or are they fully autonomous?
- **New, raised by the prototype (§12):** stay Python/moderngl for the
  production render host, or reimplement in C/C++/Rust once real
  performance requirements are known? Python has now proven the algorithms
  and protocols end-to-end, but hasn't been load-tested with real video
  decode (§5's libmpv layer, not built) or KMSDRM (headless, not built) —
  both are exactly the workloads §1 already flags as demanding
  ("a two-hour set is a sustained load the machine was not specced for").
- **New:** the corner-drag alignment UI now exists in the browser (§9 item
  4, partial) — is a browser-only tool sufficient for venue load-in, or
  does the workflow also need it to keep working if the control laptop
  can't reach the render host (a native on-host fallback)?

---

## 12. Prototype status & next build steps

A Python (`moderngl` + `PySDL2` + `aiohttp`) prototype lives in
`renderhost/` in this repo. Full detail, including exactly what was tested
and how, is in `renderhost/README.md` — this section is the short version,
kept here so the design doc and the implementation status don't drift
apart.

### What's proven, against real hardware

- The two-stage FBO render (§5): scene renders once, each output
  crops/warps/blends its own view independently.
- Crop + overlap accounting, both invented (CLI args) and from real
  RandR-detected geometry — including the halved-overlap-per-seam fix
  above.
- A true projective corner-pin homography per output, editable by hand
  (JSON) or by dragging in a browser panel — both pixel-verified.
- The gamma-corrected blend ramp + black-level lift, independently
  toggleable, verified both numerically (a synthetic leak's seam-vs-
  background delta) and visually (real 2-monitor hardware).
- RandR display enumeration (name/position/size/refresh — no EDID).
- The §6 config format, round-tripped end-to-end: bootstrap from detected
  hardware → validate → render, and `validate` genuinely catches broken
  profiles rather than silently accepting them.
- A working §8 control protocol: a browser panel tuning a live render over
  a websocket, confirmed via actual pixel changes, not just message
  round-trips. The threading/queue discipline matches this repo's own
  `composite.py`.

### What's explicitly not built

- Mesh warp (§5 step 3) — needs a real curved/irregular surface to tune
  against; building it blind would be guesswork.
- KMSDRM / any headless direct-to-hardware path — everything so far runs
  windowed under X11, the doc's own recommended dev path.
- The video layer (§5's libmpv/DMA-BUF path) — the FBO currently holds a
  procedural test pattern, not real content.
- Multi-group **simultaneous** rendering — one group per process
  currently; several groups at once (e.g. a blended wall + independent
  pillars) needs either multiple GL contexts sharing the FBO or per-group
  FBOs, a real architectural piece.
- EDID-based hardware fingerprinting/profile auto-matching (§6) — SDL's
  display API doesn't expose EDID; would need real Xlib/XRandR calls.
- Everything in §2–§4, §7: no real multi-projector rig, no RX 580, no
  isolated network link, no venue testing. The prototype has only ever run
  against a laptop's own display plus one external monitor.

### Lessons from the laptop app (topology A), carried forward

Not from `renderhost/` — from bugs found and fixed in this repo's own
shipping topology-A app (`lightsaber/`, the browser control surface +
`composite.py`) during ordinary use. Recorded here because each one bears
directly on a design point elsewhere in this doc, and the render host
should either inherit the fix or confirm by construction that it doesn't
apply.

- **The crop/viewport model in §6's config format is already
  implemented and load-tested, just not under that name.**
  `OutputMonitorConfig` (`lightsaber/projects.py`) gives each output
  `viewport_x/y/w/h` (normalized `[0,1]`, top-left origin) plus a
  `duplicate_of` index so one output can mirror another's crop while
  keeping its own flip/keystone/test-pattern independent. A project-level
  `viewports_enabled` flag makes the whole thing identity (every output
  shows the full canvas) when off — the same "off = identity, not a
  branch" shape as the blend ramp in §5. This is the same crop concept as
  §5/§6's `viewport`/crop rect, just normalized-fraction instead of
  pixel-space; worth reconciling the two representations (or picking one)
  rather than inventing a third when the render host's own config loader
  gets built.

- **A real bug, now fixed, worth confirming can't recur here: per-primitive
  opacity blended BEFORE compositing double-counts anti-seam overlap.**
  The vector renderer inflates each warped triangle by ~1.5px to hide
  seams between adjacent triangles (invisible at full opacity, since
  overlapping opaque pixels just redraw the same color). Applying a
  master/group opacity *per-primitive* — i.e. baking it into each
  triangle's own alpha before drawing — made that overlap double-blend
  into a visible grid at any fractional opacity, because the same pixel
  got alpha-composited twice. Fixed by drawing every primitive at its own
  full opacity into an off-screen scratch buffer first, then applying the
  group/master opacity **once**, to the finished frame. **This is exactly
  why §5's two-stage FBO design (scene resolves fully before any
  per-output stage runs) already avoids this class of bug by
  construction** — but it's only safe as long as no per-output or
  per-group stage reintroduces a per-primitive opacity multiply upstream
  of the FBO resolve. Worth a one-line note near §5's diagram, since the
  reason it's safe isn't obvious from the diagram alone.

  **It then happened a second time, in a different renderer.** The media /
  webcam / text path warps through its own ~72-triangle mesh with the same
  1.5px anti-seam skirt, and had the same `globalAlpha = opacity` set once
  before drawing all of them — so every shape below full opacity showed a
  bright grid. Measured on flat grey: at 50% the body read 64 while the
  seam band reached **125**, nearly double; at 25% the seams were over 3×
  too bright. Fixed identically (mesh drawn opaque into a scratch, blended
  in once), and the body is now flat to 1–2 levels of rounding at 25/50/80%.
  The lesson to carry across: **this is not a one-off bug, it is what
  "overlapping primitives + per-primitive alpha" always does**, and any
  renderer that inflates geometry to hide seams will reproduce it the
  moment a group opacity is introduced. In the GL version the FBO makes it
  structurally impossible — but only if opacity is applied at the FBO
  resolve, never inside the scene draw.

- **The test-pattern/calibration overlay (§9 item 4, "no test grid yet") has
  a working, reusable design, even though it's Canvas2D not GL.**
  `output.html`'s `drawTestPattern` draws a border, grid, corner-to-corner
  diagonals, center crosshair, an "OUTPUT N" label, and an 8-color
  reference strip through **the same coordinate-transform function
  (`mapWin`) that draws real content** — so the calibration grid
  automatically respects crop, keystone, and viewport just by construction,
  with no separate math path to keep in sync. The concrete design worth
  porting: calibration overlay and content share one transform function;
  never give the test grid its own coordinate path.

- **Control-plane broadcast rate is a separate variable from render-loop
  rate, and hardcoding one to "look like" the other is a real bug, not a
  theoretical one.** The websocket layer that pushes composite state to
  browser clients (`server.py`'s `broadcaster()`) had its own hardcoded
  ~20Hz `asyncio.sleep`, completely decoupled from the render loop's own
  `--fps`-driven cadence (which was already correct). Result: raising
  `--fps` did nothing observable, because the network layer was silently
  the bottleneck, not the renderer — confusing to debug from the outside
  since nothing errored, it just silently capped. §8's control protocol
  should make this an explicit, named relationship (broadcast rate = f(render
  fps), or intentionally decoupled and documented as such) rather than an
  implicit constant that can drift from whatever the render loop is
  actually doing. Once fixed, the control/preview path (websocket JSON
  state push, browser canvas redraw) sustained ~55 of a 60fps target on
  ordinary laptop hardware for a basic scene — evidence this pipeline
  shape isn't the limiting factor for topology A, and a rough baseline if
  the render host ever reuses the same state-broadcast pattern for its own
  preview link (§8's prototype already does).

- **Edge blending now exists in topology A too, and its config is
  deliberately the same shape as §5's.** `OutputMonitorConfig` gained
  `overlap_left/right/top/bottom` (booleans, which of this output's edges sit
  in a physical overlap) and `ProjectSpec` gained `edge_overlap` (band width
  as a fraction of one output's width) — the same split as renderhost's
  `OutputSpec.left_overlap`/`right_overlap` plus `compute_overlap_layout`'s
  single `overlap_frac`, so a project configured on the laptop carries over
  rather than being re-entered. Two details worth keeping when the GL version
  is built: ticking an edge **widens that output's viewport** by half the
  band (feathering inside the authored crop leaves a dark stripe at the join
  instead of a seamless one), and the ramp is **smoothstep specifically
  because S(t) + S(1-t) = 1**, which is what makes two neighbours' ramps sum
  back to full brightness — measured flat to within 0.4% in the Canvas2D
  implementation. Which edges blend is explicit per output rather than
  inferred from abutting viewports, since projectors are not always adjacent.
  §5's gamma/black-lift stage is NOT implemented here; the browser path stops
  at the geometric ramp, and those belong with real projectors to tune
  against.

- **Measured: the topology-A media path caps out around 4–6 distorted HD
  layers, and the limit is compositing, not decode or bitrate.** Benchmarked
  on a Ryzen 5 5500U with GPU-rasterized Canvas2D (AMD radeonsi via ANGLE),
  1080p output, using the shipping warp code verbatim. Per distorted 1080p
  media layer: ~4.5ms for the 72-triangle mesh alone, ~1.2ms for the
  `<video>`→canvas grab, **~8.8ms for both together** — superadditive,
  because a source canvas rewritten every frame defeats the GPU's texture
  caching. One output: 4 videos 24ms, 6 videos 36ms, 10 videos 84ms. Three
  findings worth carrying forward, because two of them are counter-intuitive
  enough to waste a day on:
  1. **Bitrate is irrelevant to throughput.** The same clip at 25 / 8 /
     4 Mbit/s cost 63.6 / 66.7 / 64.8ms for 8 layers — indistinguishable.
     Re-encoding source media *for performance* buys nothing.
  2. **Decode is not the bottleneck.** 20 concurrent 1080p25 decoders all
     held exactly real time (ratio 1.000); only at 40 did it slip to 0.89.
     Hardware decode is plentiful; the pixel path is what runs out. This is
     evidence for §5's assumption that VA-API decode into a GL texture is
     the cheap part, and the reason the render host should win big here is
     that it replaces the expensive part (a CPU-side triangle mesh) with a
     shader.
  3. **An undistorted quad is ~15× cheaper than a corner-pinned one**
     (~0.1ms vs ~1.55ms), because a parallelogram takes a single-`drawImage`
     fast path instead of the mesh. Irrelevant to the render host, where
     both are one textured quad — but it does mean topology-A cost scales
     with how much *warping* a show uses, not how many shapes it has.

- **Per-output culling is required in topology A and is a non-problem in
  topology B — but only because of §5's FBO ordering.** Because the viewport
  crop is applied as a coordinate remap inside `mapWin`, a shape belonging to
  another projector still ran the entire decode-and-warp path and was
  discarded only by the canvas edge at rasterization time. Fixed by testing
  the shape's corner quad against the window bounds before drawing (safe
  because every media draw path clips back to that quad — "crop" fit,
  polygon masks, and the mirror fold all do), and by *not* touching the
  element cache for culled layers, so the existing eviction sweep releases
  the decoder a few seconds later and the file genuinely stops decoding in
  windows that can't see it. Measured on a simulated 4-output rig, 10 videos
  across a 4-slice canvas: **113.3ms → 23.8ms** worst-case frame, layers
  drawn per output 10 → 3.5. The render host needs no equivalent, because it
  never draws content per output at all — but that is a *consequence* of
  resolving the scene into one FBO first, not a free-standing property, and
  it is lost the moment any content work moves after the per-output split.

- **What is and isn't cheap on a GPU-accelerated 2D path — measured, and
  one intuition that turned out backwards.** Canvas2D here is genuinely GPU
  accelerated (confirmed ANGLE / `radeonsi`), which changes where the cost
  actually sits and is worth knowing before hand-optimising anything in the
  GL renderer:
  1. **Full-surface operations are close to free.** Fixing the opacity bug
     above adds one whole-canvas clear plus one whole-canvas blit per
     translucent layer: **~0.15ms at 1080p**, about 2% of the ~8.8ms a
     distorted 1080p video layer already costs.
  2. **Restricting that clear/blit to the shape's own bounding box made it
     SLOWER.** Interleaved A/B, three runs each, verified pixel-identical
     first: 0.88× / 0.95× / 0.88× at 1920×1080 and 4096×1024, against 1.06×
     in the one case it helped. A whole-surface clear and blit is a path the
     driver does extremely well; a sub-rect `drawImage` plus device-space
     bbox maths is not. **Don't hand-optimise region updates on a GPU path** —
     measure before assuming smaller means faster. The optimisation was
     written, verified correct, measured, and then deleted.

     **Re-confirmed by the feather work (see 5-7).** Bounding a filtered
     draw to its bbox looked like a decisive 4x win — under software
     rasterisation. On the GPU it measured exactly neutral, same as here.
     Two independent attempts, same verdict: **region bounds do not pay on
     this GPU path.**
  3. **The wall is draw-call count, not fill rate.** A distorted shape costs
     ~4.5ms/layer for the mesh alone because it is 72 separate
     clip + transform + `drawImage` calls; the pixels themselves are cheap.
     That is the number a shader removes — one textured quad with a
     projective transform, no mesh, no per-triangle clip. **This, not
     compositing throughput, is the concrete argument for the GL render
     host**, and it means the win there should be large rather than
     incremental. It also means micro-optimising the Canvas2D path is close
     to pointless: nothing short of removing the per-triangle draw calls
     moves that floor.
  4. **Canvas GPU work is ASYNCHRONOUS — time it with a readback or every
     filter looks free.** `performance.now()` around a paint measures command
     *submission*, not execution; in a tight loop the queue runs ahead and a
     4-octave `feTurbulence` clocks the same as a posterize. Force a sync with
     a 1px `getImageData` before stopping the clock. This invalidated a first
     pass of every number below by roughly 10x. Async is a lower bound and
     synced (which serialises work the GPU would otherwise pipeline) is an
     upper bound; the honest signal is the *delta* between variants measured
     the same way.
  5. **Feather and key cost roughly half a millisecond per shape.** Synced,
     8 masked shapes: feather off 5.0ms, feather 12 8.6ms, feather 20 10.3ms
     — about **0.45-0.66ms per feathered shape**. Chroma key over the same
     8: off 4.4ms, keyed 9.8ms — about **0.68ms per keyed shape**, luma
     slightly cheaper. Affordable for a handful of shapes, not free, and
     worth watching if a show feathers or keys most of its layers.
  6. **SVG filter primitives are NOT uniformly accelerated, and two of them
     are traps.** One 960x540 shape, synced, cost over a no-filter baseline:
     `feComponentTransfer` posterize +3.0ms and a luminance duotone +3.4ms;
     RGB-split (offsets + blends) +16.4ms; bloom (`feGaussianBlur` +
     composite) +18.2ms; `feTurbulence`+`feDisplacementMap` +25.2ms at 2
     octaves, +37.6ms at 4. Then the cliff: **`feMorphology` +61.4ms and
     `feConvolveMatrix` +107.6ms** — edge-detect and emboss kernels are a
     CPU fallback in Chromium and are unusable per-frame. A pure-canvas
     pixelate (downscale, redraw with `imageSmoothingEnabled=false`) costs
     2.1ms *total*, cheaper than any filter — not everything wants to be one.

     In the app, synced, 8 shapes: pixelate ~0.95ms/shape, duotone
     ~0.65ms/shape, and the whole tone curve ~0.36ms/shape. That last number
     is the useful one: **solarise, posterise and invert are all per-channel
     1-D curves, so they compose into a single `feComponentTransfer` lookup
     table** and adding solarise+invert on top of posterise costs +0.4ms
     across all 8 shapes rather than another two full primitives.

     The kaleidoscope is the counter-example to item 3 worth knowing: it is
     n clipped `drawImage` calls per shape, yet 8 shapes cost 13.1ms at 2
     segments and only 16.9ms at 10. Draw-call count is the wall when each
     call covers the same area — here each extra wedge covers 1/n of it, so
     total fill is flat and the side-buffer round trip (snapshot, clear,
     blit) is what actually costs. Roughly 0.7ms/shape at 2 segments,
     1.2ms at 10. Posterise
     uses `type="discrete"` and the other two are folded into the band
     *values*, so the bands stay hard however the curve is bent. Any future
     per-channel effect should join that table rather than add a primitive.
  7. **Measure headed, or you are measuring SwiftShader.** Headless
     Chromium falls back to software rendering *even with*
     `--ignore-gpu-blocklist --enable-gpu-rasterization`; only
     `headless=False` on the real display gets the GPU. Confirm with
     `WEBGL_debug_renderer_info`'s `UNMASKED_RENDERER_WEBGL` — "SwiftShader"
     means the numbers are fiction for this purpose. (`chrome://gpu` is not
     reachable from Playwright.) This cost a whole optimisation pass:
     clipping a `ctx.filter` draw to its bounding box is a **4x win in
     software** (193ms -> 45ms per 8 shapes) and **exactly neutral on
     hardware** (0.30ms either way). The clip is kept as cheap insurance for
     any software-rasterised deployment, not because it helps here — which
     is item 2's warning restated: the region bound did nothing on the GPU
     path, again.


- **A server-authoritative playhead is the right model, and the render host
  gets it for free — but "authoritative" must not mean "seek to correct".**
  Topology A publishes one canonical playhead per layer from the compositor
  clock so that every output window shows the same frame either side of a
  projector seam (otherwise independent `<video>` elements drift apart within
  seconds — measured 2.3s between two windows when correction was disabled).
  The trap: correcting drift by seeking is a feedback death spiral, because a
  seek on high-bitrate media stalls decode for 0.5–1.2s, the clock advances
  by that much during the stall, and the resulting drift triggers the next
  seek — measured at 20 seeks/30s on a clip that should never have seeked at
  all. The discriminator that works is *whether the target moved
  discontinuously* (loop wrap, scrub, trim change), never how far the element
  has fallen behind; ordinary drift is corrected by nudging playback rate
  ±12%, which is invisible. And a wrap only warrants a seek if the element
  didn't already follow it — a clip whose trim spans the whole file wraps
  natively and is already in position, so seeking there stalls decode once
  per loop to correct nothing. **Bears directly on §5's libmpv choice:**
  libmpv is being chosen partly *for* its seeking, and the lesson is that
  seeking is the expensive correction, not the cheap one. Prefer native
  looping and rate adjustment; reserve seeks for genuine discontinuities. The
  render host mostly dissolves this problem — one process, one clock, one
  decoder per clip, no cross-window convergence to maintain — which is worth
  stating explicitly as another thing topology B removes rather than
  reimplements.

### Suggested next build steps, in order

1. **Get the actual render-host hardware running the existing prototype
   first**, before writing any new code — topology B is a hardware/OS/
   network problem at this stage, not a rendering one. See the setup
   script referenced in the chat response this section came from
   (`renderhost/setup-render-host.sh`, if present) for the concrete
   install/config steps; it is deliberately conservative about anything
   destructive (network config, systemd units) — those are generated for
   review, not auto-applied.
2. **Real xrandr multi-output span** (§4) on that hardware, then rerun
   `displays.py`/`demo_real_displays.py` against it — the prototype has
   only ever seen 1–2 displays; this is the first real test of the N-output
   path beyond synthetic data.
3. **Decide the Python-vs-native question** (§11) once real video decode
   and/or KMSDRM become the next thing to build — don't decide it
   speculatively before there's a load to measure against. There is now a
   concrete load to aim at, from the topology-A benchmarks above: **10
   distorted 1080p layers across 4 outputs**, which is where the browser path
   collapses (~2fps before culling, and still short of comfortable after).
   That is the target this question should be settled against. Note the
   measurement does *not* itself answer Python-vs-native — it measured
   Canvas2D, and both candidate render hosts are GL. What it does establish
   is that the cost sits in the pixel path rather than in per-frame
   bookkeeping, which is the regime where a handful of Python-issued GL calls
   per frame is least likely to matter. Worth confirming rather than
   assuming, but it shifts the prior toward "Python is fine".
4. Then, roughly by how much venue-load-in pain each removes: test grid +
   overlap-region highlighting in the alignment UI (§9 item 4 finish),
   group-assignment UI, the video layer, mesh warp.
