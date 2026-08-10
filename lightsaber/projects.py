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
class OutputMonitorConfig:
    """Physical-mount correction for one output window (Output 1/2/…) — flip
    reverses the whole composited image, keystone compensates an angled
    projector throw. Lives per-project now (moved off browser localStorage,
    see ProjectSpec.outputs below): a rig's physical orientation belongs to
    the show/venue it's for, not to whichever browser happened to have it
    open — and localStorage never crossed browsers/machines in the first
    place, only tabs of the same browser."""
    flip_x: bool = False
    flip_y: bool = False
    keystone_h: float = 0.0
    keystone_v: float = 0.0
    # Viewport (a crop rect into the FULL composited canvas, normalized
    # [0,1] fractions, top-left origin) — which region of the canvas this
    # output actually shows. Only takes effect when ProjectSpec's
    # viewports_enabled is True; otherwise every output always shows the
    # full canvas (0,0,1,1) regardless of these — the original "every
    # output is a duplicate feed" behaviour, and still the default even
    # with viewports on (a fresh output starts full-frame, not a random
    # crop). This is what makes a wide multi-projector canvas (see
    # ARCHITECTURE.md's render-host topology — one wide framebuffer sliced
    # across several physical outputs) representable: each output's
    # viewport is its own slice.
    viewport_x: float = 0.0
    viewport_y: float = 0.0
    viewport_w: float = 1.0
    viewport_h: float = 1.0
    # If set (another output's 0-based index), this output's CONTENT
    # ignores its own viewport above and mirrors that other output's
    # instead — the "Output 2 = duplicate of Output 1" simple/laptop case,
    # without hand-copying four numbers. Flip/keystone/test-pattern stay
    # independent regardless (a rear-mounted duplicate projector still
    # needs its own flip) — this only ever affects which canvas region is
    # shown, never the physical-orientation fields above.
    duplicate_of: int | None = None
    # Edge blending. Which of this output's own edges sit in a physical
    # overlap with a neighbouring projector, and so need feathering.
    #
    # Explicit per edge rather than inferred from whether two viewports
    # happen to touch: projectors don't always abut (a deliberately
    # separated pair of surfaces is a normal rig), and guessing wrong
    # silently fades content to black at an edge nothing overlaps.
    #
    # The AMOUNT is one project-wide number (ProjectSpec.edge_overlap),
    # not per edge — one figure to set against the physical rig at load-in.
    # Ticking an edge widens this output's viewport outward by half the
    # overlap band, so the two neighbours' cones cover a shared strip that
    # each fades across; see ARCHITECTURE.md §5 and renderhost's
    # OutputSpec.left_overlap/right_overlap, whose model this mirrors so a
    # project carries over to the render host unchanged.
    overlap_left: bool = False
    overlap_right: bool = False
    overlap_top: bool = False
    overlap_bottom: bool = False

    def to_dict(self) -> dict:
        return {"flip_x": self.flip_x, "flip_y": self.flip_y,
                "keystone_h": self.keystone_h, "keystone_v": self.keystone_v,
                "viewport_x": self.viewport_x, "viewport_y": self.viewport_y,
                "viewport_w": self.viewport_w, "viewport_h": self.viewport_h,
                "duplicate_of": self.duplicate_of,
                "overlap_left": self.overlap_left, "overlap_right": self.overlap_right,
                "overlap_top": self.overlap_top, "overlap_bottom": self.overlap_bottom}

    @staticmethod
    def from_dict(d: dict) -> "OutputMonitorConfig":
        return OutputMonitorConfig(
            flip_x=bool(d.get("flip_x", False)),
            flip_y=bool(d.get("flip_y", False)),
            keystone_h=max(-0.5, min(0.5, float(d.get("keystone_h", 0.0)))),
            keystone_v=max(-0.5, min(0.5, float(d.get("keystone_v", 0.0)))),
            viewport_x=max(0.0, min(1.0, float(d.get("viewport_x", 0.0)))),
            viewport_y=max(0.0, min(1.0, float(d.get("viewport_y", 0.0)))),
            viewport_w=max(0.01, min(1.0, float(d.get("viewport_w", 1.0)))),
            viewport_h=max(0.01, min(1.0, float(d.get("viewport_h", 1.0)))),
            duplicate_of=(int(d["duplicate_of"]) if d.get("duplicate_of") is not None else None),
            overlap_left=bool(d.get("overlap_left", False)),
            overlap_right=bool(d.get("overlap_right", False)),
            overlap_top=bool(d.get("overlap_top", False)),
            overlap_bottom=bool(d.get("overlap_bottom", False)),
        )


