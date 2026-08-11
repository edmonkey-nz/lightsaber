# Lightsaber

![version](https://img.shields.io/badge/version-0.36.0-33e0d0)
![status](https://img.shields.io/badge/status-pre--release-orange)
![platform](https://img.shields.io/badge/platform-Ubuntu-informational)

**A live visuals toolkit for projection mapping, multiple projectors and live VJing** 

Python backend + web browser for interface runs on a single computer for simple single/dual output. Or uses a master/slave approach with a second computer with quad-output and a 'render host' engine, controlled from the master computer over direct LAN cable between computers.

> **Pre-release.** Version stays 0.x until things settle; scene and canvas
> JSON may still change between minor versions. See
> [CHANGELOG.md](CHANGELOG.md).

---

## What you can do with it

- **Corner-pin every shape** to line up with a real surface — a wall panel, a
  pillar, an odd-shaped screen.
- **Mask a shape** to a circle, hexagon, triangle, square, or a custom polygon
  you draw yourself. Custom masks warp along with the corner pins, so the silhouette keeps fitting the surface.
- **Mix content types** on one canvas: generated scenes, video files, live webcam, text, and hard-edged cutouts that black out whatever they overlap.
- **Drive one wide canvas across several projectors**, each showing its own slice, with per-output flip and keystone correction.
- **Modulate anything** — position, scale, rotation, hue, brightness, opacity — from LFOs or a live audio input.
- **Sequence canvases** into a set, with per-step durations and crossfades.
- **Trim long videos** once and reuse the same clip several times, each instance with its own in/out and playhead.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

A Claude API key is optional — without one, scene generation falls back to a
local keyword mapping and everything else works unchanged.

## Run

```bash
python run.py --web
```

Then open <http://localhost:8080>.

To generate scenes you'll need a Claude API key — either set
`ANTHROPIC_API_KEY` before launching, or paste it into Settings in the app.
Without one, everything else still works and a local fallback stands in for
the scene director.

## Interface

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


MIT — see [LICENSE](LICENSE).
