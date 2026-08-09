"""Per-ASSET media metadata: how long a file is, and the default in/out trim
new shapes inherit from it.

Deliberately global (one store per install, keyed by the same media ref key
the browser uses — "lib:<name>" or "link:<root>::<rel>"), not per-project:
media is already shared across projects (see projects.py's docstring), and
"this 30-minute recording is really only interesting from 12:04 to 12:34" is
a fact about the file, not about one show.

Instances override rather than copy. A PolygonSpec's media_in/media_out are
None by default meaning "inherit whatever this file's default is", so
re-trimming the file here moves every shape that hasn't set its own — the
same sparse-override shape PolygonSpec.overrides already uses. That's the
whole point of storing a default at all: otherwise you'd re-find your 30
seconds inside the 30-minute file for every shape that uses it.

Duration is measured by the browser (the server never opens the file — no
ffprobe dependency) and reported back here the first time an element loads,
so the UI can show a timeline without re-probing on every visit.
"""

from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "media_meta.json")


def _load() -> dict:
    try:
        with open(_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, _PATH)


def all_meta() -> dict:
    return _load()


def get(key: str) -> dict:
    """{'duration': float|None, 'in': float, 'out': float|None} — always a
    usable dict, even for a file nothing's been recorded about yet."""
    entry = _load().get(str(key)) or {}
    return {
        "duration": entry.get("duration"),
        "in": float(entry.get("in", 0.0) or 0.0),
        "out": entry.get("out"),
    }


def _update(key: str, **fields) -> dict:
    data = _load()
    entry = data.get(str(key)) or {}
    entry.update(fields)
    data[str(key)] = entry
    _save(data)
    return entry


def set_duration(key: str, duration: float) -> None:
    """Recorded once, from whichever client first loaded the file. Ignores
    the junk values a <video> reports before metadata arrives (0, NaN, Inf)."""
    try:
        d = float(duration)
    except (TypeError, ValueError):
        return
    if not (d > 0) or d == float("inf"):
        return
    if get(key)["duration"] == d:
        return
    _update(key, duration=d)


def set_trim(key: str, in_s, out_s) -> dict:
    """The file's default in/out. out=None means 'to the end', which stays
    correct if the same key later points at a re-encoded, longer file."""
    try:
        i = max(0.0, float(in_s or 0.0))
    except (TypeError, ValueError):
        i = 0.0
    o = None
    if out_s is not None:
        try:
            o = float(out_s)
        except (TypeError, ValueError):
            o = None
    if o is not None and o <= i:
        o = None
    _update(key, **{"in": i, "out": o})
    return get(key)


def clear_trim(key: str) -> None:
    _update(key, **{"in": 0.0, "out": None})
