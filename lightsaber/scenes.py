"""Scenes: the unit the director produces and canvas shapes reference.

A SceneSpec is plain JSON-serialisable data: one or more visual layers (a
generator name + its params), a colour palette, and a set of modulation
routes. A live Scene builds generators from the spec and renders them.
SceneManager is a pure on-disk library (no "current scene" concept — each
canvas polygon owns its own live Scene instance, built by CompositeRenderer
via SceneManager.load_spec; see composite.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

from .geometry import Frame, clamp_frame
from . import generators as gen


# --- spec --------------------------------------------------------------------

@dataclass
class Layer:
    generator: str
    params: dict = field(default_factory=dict)


@dataclass
class SceneSpec:
    name: str = "untitled"
    layers: list = field(default_factory=list)          # list[Layer|dict]
    palette: list = field(default_factory=list)          # list of "#rrggbb"
    modulation: list = field(default_factory=list)       # list of route dicts
    camera: dict = field(default_factory=dict)           # 3D camera/depth config
    # The frame shape this scene was authored/generated for — "W:H", e.g.
    # "1:1"/"16:9"/"4:3". Every scene predating this field is implicitly
    # "1:1" (the only shape the generators/director ever targeted before).
    # Pure metadata: generators still render into [-1,1]x[-1,1] regardless —
    # this tells the compositor's per-shape "fit" mode (see canvases.py's
    # PolygonSpec.fit) what shape that square is actually meant to represent,
    # so it can letterbox/crop correctly instead of assuming 1:1.
    aspect: str = "1:1"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["layers"] = [asdict(l) if isinstance(l, Layer) else l for l in self.layers]
        return d

    @staticmethod
    def from_dict(d: dict) -> "SceneSpec":
        layers = [Layer(**l) if isinstance(l, dict) else l for l in d.get("layers", [])]
        return SceneSpec(
            name=d.get("name", "untitled"),
            layers=layers,
            palette=d.get("palette", []),
            modulation=d.get("modulation", []),
            camera=d.get("camera", {}),
            aspect=d.get("aspect", "1:1"),
        )


# --- live scene --------------------------------------------------------------

class Scene:
    """A built, renderable scene."""

    def __init__(self, spec: SceneSpec):
        self.spec = spec
        self._gens = []
        self.is_3d = False
        for layer in spec.layers:
            g = layer if isinstance(layer, Layer) else Layer(**layer)
            generator = gen.create(g.generator)
            self._gens.append((generator, dict(g.params)))
            if getattr(generator, "is_3d", False):
                self.is_3d = True
        self.camera = None
        if self.is_3d:
            from .scene3d import make_camera
            self.camera = make_camera(spec.camera)

    def _resolve(self, generator, base_params, matrix):
        p = dict(generator.defaults)
        p.update(base_params)
        if matrix is not None:
            for key in list(p.keys()):
                p[key] = matrix.value(f"visual.{key}", p[key])
        return p

    def render(self, t: float, dt: float = 0.0, matrix=None, disable_plane: bool = False) -> Frame:
        frame: Frame = []
        if self.camera is not None:
            self.camera.update(t, dt, matrix)
        for g, base_params in self._gens:
            p = self._resolve(g, base_params, matrix)
            if getattr(g, "is_3d", False):
                # "Disable scene plane" (Shape camera override): world.py's
                # World generator looks for this key and skips any node
                # whose shape/primitive name looks like a floor/ground/
                # grid/plane — a loose key on the params dict rather than a
                # new method signature, matching how the rest of this dict
                # is already loosely extended (e.g. primitives get `t`
                # merged in the same way). Generators that don't look for
                # it just ignore it.
                p["_disable_plane"] = disable_plane
                paths3d = g.render3d(t, p)
                frame.extend(self.camera.project(paths3d, g.field_depth))
            else:
                frame.extend(g.render(t, p))
        return clamp_frame(frame)


# --- manager -----------------------------------------------------------------

class SceneManager:
    """A pure on-disk scene library — no "current scene" concept. Each canvas
    polygon builds and owns its own live `Scene` (see composite.py); this
    class only ever reads/writes spec files."""

    def __init__(self, library_dir: str):
        self.library_dir = library_dir
        os.makedirs(library_dir, exist_ok=True)
        self._names_cache: list[str] | None = None

    def names(self) -> list[str]:
        # `state()` (and so this) is polled by the websocket broadcaster at
        # ~20Hz regardless of whether the library changed — an unconditional
        # os.listdir()+sort here was real, avoidable disk I/O on the same
        # process the render thread shares the GIL with, 20 times a second,
        # for a result that only ever changes on save/delete. Cached and
        # invalidated explicitly by the two calls below that can change it.
        if self._names_cache is None:
            self._names_cache = sorted(
                os.path.splitext(f)[0]
                for f in os.listdir(self.library_dir)
                if f.endswith(".json")
            )
        return self._names_cache

    def path_for(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
        return os.path.join(self.library_dir, f"{safe or 'untitled'}.json")

    def save(self, name: str, spec: SceneSpec):
        spec.name = name
        tmp = self.path_for(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(spec.to_dict(), f, indent=2)
        os.replace(tmp, self.path_for(name))
        self._names_cache = None

    def load_spec(self, name: str) -> SceneSpec:
        with open(self.path_for(name)) as f:
            return SceneSpec.from_dict(json.load(f))

    def delete(self, name: str):
        p = self.path_for(name)
        if os.path.exists(p):
            os.remove(p)
        self._names_cache = None
