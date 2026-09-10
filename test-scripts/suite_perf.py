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
REFERENCE = {          # median ms per synced paint of 8 shapes
    "baseline": 5.0,
    "feather": 8.6,
    "key": 9.8,
    "fx-all": 16.4,
    "kaleido-10": 16.9,
}
REGRESSION_FACTOR = 2.5     # generous: this is cross-machine

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

    async def measure(label, pairs, key):
        await set_all(pairs)
        median = (await s.bench())["median"]
        ref = REFERENCE[key]
        rep.note(f"perf/{key}", f"{median:6.2f} ms", f"(reference {ref} ms)")
        if not software:
            rep.check_true(f"perf/{key}/within-budget", median <= ref * REGRESSION_FACTOR,
                           detail=f"{median} ms vs reference {ref} ms",
                           hint=f"more than {REGRESSION_FACTOR}x the reference for {label}. "
                                "Check whether a filtered draw lost its clip — a ctx.filter "
                                "draw costs what its CLIP covers, not what it paints.")

    OFF = [("feather", 0), ("key_mode", "off"), ("mirror_kaleido", 0),
           ("fx_pixelate", 0), ("fx_posterize", 0), ("fx_solarize", 0),
           ("fx_invert", 0), ("fx_duotone", 0)]
    await measure("no effects", OFF, "baseline")
    await measure("feather 12", [("feather", 12)], "feather")
    await measure("chroma key", OFF + [("key_mode", "colour")], "key")
    await measure("all five FX", OFF + [("fx_pixelate", 12), ("fx_posterize", 5),
                                        ("fx_solarize", 0.5), ("fx_invert", 0.3),
                                        ("fx_duotone", 0.8)], "fx-all")
    await measure("kaleidoscope 10", OFF + [("mirror_kaleido", 10)], "kaleido-10")
