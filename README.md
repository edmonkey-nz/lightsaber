# Lightsaber

![version](https://img.shields.io/badge/version-0.39.0-33e0d0)
![status](https://img.shields.io/badge/status-pre--release-orange)
![platform](https://img.shields.io/badge/platform-Ubuntu-informational)
![ai-code](https://img.shields.io/badge/AI%20coded-YES-orange)

**A live visuals toolkit for projection mapping and VJing.**

Build a canvas out of shapes, fill each one with video, images, a webcam,
text or an audio-responsive visualiser, and corner-pin it onto whatever you're
projecting on. A Python backend keeps time and state; the browser draws.

It runs on one computer driving **one output**. For several projectors the
direction is a separate **render host**: a second machine with a multi-output
GPU, controlled from the laptop over a direct LAN cable (prototype in
`renderhost/`).

> **Pre-release.** Version stays 0.x until things settle; project and canvas
> JSON may still change between minor versions. See
> [CHANGELOG.md](CHANGELOG.md).

---

## What you can do with it

- **Corner-pin every shape** to line up with a real surface — a wall panel, a
  pillar, an odd-shaped screen.
- **Fill shapes with** video files, images, a live webcam, text, or one of six
  **audio-responsive visualisers**: starfield, dreamy clouds, turbulence, oil
  slick, ink in water and aurora. They react to a live mic input, and run
  fine without one.
- **Mask a shape** to a circle, hexagon, triangle, square, or a custom polygon
  you draw yourself, with an optional feathered edge. Custom masks warp along
  with the corner pins, so the silhouette keeps fitting the surface.
- **Cut holes** with cutouts, which either black out whatever they overlap or
  punch through it so the shapes behind show through.
- **Key out** green-screen or black backgrounds (chroma and luma key), and
  treat any shape with colour filters, pixelate, posterise, solarise, invert,
  duotone, mirror folds and a kaleidoscope.
- **Modulate anything** — position, scale, rotation, hue, brightness, opacity —
  from LFOs or a live audio input.
- **Sequence canvases** into a set, with per-step durations and crossfades.
- **Trim long videos** once and reuse the same clip several times, each
  instance with its own in/out and playhead.
- **Slice a wide canvas** into per-projector crops with flip, keystone and
  edge blending — for setting up a multi-projector show, which plays on the
  render host.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

```bash
python run.py --web --open default
```

This starts the server and opens the editor in a Chrome of its own, once the
server is answering. (`python run.py --web` alone starts just the server; then
open <http://localhost:8080>.)

That Chrome uses a separate profile (`~/.config/lightsaber-chrome`), so it runs
alongside your everyday Chrome. It stays open if you stop or restart the
server and reconnects by itself — and so do the output windows, which keep
playing video meanwhile. Ctrl+C stops the server only.

Open an output with the **Output** button in the header, drag it to the
projector and press F11.

### Show-night checklist

- **One output window.** Each output window repeats all of the video work for
  its own slice, so the same canvas ran three times faster as one window than
  as three.
- **Turn off the editor's live preview** (the screen icon in the canvas
  toolbar). The editor is a full renderer in its own right, with its own copy
  of every video; off, it draws outlines only.
- **Check the GPU dot** in the header: it shows which GPU each output window
  got (green dedicated, amber integrated, red software rendering).
- **Keep the project frame rate at what the content needs** — 30 is plenty for
  25fps footage.
- **Plug the laptop in.**

On a hybrid NVIDIA laptop, `--open nvidia` asks for the NVIDIA card. In a
Wayland session it falls back to the default GPU, because Chrome there can't
display video on the NVIDIA card. See
[TECHNICAL.md](docs/TECHNICAL.md#performance) for the details and measurements.

## Interface

**Project** — the top-level container for a show or venue. Owns its own
canvases and sequence, its output resolution, frame rate and outputs.
Uploaded media and media folders are shared across all projects.

**Canvas** — an arrangement of shapes. This is what you compose.

**Shape** — a four-corner quad you drag-warp into position, holding one piece
of content. Each shape has its own opacity, colour treatment, mask, paint
order, and can be locked so a stray click can't nudge it mid-set.

**Input rail** — the strip on the left of the canvas: video and images,
webcams, text, visualisers and cutouts. Each adds a shape.

**Visualisers** — generated fills drawn by GPU shaders. Each has its own
sliders in Shape settings (double-click one to reset it), including how
strongly it reacts to bass, mids, highs or the overall level. Start the mic
in the Effectors pane to make them react.

**Media** — every file the project can draw from. Videos and images can be
*linked* where they already live rather than copied, so a 4GB video costs no
extra disk. Trim a long recording once and every shape using it inherits
that trim.

**Sequencer** — plays saved canvases in order with per-step durations. This
is what the outputs follow during a set.

**Effectors** — a modulation matrix. Wire an LFO or a live audio band to a
shape's position, scale, rotation, hue, brightness or opacity.

**Outputs** — one browser window per screen, with its own flip, keystone and
(optionally) its own crop of a wider canvas.

## Status and direction

Working today: canvas/shape editing with corner-pin warping, masks and
cutouts, video/image/webcam/text content, six audio-responsive visualisers,
keying and FX, linked media with per-instance trimming, effectors, the
sequencer, and per-output correction and viewport slicing.

Headed toward **dedicated multi-projector output** — a separate render host
driving several projectors from one wide framebuffer, decoding each clip once
rather than once per output window, with edge blending and mesh warping. A
prototype lives in `renderhost/`; the data model here is meant to grow into it
rather than be replaced.

Also on the list: more visualisers, beat detection, and bezier masks.

Generated vector scenes (and the Claude scene director that composed them)
were removed in 0.39.0: lightsaber is a video and projection tool, and that
path cost more than everything else on the server combined.

## Related project

Lightsaber began as a fork of
[promptwaver](https://github.com/edmonkey-nz/promptwaver), which drives a
vector laser rather than projectors. They no longer share an engine; the odd
UI or server fix still crosses over by hand — see
[TECHNICAL.md](docs/TECHNICAL.md#relationship-to-promptwaver).

## Development

Opens straight into VSCode — `.vscode/` points at `.venv` and has F5-ready
configs; `pyproject.toml` holds Ruff/Black config (line length 100).

Regression suites drive the real app in a real browser on a throwaway sandbox:
`python test-scripts/run.py` (see [test-scripts/README.md](test-scripts/README.md)).

MIT — see [LICENSE](LICENSE).
