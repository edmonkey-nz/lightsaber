"""Lightsaber — a live canvas/shape visual instrument for browser-driven
monitor and projector output.

An ambient scene sculptor: procedural vector visuals composited from
scene-filled shapes on a canvas (see canvases.py/composite.py), driven by a
per-shape modulation matrix of LFOs and envelopes. Claude acts as an offline
*scene director* (keyword -> scene spec); the local renderer runs at full
framerate with no further API calls. Projects (see projects.py) are the
base-level container, each owning its own canvases and sequence.
"""

__version__ = "0.34.0"