@dataclass
class ProjectSpec:
    name: str = "default"
    # Canvas library key last open in the editor when this project was
    # saved — restores your place on reopen. None = start on a fresh
    # untitled canvas.
    active_canvas: str | None = None
    # Output resolution (e.g. a projector's native mode) — lives here, not
    # on each CanvasSpec, because it's a property of the physical rig a
    # project targets, not of any one canvas layout within it: every canvas
    # in the sequencer composites onto the same screens in turn, so they
    # must all share one resolution or the output window would resize its
    # letterbox on every sequencer step. See composite.py's
    # set_project_resolution / CompositeRenderer.current_project.
    width: int = 1920
    height: int = 1080
    # Per-output-monitor flip/keystone/viewport (see OutputMonitorConfig
    # above) — a plain list, index 0 = Output 1, index 1 = Output 2, and so
    # on. Its LENGTH is the project's own "number of outputs" (Project tab)
    # — the header's Output N buttons and the Output Preview tab's tiles are
    # both generated from this same length, so changing it here is the one
    # place that controls how many outputs this project drives, from a
    # laptop's 1-2 up to a render host's 5 (see ARCHITECTURE.md) — see
    # composite.py's set_output_count.
    outputs: list = field(default_factory=lambda: [OutputMonitorConfig(), OutputMonitorConfig()])
    # Master switch for viewports (OutputMonitorConfig.viewport_*/
    # duplicate_of above) — off by default so every EXISTING project keeps
    # today's exact behaviour (every output = the full canvas) even though
    # it now has viewport fields sitting there unused. Flip it on to start
    # treating each output as a slice of one wide canvas instead.
    viewports_enabled: bool = False
    # Width of the physical overlap band between two adjacent projectors,
    # as a fraction of ONE output's width (or height, on a horizontal
    # seam). Project-wide rather than per output: it's a property of how
    # the rig is physically set up, and one number is what you tune against
    # the wall at load-in.
    #
    # Which edges actually use it is per output and explicit — see
    # OutputMonitorConfig.overlap_*. 0.0 means no blending anywhere, which
    # is the default, so every existing project keeps its hard-cut edges.
    edge_overlap: float = 0.0
    # Small FPS readout drawn in the corner of each output window — a
    # calibration/diagnostic aid (Project tab), not show content, so it
    # lives right next to viewports_enabled rather than on
    # OutputMonitorConfig: it's one on/off for every output, not per-output.
    show_fps: bool = False
    # Render/broadcast rate for this project, in frames per second. Lives on
    # the project because it's a property of the rig+content the project
    # targets (a heavy 8-shape video canvas on a laptop wants a different
    # number than a light vector scene on the render host), not of the
    # process — `run.py --fps` is now just the startup default that a loaded
    # project immediately overrides. This paces BOTH the compositor's render
    # loop and server.py's state broadcaster; they must stay tied together,
    # since a broadcaster running slower than the render loop silently caps
    # what every output window actually sees regardless of how fast frames
    # are produced (which is exactly the bug that made --fps look inert).
    fps: int = 20
    # Free-text notes (Project tab) — a show's own run sheet/reminders
    # ("venue contact is X", "canvas 3 needs the fisheye lens"), not
    # consumed by anything else in the engine. Same edit-in-memory,
    # persist-on-explicit-Save discipline as everything else on this spec.
    notes: str = ""
    schema: int = 1   # forward-compat marker for future project.json shapes

    def to_dict(self) -> dict:
        return {"name": self.name, "active_canvas": self.active_canvas,
                "width": self.width, "height": self.height,
                "outputs": [o.to_dict() for o in self.outputs],
                "viewports_enabled": self.viewports_enabled,
                "edge_overlap": self.edge_overlap, "show_fps": self.show_fps,
                "fps": self.fps, "notes": self.notes, "schema": self.schema}

    @staticmethod
    def from_dict(d: dict) -> "ProjectSpec":
        outputs = [OutputMonitorConfig.from_dict(o) for o in d.get("outputs", [])]
        if not outputs:
            outputs = [OutputMonitorConfig(), OutputMonitorConfig()]
        return ProjectSpec(
            name=d.get("name", "default"),
            active_canvas=d.get("active_canvas"),
            width=int(d.get("width", 1920)),
            height=int(d.get("height", 1080)),
            outputs=outputs,
            viewports_enabled=bool(d.get("viewports_enabled", False)),
            edge_overlap=max(0.0, min(0.5, float(d.get("edge_overlap", 0.0) or 0.0))),
            show_fps=bool(d.get("show_fps", False)),
            fps=max(1, min(120, int(d.get("fps", 20)))),
            notes=str(d.get("notes", "")),
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
