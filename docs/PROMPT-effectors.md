# Build task: effector / modulation system

Read `ARCHITECTURE.md` first. This feature lives in the scene layer, upstream of the FBO — it does not touch the correction chain.

## Concept

A **modulation matrix**, in the synth sense. Sources produce a continuously varying value; routes connect a source to a shape parameter with a depth. Nothing about a source knows what it drives, and nothing about a target knows what drives it. Adding a new source type must not require touching the routing code.

## Sources

All sources emit a normalised value each frame, computed from the master clock. Convention: **bipolar sources emit −1..1, unipolar emit 0..1**, declared by the source type. Never leak raw units (Hz, dB, FFT bins) past the source boundary.

Implement three types:

**`lfo`** — shape: `sine | triangle | saw | ramp | square | sample_hold`. Params: rate, phase offset, pulse width (square only). Rate is either free-running Hz or tempo-synced note division (`1/1`, `1/2`, `1/4`, `1/8`, `1/8T`, `1/16`). Bipolar.

**`audio`** — microphone input, FFT, three bands with configurable crossover frequencies (defaults ~200Hz and ~2kHz). Each band is a separate selectable source. Unipolar.

Practical requirements — raw FFT magnitude is unusable without these:
- Per-band **attack/release smoothing** (separate coefficients; fast attack, slow release is the useful default)
- **Running normalisation** against a rolling peak so it responds to quiet and loud rooms alike, with a floor to stop it amplifying silence into noise
- Log-weighted band summing, not linear bin averaging
- Input device selection, and a visible input meter so a dead mic is obvious immediately

**`steps`** — a step sequencer, which is also how the arpeggiator works. An array of values (default 8, resizable), advanced at a rate using the same rate model as the LFO. Modes: `forward | reverse | pingpong | random`. Optional glide between steps. Unipolar.

## Routes

```
route: { source_id, target: "shape.param", depth, offset, curve, mode }
```

- `depth` bipolar, so a route can invert
- `curve`: `linear | exponential | logarithmic`
- `mode`: `add | multiply | replace`
- **Multiple routes to the same target sum** (in declaration order, `replace` wins last)
- Targets are addressed by path string and resolved through a registry, so new modulatable params need no routing changes

Targets to expose initially: position x/y, scale, rotation, hue, saturation, brightness, opacity, stroke width.

## Evaluation

Once per frame, in this order: advance clock → evaluate all sources → resolve routes → apply to shape params → render.

**Sources must be a pure function of time**, not of accumulated frame deltas. Same timestamp must produce the same value. This preserves determinism (required by `ARCHITECTURE.md` §5) and means a dropped frame causes no drift. `sample_hold` and `random` step modes therefore need a seeded PRNG keyed on step index, not `Math.random()`.

## UI

Part of the existing browser control surface.

- **Source list** — add/remove/configure, each with a **live animated meter** showing its current output. This is the single most important debugging affordance; without it, diagnosing a silent route is guesswork.
- **Route list** — source dropdown, target dropdown, depth slider, curve, mode. Inline, compact, add/remove per row.
- **Step editor** — draggable bar-graph for the `steps` source, editable while running.
- **Tempo** — global BPM, with tap-tempo. All synced rates derive from it.
- Changing anything takes effect immediately; no apply button.

## Persistence

Sources and routes are **creative config** — they save into the show file, not the profile. See `ARCHITECTURE.md` §6.

## Scope

Do not build: MIDI input, envelope followers, per-shape effect chains, or preset morphing. Structure the source registry so those are additions later, not rewrites.
