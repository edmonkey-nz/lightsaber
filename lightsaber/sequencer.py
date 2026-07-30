"""The sequencer: an ordered list of saved canvases played back over time for
the monitor outputs. Steps reference a canvas by its library name (so the
same canvas can appear in more than one step — that's why a Step has its own
id, distinct from the canvas name it points at), and hold their own
duration. Persisted as a single working sequence — a pane, not a library of
named sequences — to one sequence.json alongside the canvas/scene libraries.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    canvas: str = ""          # CanvasManager library key
    duration: float = 8.0     # seconds

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Step":
        return Step(id=d.get("id") or uuid.uuid4().hex[:8],
                    canvas=d.get("canvas", ""),
                    duration=float(d.get("duration", 8.0)))


@dataclass
class Sequence:
    loop: bool = True
    steps: list = field(default_factory=list)   # list[Step]

    def to_dict(self) -> dict:
        return {"loop": self.loop, "steps": [s.to_dict() for s in self.steps]}

    @staticmethod
    def from_dict(d: dict) -> "Sequence":
        return Sequence(loop=bool(d.get("loop", True)),
                        steps=[Step.from_dict(s) for s in d.get("steps", [])])


class Sequencer:
    """Owns the sequence document and the playhead, and persists to a single
    sequence.json per project (see projects.py). Ticked from the
    compositor's own clock (composite.py)."""

    def __init__(self, path: str):
        self.path = path
        self.sequence = Sequence()
        self._load()
        self.playing = False
        self.index = 0
        self.elapsed = 0.0

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.sequence = Sequence.from_dict(json.load(f))
            except Exception as e:
                print(f"[lightsaber] could not load {self.path}: {e}")

    def retarget(self, path: str):
        """Re-point this sequencer at a different project's sequence.json in
        place — composite.py holds a live reference to this instance.
        Stops playback so the old project's steps don't keep advancing."""
        self.path = path
        self.sequence = Sequence()
        self._load()
        self.stop()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.sequence.to_dict(), f, indent=2)
        os.replace(tmp, self.path)

    # editing -----------------------------------------------------------
    def add_step(self, canvas: str, duration: float = 8.0) -> Step:
        step = Step(canvas=canvas, duration=max(0.5, float(duration)))
        self.sequence.steps.append(step)
        self.save()
        return step

    def remove_step(self, step_id: str):
        self.sequence.steps = [s for s in self.sequence.steps if s.id != step_id]
        if self.index >= len(self.sequence.steps):
            self.index = max(0, len(self.sequence.steps) - 1)
            self.elapsed = 0.0
        self.save()

    def reorder_step(self, step_id: str, index: int):
        steps = self.sequence.steps
        cur = next((i for i, s in enumerate(steps) if s.id == step_id), None)
        if cur is None:
            return
        step = steps.pop(cur)
        steps.insert(max(0, min(index, len(steps))), step)
        self.save()

    def set_step(self, step_id: str, canvas: str | None = None, duration: float | None = None):
        for s in self.sequence.steps:
            if s.id == step_id:
                if canvas is not None:
                    s.canvas = canvas
                if duration is not None:
                    s.duration = max(0.5, float(duration))
                break
        self.save()

    def set_loop(self, value: bool):
        self.sequence.loop = bool(value)
        self.save()

    # playback ------------------------------------------------------------
    def current_step(self) -> Step | None:
        steps = self.sequence.steps
        if not steps or not (0 <= self.index < len(steps)):
            return None
        return steps[self.index]

    def play(self):
        self.playing = bool(self.sequence.steps)

    def pause(self):
        self.playing = False

    def stop(self):
        self.playing = False
        self.index = 0
        self.elapsed = 0.0

    def goto(self, index: int):
        if self.sequence.steps:
            self.index = max(0, min(index, len(self.sequence.steps) - 1))
        self.elapsed = 0.0

    def next(self):
        self._advance(1)

    def prev(self):
        self._advance(-1)

    def _advance(self, delta: int):
        n = len(self.sequence.steps)
        if n == 0:
            return
        self.index += delta
        if self.index >= n:
            self.index = 0
            if not self.sequence.loop:
                self.playing = False
        elif self.index < 0:
            self.index = n - 1
        self.elapsed = 0.0

    def tick(self, dt: float) -> bool:
        """Advance the playhead by dt. Returns True exactly on the tick the
        active step changes (add/prebuild/step-boundary work belongs there,
        driven by the caller — CompositeRenderer)."""
        if not self.playing:
            return False
        step = self.current_step()
        if step is None:
            self.playing = False
            return False
        self.elapsed += dt
        if self.elapsed >= step.duration:
            self.elapsed = 0.0
            self.next()
            return True
        return False

    def state(self) -> dict:
        step = self.current_step()
        return {
            "playing": self.playing,
            "loop": self.sequence.loop,
            "index": self.index,
            "step_id": step.id if step else None,
            "elapsed": round(self.elapsed, 2),
            "duration": step.duration if step else 0.0,
            "n_steps": len(self.sequence.steps),
            "steps": [s.to_dict() for s in self.sequence.steps],
        }
