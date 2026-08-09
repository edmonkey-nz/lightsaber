# Lightsaber

![version](https://img.shields.io/badge/version-0.33.0-33e0d0)
![status](https://img.shields.io/badge/status-pre--release-orange)
![platform](https://img.shields.io/badge/platform-Ubuntu-informational)

**A live visuals instrument for projectors and screens.**

You build a canvas out of shapes, fill each one with something — a procedural
3D scene, a video, a webcam, some text — warp it to fit whatever you're
projecting onto, and send the result out to as many screens as you have.
It runs entirely in the browser.

> **Pre-release.** Version stays 0.x until things settle; scene and canvas
> JSON may still change between minor versions. See
> [CHANGELOG.md](CHANGELOG.md).

---

## The idea

Most visual tools give you clips to play. Lightsaber gives you *scenes* —
procedural, generative geometry that runs live and never loops.

You describe one in words. Claude writes it as a scene file. From then on it
renders locally at full frame rate, forever, with no network involved.

```
  "aurora over a still lake"
            │
            ▼
      Claude writes a scene   ── once, then cached to disk
            │
            ▼
   ┌────────────────────────┐
   │  a canvas of shapes    │   each shape holds a scene, video,
   │  ┌────┐   ┌────┐       │   webcam, text, or acts as a cutout
   │  │ 1  │   │ 2  │       │   — and is corner-pinned to fit the
   │  └────┘   └────┘       │   surface you're projecting onto
   └────────────────────────┘
            │
            ▼
     Output windows  ──▶  projector 1, projector 2, …
```

An **Output** is just a chrome-less browser window you drag onto a projector.
There's nothing to install on the output machine.

## What you can do with it

- **Corner-pin every shape** to line up with a real surface — a wall panel, a
  pillar, an odd-shaped screen.
- **Mask a shape** to a circle, hexagon, triangle, square, or a custom polygon
  you draw yourself. Custom masks warp along with the corner pins, so the
  silhouette keeps fitting the surface.
- **Mix content types** on one canvas: generated scenes, video files, live
  webcam, text, and hard-edged cutouts that black out whatever they overlap.
- **Drive one wide canvas across several projectors**, each showing its own
  slice, with per-output flip and keystone correction.
- **Modulate anything** — position, scale, rotation, hue, brightness, opacity
  — from LFOs or a live audio input.
- **Sequence canvases** into a set, with per-step durations and crossfades.
- **Trim long videos** once and reuse the same clip several times, each
  instance with its own in/out and playhead.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # numpy + aiohttp
pip install anthropic                # optional — only for generating scenes
```

## Run

```bash
python run.py --web
```

Then open <http://localhost:8080>.

To generate scenes you'll need a Claude API key — either set
`ANTHROPIC_API_KEY` before launching, or paste it into Settings in the app.
Without one, everything else still works and a local fallback stands in for
the scene director.

## First five minutes

1. You land in a project with one empty shape on the canvas.
2. Open **Input › Scene library** and either generate a scene from a
   description, or pick one that ships with the app.
3. Click a scene to drop it onto the selected shape.
4. Drag the shape's corners to warp it into place. Add more shapes with
   **+ Add shape**.
5. Hit **▶ Start**, then open **Output 1** from the header and drag that
   window onto your projector.

That's the whole loop. Everything else is refinement.

## The pieces

**Project** — the top-level container for a show or venue. Owns its own
canvases and sequence, its output resolution, and how many outputs it drives.
Scenes and media are shared across all projects.

**Canvas** — an arrangement of shapes. This is what you compose.

**Shape** — a four-corner quad you drag-warp into position, holding one piece
of content. Each shape has its own opacity, colour treatment, mask, paint
order, and can be locked so a stray click can't nudge it mid-set.

**Media** — every file the project can draw from. Videos and images can be
*linked* where they already live rather than copied, so a 4GB video costs no
extra disk. Trim a long recording once and every shape using it inherits
that trim.

**Sequencer** — plays saved canvases in order with per-step durations. This
is what the outputs follow during a set.

**Effectors** — a modulation matrix. Wire an LFO or a live audio band to a
shape's position, scale, rotation, hue, brightness or opacity.

**Outputs** — one browser window per screen. Each has its own flip, keystone
and (optionally) its own crop of a wider canvas, so several projectors can
share one continuous image.

## About generating scenes

Generating a scene is a single API call. After that the scene is a local
file that renders without touching the network, so a two-hour set costs
nothing beyond the scenes you made.

Scenes cost roughly **5–40 cents (NZD) each** depending on size, detail and
model. A $5 credit goes a long way unless you lean on the larger models.

Claude doesn't pick from a fixed list of objects — it authors the geometry
itself, in a small shape grammar the app interprets. That's why the range of
possible scenes isn't bounded by anything hard-coded. See
[TECHNICAL.md](TECHNICAL.md#claude-authors-the-geometry) for how it works.

## Going deeper

[**TECHNICAL.md**](TECHNICAL.md) covers the architecture, the render model,
the scene format, writing your own generators, the shape grammar, media
internals and security, effector routing, output correction, and cost
control.

## Status and direction

Working today: scene generation, canvas/shape editing with corner-pin warping
and masks, video/image/webcam/text content, linked media with per-instance
trimming, effectors, the sequencer, and multi-output with per-output
correction and viewport slicing.

Headed toward **dedicated multi-projector output** — a separate render host
driving several projectors from one wide framebuffer, with edge blending and
mesh warping. A prototype lives in `renderhost/`. The data model here is
meant to grow into it rather than be replaced.

Also on the list: more generators and post-processing, a UI for per-shape
modulation routes, richer camera paths, and moving the API key out of a
plaintext settings file before any public release.

## Development

Opens straight into VSCode — `.vscode/` points at `.venv` and has F5-ready
configs; `pyproject.toml` holds Ruff/Black config (line length 100).

Lightsaber is a visuals-only fork of
[PromptWaver](https://github.com/edmonkey-nz/promptwaver), which pairs the
same engine with a polyphonic ambient synth. This fork dropped the audio
layer, and later the physical laser/DAC output it originally targeted, to
focus on projector-driven visuals.

MIT — see [LICENSE](LICENSE).
