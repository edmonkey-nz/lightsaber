"""The effector / modulation matrix — PROMPT-effectors.md's concept, scoped
to a first slice: sources produce a continuously varying value; routes
connect a source to a shape's own display param (the overrides composite.py
already reads for hue/saturation/brightness/opacity — see composite.py's
_poly_layer_base/set_polygon) with a depth, curve and mode. Nothing about a
source knows what it drives, and nothing about a target knows what drives
it — adding a new source type doesn't touch routing.

Lives at the CANVAS level (see canvases.py's CanvasSpec.effectors), not the
scene: a route targets a specific polygon + param on THIS canvas, the same
place Monitor filters' brightness/hue/etc already live, whereas
SceneSpec.modulation (scenes.py) is a different, older mechanism entirely —
per-scene-generator-param modulation baked into a scene's own JSON, unrelated
to this.

Scope (see PROMPT-effectors.md's own precedent for phased scope): `lfo` and
`audio` sources are implemented; `steps` (step sequencer / arpeggiator) is
NOT — same spirit as the prompt's own deferred list (MIDI, envelope
followers, per-shape effect chains, preset morphing), just extended by one
implementation pass. Routing targets: hue/saturation/brightness/opacity
(the params Monitor filters already exposes as plain overrides) plus
position_x/position_y/scale/rotation (a delta applied around a chosen
anchor point — see transform_corners/ANCHOR_FRACTIONS below; a polygon's
geometry is just four corners with no decomposed transform basis, so these
are pivot-relative deltas, not absolute values). stroke-width isn't
modulatable yet — no per-stroke-width override exists to hook into.

Determinism (ARCHITECTURE.md §5): every `lfo` source is a pure function of
a single timestamp `t`, never of accumulated per-tick deltas — the render
loop already computes `t` this way for every scene slot (see composite.py's
_loop), so plugging in here doesn't change that discipline. `sample_hold`
needs a value that's constant within one LFO cycle but changes between
cycles unpredictably — computed from a seeded PRNG keyed on the cycle
INDEX (`random.Random(seed)`, a fresh instance per call), never bare
`random.random()`, so the same cycle always reproduces the same value
regardless of how many ticks landed in it or whether one was dropped.
`audio` sources are the deliberate exception: a live mic signal was never a
function of time to begin with (see EffectorSource.sample's own comment on
this) — the browser streams already-smoothed/normalised band values up via
composite.py's set_audio_bands, and a source of type "audio" just reads
whichever one it's configured for.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field

# Tempo-synced rates (rate = beats-per-second / beats-per-cycle): how many
# beats one full LFO cycle spans, at the current global BPM.
_SYNC_BEATS = {
    "1/1": 4.0, "1/2": 2.0, "1/4": 1.0, "1/8": 0.5, "1/8T": 1.0 / 3.0, "1/16": 0.25,
}

LFO_SHAPES = ("sine", "triangle", "saw", "ramp", "square", "sample_hold")
CURVES = ("linear", "exponential", "logarithmic")
MODES = ("add", "multiply", "replace")
# Params Monitor filters already exposes as a plain per-polygon override —
# see composite.py's set_polygon / _poly_layer_base and the two HTML files'
# Monitor filters panel.
FILTER_TARGET_PARAMS = ("hue", "saturation", "brightness", "opacity")
# Geometry targets — a polygon is just four corners with no decomposed
# position/scale/rotation basis, so these are deltas applied around a
# chosen ANCHOR point (see transform_corners/ANCHOR_FRACTIONS below), not
# absolute values: position_x/position_y default 0 (no offset), scale
# defaults 1 (no change), rotation defaults 0 (degrees).
GEOMETRY_TARGET_PARAMS = ("position_x", "position_y", "scale", "rotation")
TARGET_PARAMS = FILTER_TARGET_PARAMS + GEOMETRY_TARGET_PARAMS

# The 3x3 anchor grid (PolygonSpec.transform_anchor) — where position/scale/
# rotation modulation pivots from, as a fraction of the polygon's own
# axis-aligned bounding box. y=1 is the TOP on purpose: corners are stored
# clockwise from top-left with +y up (see canvases.py's DEFAULT_CORNERS
# comment), not screen-space +y-down.
ANCHOR_POINTS = (
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)
ANCHOR_FRACTIONS = {
    "top-left": (0.0, 1.0), "top-center": (0.5, 1.0), "top-right": (1.0, 1.0),
    "middle-left": (0.0, 0.5), "center": (0.5, 0.5), "middle-right": (1.0, 0.5),
    "bottom-left": (0.0, 0.0), "bottom-center": (0.5, 0.0), "bottom-right": (1.0, 0.0),
}


def transform_corners(corners: list, anchor: str, dx: float, dy: float,
                       scale: float, rotation_deg: float) -> list:
    """Applies a position/scale/rotation delta to `corners` (4 [x,y] pairs,
    normalised canvas space) around the chosen anchor point — the anchor
    itself is computed fresh from `corners`' own axis-aligned bounding box
    every call, not stored, so it stays correct as corners are dragged by
    hand. Pure geometry, no clamping — composite.py's caller is expected to
    skip calling this entirely when (dx,dy,scale,rotation_deg) is the
    neutral (0,0,1,0), the common case with no geometry routes."""
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    fx, fy = ANCHOR_FRACTIONS.get(anchor, (0.5, 0.5))
    ax = min_x + fx * (max_x - min_x)
    ay = min_y + fy * (max_y - min_y)
    rad = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    out = []
    for x, y in corners:
        rx, ry = (x - ax) * scale, (y - ay) * scale
        rx, ry = rx * cos_r - ry * sin_r, rx * sin_r + ry * cos_r
        out.append([ax + rx + dx, ay + ry + dy])
    return out


def _apply_curve(x: float, curve: str) -> float:
    """Reshapes a -1..1 (or 0..1) source value's response — sign-preserving,
    so it works the same for bipolar and unipolar sources without needing to
    know which. linear = identity (the common case, cheap to skip)."""
    if curve == "exponential":
        return math.copysign(x * x, x)
    if curve == "logarithmic":
        return math.copysign(math.sqrt(abs(x)), x)
    return x


SOURCE_TYPES = ("lfo", "audio")
AUDIO_BANDS = ("low", "mid", "high", "master")   # "master" = the combined/overall level (see composite.py's set_audio_bands)
# Fixed, always-resolvable route targets for the 3 EQ bands + master level
# (2) — lets a route reference audio directly (`source_id` = one of these),
# without first adding and configuring an "audio"-type EffectorSource card.
# A real audio-type source (via `band` above) still works too — this is
# purely a friction-reducing shortcut, not a replacement.
AUDIO_PSEUDO_SOURCE_IDS = {
    "low": "__audio_low__", "mid": "__audio_mid__",
    "high": "__audio_high__", "master": "__audio_master__",
}


@dataclass
class EffectorSource:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "LFO"
    type: str = "lfo"          # SOURCE_TYPES
    shape: str = "sine"        # LFO_SHAPES, meaningful iff type=="lfo"
    rate_hz: float = 0.2       # used when `sync` is None (free-running)
    sync: str | None = None    # one of _SYNC_BEATS' keys, or None for free-running Hz
    phase: float = 0.0         # 0..1
    pulse_width: float = 0.5   # square only, 0..1
    band: str = "low"          # AUDIO_BANDS, meaningful iff type=="audio"

    def _rate(self, bpm: float) -> float:
        if self.sync and self.sync in _SYNC_BEATS:
            return (bpm / 60.0) / _SYNC_BEATS[self.sync]
        return self.rate_hz

    def sample(self, t: float, bpm: float, audio_bands: dict | None = None) -> float:
        """LFO: bipolar -1..1, pure function of (t, bpm) — see module
        docstring. Audio: unipolar 0..1, NOT a function of t at all — it's a
        live external signal (see composite.py's set_audio_bands), the same
        category modulation.py's own `Value` class already models for
        externally-driven values. The determinism requirement in the module
        docstring is specifically about sources that claim to be a pure
        function of time; a live mic input never was one, same as the spec's
        own framing ("microphone input") assumes."""
        if self.type == "audio":
            return (audio_bands or {}).get(self.band, 0.0)
        rate = self._rate(bpm)
        cycle = t * rate + self.phase
        x = cycle % 1.0
        if self.shape == "sine":
            return math.sin(2 * math.pi * x)
        if self.shape == "triangle":
            return 4 * abs(x - 0.5) - 1
        if self.shape == "saw":
            return 2 * x - 1
        if self.shape == "ramp":
            return 1 - 2 * x
        if self.shape == "square":
            return 1.0 if x < max(0.001, min(0.999, self.pulse_width)) else -1.0
        if self.shape == "sample_hold":
            cycle_index = math.floor(cycle)
            return random.Random(f"{self.id}:{cycle_index}").uniform(-1.0, 1.0)
        return 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.type, "shape": self.shape,
            "rate_hz": self.rate_hz, "sync": self.sync, "phase": self.phase,
            "pulse_width": self.pulse_width, "band": self.band,
        }

    @staticmethod
    def from_dict(d: dict) -> "EffectorSource":
        return EffectorSource(
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=str(d.get("name", "LFO")),
            type=d.get("type") if d.get("type") in SOURCE_TYPES else "lfo",
            shape=d.get("shape") if d.get("shape") in LFO_SHAPES else "sine",
            rate_hz=max(0.001, min(50.0, float(d.get("rate_hz", 0.2)))),
            sync=d.get("sync") if d.get("sync") in _SYNC_BEATS else None,
            phase=float(d.get("phase", 0.0)) % 1.0,
            pulse_width=max(0.01, min(0.99, float(d.get("pulse_width", 0.5)))),
            band=d.get("band") if d.get("band") in AUDIO_BANDS else "low",
        )


@dataclass
class EffectorRoute:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_id: str = ""
    target_poly: str = ""      # a PolygonSpec.id on this canvas
    target_param: str = "hue"  # TARGET_PARAMS
    depth: float = 1.0         # bipolar — negative inverts
    offset: float = 0.0
    curve: str = "linear"      # CURVES
    mode: str = "add"          # MODES

    def to_dict(self) -> dict:
        return {
            "id": self.id, "source_id": self.source_id, "target_poly": self.target_poly,
            "target_param": self.target_param, "depth": self.depth, "offset": self.offset,
            "curve": self.curve, "mode": self.mode,
        }

    @staticmethod
    def from_dict(d: dict) -> "EffectorRoute":
        return EffectorRoute(
            id=d.get("id") or uuid.uuid4().hex[:8],
            source_id=str(d.get("source_id", "")),
            target_poly=str(d.get("target_poly", "")),
            target_param=d.get("target_param") if d.get("target_param") in TARGET_PARAMS else "hue",
            depth=float(d.get("depth", 1.0)),
            offset=float(d.get("offset", 0.0)),
            curve=d.get("curve") if d.get("curve") in CURVES else "linear",
            mode=d.get("mode") if d.get("mode") in MODES else "add",
        )


@dataclass
class EffectorMatrix:
    sources: list = field(default_factory=list)   # list[EffectorSource]
    routes: list = field(default_factory=list)     # list[EffectorRoute]
    tempo_bpm: float = 120.0

    def to_dict(self) -> dict:
        return {
            "sources": [s.to_dict() for s in self.sources],
            "routes": [r.to_dict() for r in self.routes],
            "tempo_bpm": self.tempo_bpm,
        }

    @staticmethod
    def from_dict(d: dict) -> "EffectorMatrix":
        return EffectorMatrix(
            sources=[EffectorSource.from_dict(s) for s in d.get("sources", [])],
            routes=[EffectorRoute.from_dict(r) for r in d.get("routes", [])],
            tempo_bpm=max(1.0, min(400.0, float(d.get("tempo_bpm", 120.0)))),
        )

    def source_values(self, t: float, audio_bands: dict | None = None) -> dict:
        """One sampled value per source, for meters (2) and route resolution —
        computed once per tick and reused for both, same discipline as
        modulation.py's ModMatrix.update()/source_value(). `audio_bands` —
        {"low"/"mid"/"high"/"master": 0..1} — is the browser's live mic
        analysis (see composite.py's set_audio_bands); ignored by "lfo"-type
        sources. Always includes the 4 fixed AUDIO_PSEUDO_SOURCE_IDS too (1),
        regardless of whether any real "audio"-type source exists, so a
        route can target a band directly.
        """
        values = {s.id: s.sample(t, self.tempo_bpm, audio_bands) for s in self.sources}
        ab = audio_bands or {}
        for band, pseudo_id in AUDIO_PSEUDO_SOURCE_IDS.items():
            values[pseudo_id] = ab.get(band, 0.0)
        return values

    def resolve(self, t: float, base_values: dict, audio_bands: dict | None = None) -> dict:
        """`base_values`: {(poly_id, param): base_float}. Returns a NEW dict
        with the same keys, each run through every route that targets it, in
        declaration order (mode="replace" wins last, others accumulate) — see
        module docstring. Keys with no route targeting them pass through
        unchanged (a cheap, correct no-op for shapes with no effectors)."""
        values = dict(base_values)
        sv = self.source_values(t, audio_bands)
        for route in self.routes:
            key = (route.target_poly, route.target_param)
            if key not in values:
                continue
            raw = sv.get(route.source_id)
            if raw is None:
                continue
            contribution = _apply_curve(raw, route.curve) * route.depth + route.offset
            if route.mode == "replace":
                values[key] = contribution
            elif route.mode == "multiply":
                values[key] = values[key] * (1.0 + contribution)
            else:  # add
                values[key] = values[key] + contribution
        return values
