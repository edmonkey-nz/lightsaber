"""Projects: the base-level container. A project owns its own directory of
canvases and its own sequencer document — self-contained, disposable,
independently versioned, the same way ARCHITECTURE.md's "show" artifact is
described (creative content, cheap to fork/discard) as opposed to a
hardware/rig "profile" (not modelled by this codebase yet). Scenes and
uploaded media stay global/shared across every project deliberately: a
SceneSpec is expensive, reusable, Claude-authored content, not a per-show
artifact.

```
projects/
  default/
    project.json     # ProjectSpec
    canvases/*.json  # this project's canvas library
    sequence.json    # this project's sequencer document
```

ProjectManager is a pure on-disk library, same persistence shape as
SceneManager/CanvasManager (cached names(), atomic tmp+replace save,
sanitised filenames) — it does not itself own "the current project"; that's
Engine's job, since switching projects means retargeting CanvasManager and
Sequencer in place (see their `retarget` methods) rather than swapping
object references CompositeRenderer already holds live.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field


@dataclass
class ProjectSpec:
    name: str = "default"
    # Canvas library key last open in the editor when this project was
    # saved — restores your place on reopen. None = start on a fresh
    # untitled canvas.
    active_canvas: str | None = None
    schema: int = 1   # forward-compat marker for future project.json shapes

    def to_dict(self) -> dict:
        return {"name": self.name, "active_canvas": self.active_canvas, "schema": self.schema}

    @staticmethod
    def from_dict(d: dict) -> "ProjectSpec":
        return ProjectSpec(
            name=d.get("name", "default"),
            active_canvas=d.get("active_canvas"),
            schema=int(d.get("schema", 1)),
        )


class ProjectManager:
    def __init__(self, projects_dir: str):
        self.projects_dir = projects_dir
        os.makedirs(projects_dir, exist_ok=True)
        self._names_cache: list[str] | None = None

    def names(self) -> list[str]:
        if self._names_cache is None:
            self._names_cache = sorted(
                n for n in os.listdir(self.projects_dir)
                if os.path.isfile(os.path.join(self.projects_dir, n, "project.json"))
            )
        return self._names_cache

    def dir_for(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
        return os.path.join(self.projects_dir, safe or "untitled")

    def canvas_dir(self, name: str) -> str:
        return os.path.join(self.dir_for(name), "canvases")

    def sequence_path(self, name: str) -> str:
        return os.path.join(self.dir_for(name), "sequence.json")

    def create(self, name: str) -> ProjectSpec:
        spec = ProjectSpec(name=name)
        self.save(name, spec)
        return spec

    def save(self, name: str, spec: ProjectSpec):
        spec.name = name
        d = self.dir_for(name)
        os.makedirs(os.path.join(d, "canvases"), exist_ok=True)
        tmp = os.path.join(d, "project.json.tmp")
        with open(tmp, "w") as f:
            json.dump(spec.to_dict(), f, indent=2)
        os.replace(tmp, os.path.join(d, "project.json"))
        self._names_cache = None

    def load(self, name: str) -> ProjectSpec:
        with open(os.path.join(self.dir_for(name), "project.json")) as f:
            return ProjectSpec.from_dict(json.load(f))

    def duplicate(self, src_name: str, dst_name: str) -> ProjectSpec:
        """Save-as: copy a project's whole directory (project.json,
        canvases/*.json, sequence.json) under a new name and return the
        copy's spec. "Fork the show before the gig.\""""
        src, dst = self.dir_for(src_name), self.dir_for(dst_name)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        spec = self.load(dst_name) if os.path.isfile(os.path.join(dst, "project.json")) else ProjectSpec()
        spec.name = dst_name
        self.save(dst_name, spec)
        self._names_cache = None
        return spec

    def delete(self, name: str):
        # Explicit enumeration, not shutil.rmtree(dir_for(name)) — dir_for()
        # derives from user-provided text, and a blanket recursive delete on
        # a derived path is the kind of thing worth refusing to make
        # convenient. Delete exactly what a project is known to contain.
        d = self.dir_for(name)
        canvases_dir = os.path.join(d, "canvases")
        if os.path.isdir(canvases_dir):
            for f in os.listdir(canvases_dir):
                if f.endswith(".json"):
                    os.remove(os.path.join(canvases_dir, f))
            os.rmdir(canvases_dir)
        for f in ("sequence.json", "project.json"):
            p = os.path.join(d, f)
            if os.path.exists(p):
                os.remove(p)
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
        self._names_cache = None
