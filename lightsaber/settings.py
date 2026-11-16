"""Tiny local settings store — machine-level prefs (the media roots and the
last-opened project) in a gitignored JSON file next to the project.

It used to hold an Anthropic API key for the scene director too. That feature
is gone; an old settings.json may still contain the key, which nothing reads
any more — delete the "anthropic_api_key" entry by hand if you want it off
disk.
"""

from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "settings.json")


def load() -> dict:
    try:
        with open(_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save(data: dict) -> None:
    tmp = _PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _PATH)
    try:
        os.chmod(_PATH, 0o600)   # holds local paths; and an old API key may linger
    except Exception:
        pass


def get(key: str, default=None):
    return load().get(key, default)


def set(key: str, value) -> None:
    data = load()
    data[key] = value
    save(data)
