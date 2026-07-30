"""Lightsaber's coordinator.

Owns the on-disk libraries — scenes (global, shared across projects),
projects (each with its own canvas library + sequencer document) — and the
Claude scene director, and wires them into CompositeRenderer: the render
loop, on its own thread/clock. There is no other render path: canvas
polygons ("shapes") are the only thing that ever gets drawn, each with its
own live Scene built from the scene library (see composite.py).
"""

from __future__ import annotations

import os

from .scenes import SceneManager
from .canvases import CanvasManager
from .sequencer import Sequencer
from .projects import ProjectManager
from .composite import CompositeRenderer
from .director import SceneDirector


class Engine:
    def __init__(self, *, library_dir, cache_dir, projects_dir,
                 fps=20, model=None, enable_diagnostics=False):
        self.scenes = SceneManager(library_dir)
        self.director = SceneDirector(cache_dir, model=model)
        self.projects = ProjectManager(projects_dir)

        # CompositeRenderer holds a live reference to `canvases`/`sequencer`
        # for the life of the process; which project they point at is set
        # by `set_project` below (retargets in place — see canvases.py's
        # and sequencer.py's `retarget`), so these starting paths are just
        # placeholders satisfying the constructor, not a real project.
        boot_dir = os.path.join(projects_dir, ".bootstrap")
        self.canvases = CanvasManager(os.path.join(boot_dir, "canvases"))
        self.sequencer = Sequencer(os.path.join(boot_dir, "sequence.json"))
        self.composite = CompositeRenderer(
            self.canvases, self.sequencer, self.scenes.load_spec, self.projects, fps=fps)
        self.composite.set_diagnostics(enable_diagnostics)

    # lifecycle ---------------------------------------------------------
    def start(self):
        self.composite.start()

    def stop(self):
        self.composite.stop()

    # scene generation ----------------------------------------------------
    def generate_scene(self, keyword: str, name: str | None = None, size: str = "small",
                        aspect: str = "1:1") -> str:
        """The director call may hit the network — call this off the event
        loop (see server.py). Returns the library key the scene was saved
        under, so the caller can put it straight onto the canvas (see
        server.py's `generate` handler)."""
        spec = self.director.generate(keyword, size=size, aspect=aspect)
        name = (name or "").strip() or spec.name or keyword
        spec.name = name
        try:
            self.scenes.save(name, spec)
        except Exception as e:
            print(f"[lightsaber] could not save generation: {e}")
        return name

    # introspection for the UI ---------------------------------------------
    def state(self) -> dict:
        return {
            "version": __import__("lightsaber").__version__,
            "library": self.scenes.names(),
            "generators": __import__("lightsaber.generators", fromlist=["available"]).available(),
            "director_online": self.director.online,
            "director_model": self.director.model,
            "director_source": self.director.last_source,
            "director_error": self.director.last_error,
            "director_choice": self.director.model_choice,
            "director_effort": self.director.effort,
            "director_progress": self.director.last_progress,
            "director_generating": self.director.generating,
            "max_pps": self.director.max_pps,
            **self.composite.state(),   # active, visuals_disabled, master_gain,
                                          # perf_diag, diagnostics_enabled,
                                          # project, project_library
            "canvas": self.canvases.current.to_dict(),
            "canvas_library": self.canvases.names(),
            "sequence": self.sequencer.state(),
        }

    def composite_preview(self, max_points: int = 4000, stroke_thin: int = 200):
        return self.composite.composite_preview(max_points=max_points, stroke_thin=stroke_thin)
