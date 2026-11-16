"""Audio-responsive visualisers — the "visual" fill type.

A visualiser is a shape fill whose frames come from a GLSL fragment shader
instead of a file or a camera. The browser renders it (web/static/visuals.js)
into a small offscreen canvas and then treats that canvas exactly like a video
frame: corner-pin warp, masks, feather, key, FX, kaleidoscope, effectors and
culling all apply unchanged. The server never touches pixels.

This module is the ONE definition of each preset's parameters — label, range,
default — used to clamp what is stored (canvases.py / composite.py) and served
to the editor at GET /visuals/presets to build its sliders. visuals.js holds
the shaders and reads parameters by the same keys, as `u_<key>` uniforms.

Why the server owns the clock and the phase
-------------------------------------------
Every output window renders its own copy of a visualiser (as it decodes its
own copy of a video), so a shape spanning two projectors only lines up if
each window computes the same picture. Hence every preset is a pure function
of (time, phase, audio levels, params) — no frame-to-frame state in the
browser.

The catch is audio-driven SPEED: "bass speeds up the starfield" means
position is the integral of speed over time, which depends on history. A
window that joined late, or saw a different message jitter, would integrate
to a different place. So the compositor integrates it once per tick
(`phase_rate` below) and publishes the running total as `visual_phase`, the
same way it publishes a video playhead; windows only extrapolate it between
messages using the published rate.
"""

from __future__ import annotations

# Each param: key -> (label, min, max, step, default). Order is UI order.
# Hues are 0..1 floats rather than colour strings, so every param is a plain
# clamped float and every uniform a float.
PRESETS: dict[str, dict] = {
    "starfield": {
        "label": "Starfield",
        "hint": "stars streaming towards you — bass pushes the speed, highs make them twinkle",
        "params": {
            "speed":      ("speed", 0.0, 3.0, 0.01, 0.5),
            "density":    ("density", 0.2, 2.0, 0.01, 1.0),
            "size":       ("star size", 0.3, 3.0, 0.01, 1.0),
            "twinkle":    ("twinkle", 0.0, 1.0, 0.01, 0.35),
            "hue":        ("hue", 0.0, 1.0, 0.005, 0.6),
            "colour":     ("colour", 0.0, 1.0, 0.01, 0.35),
            "brightness": ("brightness", 0.0, 2.0, 0.01, 1.0),
            "react_bass": ("bass → speed", 0.0, 3.0, 0.01, 1.0),
            "react_high": ("highs → sparkle", 0.0, 2.0, 0.01, 1.0),
        },
    },
    "clouds": {
        "label": "Dreamy clouds",
        "hint": "slow, soft cloud drift — the overall level swells them, bass makes them billow",
        "params": {
            "speed":      ("drift", 0.0, 2.0, 0.01, 0.25),
            "scale":      ("scale", 0.3, 4.0, 0.01, 2.2),
            "coverage":   ("coverage", 0.0, 1.0, 0.01, 0.35),
            "softness":   ("softness", 0.05, 1.0, 0.01, 0.45),
            "warp":       ("swirl", 0.0, 2.0, 0.01, 0.8),
            "hue":        ("hue", 0.0, 1.0, 0.005, 0.62),
            "hue2":       ("second hue", 0.0, 1.0, 0.005, 0.85),
            "brightness": ("brightness", 0.0, 2.0, 0.01, 1.0),
            "react_level": ("level → swell", 0.0, 2.0, 0.01, 0.8),
            "react_bass":  ("bass → billow", 0.0, 2.0, 0.01, 0.8),
        },
    },
    "turbulence": {
        "label": "Turbulence",
        "hint": "swirling, marbled flow — bass pushes it along, mids make it churn",
        "params": {
            "speed":      ("speed", 0.0, 3.0, 0.01, 0.6),
            "scale":      ("scale", 0.3, 4.0, 0.01, 1.4),
            "churn":      ("churn", 0.0, 2.0, 0.01, 0.9),
            "detail":     ("streaks", 0.5, 6.0, 0.01, 2.5),
            "softness":   ("softness", 0.0, 1.0, 0.01, 0.5),
            "hue":        ("hue", 0.0, 1.0, 0.005, 0.55),
            "hue2":       ("second hue", 0.0, 1.0, 0.005, 0.95),
            "brightness": ("brightness", 0.0, 2.0, 0.01, 1.0),
            "react_bass": ("bass → speed", 0.0, 3.0, 0.01, 1.0),
            "react_mid":  ("mids → churn", 0.0, 2.0, 0.01, 1.0),
        },
    },
    "oil": {
        "label": "Oil slick",
        "hint": "rainbow interference on a slowly warping film, like oil on water — bass swirls it, the level makes it shimmer",
        "params": {
            "speed":       ("swirl speed", 0.0, 2.0, 0.01, 0.3),
            "scale":       ("scale", 0.3, 4.0, 0.01, 1.3),
            "thickness":   ("film thickness", 0.2, 3.0, 0.01, 1.0),
            "warp":        ("swirl", 0.0, 2.0, 0.01, 1.0),
            "coverage":    ("coverage", 0.0, 1.0, 0.01, 0.45),
            "saturation":  ("colour", 0.0, 1.0, 0.01, 0.9),
            "brightness":  ("brightness", 0.0, 2.0, 0.01, 1.0),
            "react_bass":  ("bass → swirl", 0.0, 3.0, 0.01, 1.0),
            "react_level": ("level → shimmer", 0.0, 2.0, 0.01, 0.8),
        },
    },
    "ink": {
        "label": "Ink in water",
        "hint": "drops of ink blooming into tendrils and fading — bass makes them bloom faster",
        "params": {
            "speed":      ("bloom speed", 0.0, 3.0, 0.01, 0.6),
            "drops":      ("drops", 1.0, 8.0, 1.0, 4.0),
            "size":       ("size", 0.3, 2.5, 0.01, 1.0),
            "swirl":      ("swirl", 0.0, 2.0, 0.01, 1.0),
            "tendrils":   ("tendrils", 0.0, 1.0, 0.01, 0.7),
            "hue":        ("hue", 0.0, 1.0, 0.005, 0.58),
            "spread":     ("colour spread", 0.0, 1.0, 0.01, 0.35),
            "brightness": ("brightness", 0.0, 2.0, 0.01, 1.0),
            "react_bass": ("bass → bloom", 0.0, 3.0, 0.01, 1.2),
        },
    },
    "aurora": {
        "label": "Aurora",
        "hint": "slow curtains of light — the level brightens them, highs make them shimmer",
        "params": {
            "speed":       ("speed", 0.0, 2.0, 0.01, 0.35),
            "curtains":    ("curtains", 1.0, 5.0, 1.0, 2.0),
            "height":      ("height", 0.2, 1.5, 0.01, 0.8),
            "ripple":      ("ripple", 0.0, 2.0, 0.01, 0.8),
            "hue":         ("hue", 0.0, 1.0, 0.005, 0.38),
            "hue2":        ("top hue", 0.0, 1.0, 0.005, 0.8),
            "brightness":  ("brightness", 0.0, 2.0, 0.01, 1.0),
            "react_level": ("level → glow", 0.0, 2.0, 0.01, 1.0),
            "react_high":  ("highs → shimmer", 0.0, 2.0, 0.01, 1.0),
            "react_bass":  ("bass → speed", 0.0, 3.0, 0.01, 0.6),
        },
    },
}
DEFAULT_PRESET = "starfield"


