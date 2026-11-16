"""Lightsaber's coordinator.

Owns the on-disk project library (each project with its own canvas library
and sequencer document) and wires it into CompositeRenderer: the render loop,
on its own thread/clock. Canvas polygons ("shapes") are the only thing that
is ever drawn, and the drawing itself happens in the browser (see
composite.py).
"""

from __future__ import annotations

import os

from .canvases import CanvasManager
from .sequencer import Sequencer
from .projects import ProjectManager
from .composite import CompositeRenderer


class Engine:
    def __init__(self, *, projects_dir, fps=20, enable_diagnostics=False):
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
            self.canvases, self.sequencer, self.projects, fps=fps)
        self.composite.set_diagnostics(enable_diagnostics)
        # Latest client_stats from each output window, keyed by its socket
        # (see server.py): which GPU the browser gave it, its canvas size and
        # its paint rate. Written and read only on the event loop, so no lock.
        # Ported from promptwaver's GPU indicator.
        self.client_stats: dict = {}

    # lifecycle ---------------------------------------------------------
    def start(self):
        self.composite.start()

    def stop(self):
        self.composite.stop()

    # introspection for the UI ---------------------------------------------
    def state(self) -> dict:
        return {
            "version": __import__("lightsaber").__version__,
            **self.composite.state(),   # active, visuals_disabled, master_gain,
                                          # perf_diag, diagnostics_enabled,
                                          # project, project_library
            "canvas": self.canvases.current.to_dict(),
            "canvas_library": self.canvases.names(),
            "sequence": self.sequencer.state(),
            # NOT "outputs" — that key already holds the output monitor configs.
            "output_clients": list(self.client_stats.values()),
        }

    def composite_preview(self):
        return self.composite.composite_preview()
