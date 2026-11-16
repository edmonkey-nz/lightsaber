"""Frame-cost measurements for the compositing features.

Two rules this suite exists to enforce, both learned the hard way:

* **Sync the GPU.** Canvas work is asynchronous, so timing a paint measures
  command submission, not execution — every filter looks free and the numbers
  come out roughly ten times too low. `Session.bench` reads back a pixel to
  stall until the work is done.
* **Run headed.** Headless Chromium is SwiftShader even with the GPU flags.
  The suite refuses to compare against the reference numbers when it detects a
  software renderer, because they would be meaningless.

REFERENCE is what this cost on the machine named beside it. It is a tripwire
for "this got dramatically worse", not a spec — a different GPU will differ,
so only a large multiple is treated as a regression.
"""

from harness import FULL_QUAD

REFERENCE_MACHINE = "Ryzen 5 5500U / AMD Radeon (radeonsi renoir), 8 shapes, 1600x1000 editor"

# Cost of each feature as a DELTA over a baseline measured moments earlier in
# the same run — not an absolute time. Absolute times move with machine load
# (a busy laptop tripled the baseline here and failed a budget that was fine),
# and a regression check that cries wolf is one people learn to ignore. The
# delta cancels load, browser state and GPU generation, and is what actually
# regresses when a filtered draw loses its clip.
REFERENCE_DELTA_MS = {
    "feather": 3.6,
    "key": 6.0,
    "fx-all": 10.5,
    "kaleido-10": 5.0,
}
REGRESSION_FACTOR = 3.0     # generous: this is still cross-machine

SHAPES = 8


async def run(s, rep):
    renderer = await s.renderer()
    software = renderer.startswith("SOFTWARE")
    rep.note("perf/renderer", renderer)
    if software:
        rep.note("perf/comparison", "SKIPPED",
                 "software renderer — timings are not comparable to the reference")
    rep.note("perf/reference-machine", REFERENCE_MACHINE)

    await s.reset_canvas()
    ids = []
    for i in range(SHAPES):
        cx = -0.75 + 0.5 * (i % 4)
        cy = 0.5 - 1.0 * (i // 4)
        quad = [[cx - 0.22, cy + 0.22], [cx + 0.22, cy + 0.22],
                [cx + 0.22, cy - 0.22], [cx - 0.22, cy - 0.22]]
        ids.append(await s.add_shape(
            {"source_type": "media", "media": "greenred.png", "z_index": i + 1,
             "clip_shape": "circle"}, quad))
    await s.start()

    async def set_all(pairs):
        for pid in ids:
            for k, v in pairs:
                await s.set(pid, k, v, settle=0)
        await s.page.wait_for_timeout(900)

    OFF = [("feather", 0), ("key_mode", "off"), ("mirror_kaleido", 0),
           ("fx_pixelate", 0), ("fx_posterize", 0), ("fx_solarize", 0),
           ("fx_invert", 0), ("fx_duotone", 0)]

    await set_all(OFF)
    baseline = (await s.bench())["median"]
    rep.note("perf/baseline", f"{baseline:6.2f} ms", "(the yardstick; not itself budgeted)")

    async def measure(label, pairs, key):
        await set_all(OFF + pairs)
        median = (await s.bench())["median"]
        delta = round(median - baseline, 2)
        ref = REFERENCE_DELTA_MS[key]
        rep.note(f"perf/{key}", f"{median:6.2f} ms", f"(+{delta} over baseline; reference +{ref})")
        if not software:
            rep.check_true(f"perf/{key}/within-budget", delta <= ref * REGRESSION_FACTOR,
                           detail=f"+{delta} ms vs reference +{ref} ms",
                           hint=f"{label} costs more than {REGRESSION_FACTOR}x its reference "
                                "delta. Check whether a filtered draw lost its clip — a "
                                "ctx.filter draw costs what its CLIP covers, not what it paints.")

    await measure("feather 12", [("feather", 12)], "feather")
    await measure("chroma key", [("key_mode", "colour")], "key")
    await measure("all five FX", [("fx_pixelate", 12), ("fx_posterize", 5),
                                  ("fx_solarize", 0.5), ("fx_invert", 0.3),
                                  ("fx_duotone", 0.8)], "fx-all")
    await measure("kaleidoscope 10", [("mirror_kaleido", 10)], "kaleido-10")