def preset_key(name) -> str:
    return name if name in PRESETS else DEFAULT_PRESET


def clamp_param(preset: str, key: str, value) -> float | None:
    """The stored value for one param, or None if the key isn't one of this
    preset's (dropped rather than stored, so junk can't accumulate)."""
    spec = PRESETS[preset_key(preset)]["params"].get(key)
    if spec is None:
        return None
    _, lo, hi, _, default = spec
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                       # NaN
        return default
    return max(lo, min(hi, v))


def sanitize_params(value) -> dict:
    """{preset: {key: float}} — sparse per-preset overrides, so switching a
    shape between presets and back keeps each preset's own tweaks."""
    out: dict = {}
    if not isinstance(value, dict):
        return out
    for preset, params in value.items():
        if preset not in PRESETS or not isinstance(params, dict):
            continue
        clean = {}
        for k, v in params.items():
            c = clamp_param(preset, k, v)
            if c is not None:
                clean[k] = c
        if clean:
            out[preset] = clean
    return out


def resolve(preset: str, stored: dict) -> dict:
    """Every param of `preset`, overrides applied over defaults — what the
    renderers receive, so the output window never needs the spec."""
    preset = preset_key(preset)
    own = (stored or {}).get(preset, {})
    return {k: own.get(k, spec[4]) for k, spec in PRESETS[preset]["params"].items()}


def phase_rate(preset: str, params: dict, bands: dict) -> float:
    """d(phase)/dt for this tick — the audio-driven part of the motion. Must
    stay a function of the CURRENT params and levels only; the compositor
    does the integrating (see the module docstring)."""
    speed = params.get("speed", 0.0)
    gain = _BASS_SPEED_GAIN.get(preset, 1.0)
    return speed * (1.0 + params.get("react_bass", 0.0) * gain * bands.get("low", 0.0))


# How hard full bass pushes each preset's speed, at react_bass = 1. A
# starfield wants an obvious surge on a kick; clouds and oil only a nudge, or
# they read as jittery rather than swelling.
_BASS_SPEED_GAIN = {"starfield": 2.0, "clouds": 1.0, "turbulence": 1.5,
                    "oil": 1.0, "ink": 1.5, "aurora": 0.8}


def presets_for_ui() -> dict:
    """JSON for GET /visuals/presets — the editor builds its sliders from it."""
    return {
        name: {
            "label": p["label"], "hint": p["hint"],
            "params": [{"key": k, "label": s[0], "min": s[1], "max": s[2],
                        "step": s[3], "default": s[4]} for k, s in p["params"].items()],
        }
        for name, p in PRESETS.items()
    }
