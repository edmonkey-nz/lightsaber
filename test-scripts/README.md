# Render regression scripts

Checks that the compositing features still do what they did, by driving the
real app in a real browser and reading real pixels. There is no unit-test layer
under this — the things that break here are render paths, and a render path can
only be checked by rendering.

```bash
.venv/bin/python -m pip install playwright        # test-only, not in requirements.txt
.venv/bin/python -m playwright install chromium

.venv/bin/python test-scripts/run.py              # all suites
.venv/bin/python test-scripts/run.py --suite render
.venv/bin/python test-scripts/run.py --json results.json
```

A window opens and drives itself for about a minute. Exit code is non-zero if
anything failed, so it drops straight into a shell `&&` chain or a hook.

## Suites

| suite | ~time | what it covers |
|---|---|---|
| `render` | 40s | punch-through depths, chroma/luma key, all five FX, feather ramps, kaleidoscope symmetry, and one check in the real output window |
| `ui` | 20s | every pane exists and is in the right order; controls show the right rows for the selected shape; each control's value actually reaches the server |
| `perf` | 20s | synced frame cost for feather, key, FX and kaleidoscope against reference numbers |

## Reading the output

Every check prints what it got. A failure also prints what was expected and a
hint naming the likely cause and where to look:

```
  FAIL render/key/chroma-keeps-foreground        [0, 0, 0]
       expected: red
       hint:     non-key colours must survive with their colour intact. Black
                 here means the colour branch had its alpha driven to 0 — the
                 pipeline is premultiplied.
```

That hint is the point of the whole thing. If you are handing results to
someone — or to Claude — paste the failing block verbatim; it carries the
symptom, the expectation and the first place to look. `--json` writes the same
records for a tool to read.

`perf` lines are notes, not assertions, except for a `within-budget` check that
only fires at 2.5x the reference. The references were measured on one machine
(named in the output) so they are a tripwire for "this got dramatically worse",
not a spec.

## Three things the harness handles that are easy to get wrong by hand

- **It never touches your work.** `run.py` resolves `scenes/`, `projects/`,
  `media/` and `settings.json` relative to the repo root, so a test server
  started in the repo would read and write your real projects. The harness
  builds a throwaway copy in a temp dir with generated fixtures, on a free
  port, and deletes it afterwards (`--keep` to inspect it).
- **It clears the canvas between suites.** The server holds canvas state in
  memory for its whole life, so shapes accumulate and sit under the sample
  points, quietly changing the answer. This has produced two separate
  false diagnoses.
- **It syncs the GPU before timing, and runs headed.** Canvas work is
  asynchronous — timing a paint measures command submission, not execution, and
  every filter looks free. And headless Chromium is SwiftShader even with the
  GPU flags, so headless timings are software and about ten times too slow.
  `--headless` works but skips the perf comparisons and says so.

## Adding a check

Suites are plain async functions taking a `Session` and a `Report`:

```python
async def run(s, rep):
    await s.reset_canvas()
    shot = await s.add_shape({"source_type": "media", "media": "greenred.png"}, FULL_QUAD)
    await s.start()
    await s.set(shot, "key_mode", "colour")
    rep.check("render/key/removes-key-colour", nearest_name(await s.rgb(-0.5)), "blue",
              hint="where to look when this breaks")
```

`Session` gives you `reset_canvas`, `add_shape`, `set`, `start`, `pixel`/`rgb`
(canvas coordinates, `[-1,1]` with `+y` up), `ramp_width`, `bench` and
`open_output`. `Report` gives you `check` (with an optional `tolerance`),
`check_true` and `note`. Fixtures are solid-colour and gradient PNGs generated
by the harness — `harness.FIXTURES` lists them; add one there rather than
committing a binary.

Always give a `hint`. A bare FAIL costs whoever reads it the same debugging you
have already done.

## Render host

The runner assumes the browser app. When `renderhost/` grows past a prototype
it wants its own suite next to these — the correction chain (crop, corner-pin,
blend ramp) is testable the same way, by rendering known input and sampling
output, and the reporting here is deliberately transport-agnostic.
