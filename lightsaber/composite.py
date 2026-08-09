"""THE renderer: renders one live Scene per canvas polygon, each on its own
modulation matrix (routes are matrix-global — see ModMatrix.add_route — so
simultaneous scenes can't share one), and hands the browser the raw
per-polygon stroke lists plus each polygon's current corners so the quad
warp (unit square -> polygon) happens client-side.

Runs on its own thread and clock. Owns the master Start/Stop gate (`active`)
and the "Disable Visuals" fade (`master_gain`) — this used to be a second,
monitor-only render path alongside Engine._loop's single-scene/laser loop;
now that the laser is gone and there's no single-scene preview either, this
is the only render loop in the app, so those controls moved here.
"""

from __future__ import annotations

import threading
import time

from .modulation import ModMatrix, LFO
from .scenes import Scene
from .canvases import (CanvasManager, CanvasSpec, PolygonSpec,
                       DEFAULT_CLIP_POINTS, sanitize_clip_points)
from .media_roots import resolve as media_link_path, link_exists
from . import media_meta
from .sequencer import Sequencer
from .projects import ProjectManager, ProjectSpec, OutputMonitorConfig
from .perf import LoopStats
from .effectors import EffectorSource, EffectorRoute, transform_corners


def media_ref_key(poly: PolygonSpec) -> str | None:
    """The same asset key the browser uses (see index.html's mediaRefFor), so
    library metadata is looked up identically on both sides."""
    if poly.media_link:
        return "link:" + poly.media_link
    if poly.media:
        return "lib:" + poly.media
    return None


def media_cfg_sig(poly: PolygonSpec) -> str:
    return (f"{poly.media_in}/{poly.media_out}/{poly.media_mode}"
            f"/{poly.media_rate}/{poly.media_offset}")


# Same attr mapping as Engine._apply_param's "camera." branch (engine.py) —
# kept in sync so a polygon's camera override behaves identically to the
# single-scene one, just applied to that polygon's own live Scene.camera.
_CAMERA_ATTR_MAP = {
    "mode": ("mode", str),
    "speed": ("base_speed", float),
    "orbit_radius": ("orbit_radius", float),
    "fov": ("fov", float),
    "far": ("far", float),
    "max_strokes": ("max_strokes", int),
}


def _apply_camera_overrides(cam, overrides: dict):
    if cam is None:
        return
    for attr, value in overrides.items():
        mapped = _CAMERA_ATTR_MAP.get(attr)
        if mapped is None:
            continue
        field, cast = mapped
        setattr(cam, field, cast(value))


class _Slot:
    __slots__ = ("scene", "matrix", "t0", "scene_name")

    def __init__(self, scene, matrix, t0, scene_name):
        self.scene = scene
        self.matrix = matrix
        self.t0 = t0
        self.scene_name = scene_name


class CompositeRenderer:
    def __init__(self, canvases: CanvasManager, sequencer: Sequencer, scene_loader,
                 projects: ProjectManager, fps: int = 20):
        """`scene_loader(name) -> SceneSpec` — pass SceneManager.load_spec so
        a polygon's scene comes from the exact same library/file every
        project shares. `projects` — the on-disk project library; switching
        projects retargets `canvases`/`sequencer` in place (see their
        `retarget` methods) rather than replacing them, since this class
        holds a live reference to both."""
        self.canvases = canvases
        self.sequencer = sequencer
        self._scene_loader = scene_loader
        self.projects = projects
        self.current_project: ProjectSpec | None = None
        self.fps = fps

        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._queue = []          # queued mutations, applied at the top of _loop —
                                    # same discipline as Engine._enqueue, so the
                                    # render thread never races the websocket thread

        self._slots: dict[str, _Slot] = {}
        self._canvas_sig = None   # (name, ((poly_id, scene), ...)) — rebuild trigger
        self._last_layers: list[dict] = []   # published composite payload (raw Frames)

        # Master Start/Stop — nothing draws/animates until this is on. While
        # off, every slot's clock is frozen the same way Engine._loop used
        # to freeze the single scene's (shift t0 forward by dt each tick, so
        # resuming continues rather than jumping), and the published layers
        # go empty so the output windows go black — the show's "blackout".
        self.active = False
        # "Freeze shape playback" (Canvas panel, next to resolution) — unlike
        # `active`, content stays visible (rendered + published normally),
        # just with every slot's clock stopped (same t0-shift trick as the
        # `active=False` freeze, without the "publish nothing" part). For
        # calmer editing: `active` off shows nothing at all, which is no use
        # while dragging a shape's corners and wanting to actually see it.
        self.frozen = False
        # "Disable Visuals" (Global section) — a gracefully-fadeable dim,
        # independent of active/blank, applied client-side: composite_preview
        # publishes `master_gain` and both painters multiply it into opacity
        # (see index.html/output.html's drawWarpedStrokes) rather than
        # scaling colours server-side per point the way the old single-scene
        # loop did.
        self.visuals_disabled = False
        # Per-output test pattern (Project tab > Output monitors) — a
        # calibration aid, not show content: which projector is which, and
        # (drawn through that output's own keystone) whether the correction
        # is actually straightening the grid on the physical screen. Kept as
        # engine-runtime state rather than on ProjectSpec/disk on purpose —
        # it's a "right now, while I'm setting up" toggle, not something a
        # show should remember and silently re-enable on reopen. Keyed by
        # output index (0 = Output 1), same indexing as outputs.
        self._test_pattern: dict[int, bool] = {}
        self.master_gain = 1.0
        self._gain_from = 1.0
        self._gain_to = 1.0
        self._gain_fade_dur = 0.0
        self._gain_fade_pos = 0.0
        # Fires once, the tick a gain fade finishes — used by the sequencer's
        # crossfade (see _swap_sequencer_canvas) to swap the canvas exactly
        # when the fade-to-black bottoms out, then start the fade back in.
        # Not a general-purpose queue: a fresh fade (disable_visuals,
        # enable_visuals, or another crossfade) always overwrites/clears
        # whatever the previous one had pending, same as it overwrites
        # _gain_from/_gain_to/_gain_fade_dur/_gain_fade_pos.
        self._gain_fade_on_complete = None

        self.perf = LoopStats(fps)
        self._diag_enabled = False

        # Effector matrix clock (see effectors.py) — a canvas-wide t,
        # independent of any one scene slot's own t0, shifted by dt during
        # blackout/freeze exactly like every slot.t0 below, so LFOs stop
        # advancing right along with everything else instead of jumping
        # ahead when playback resumes.
        self._effector_t0 = time.monotonic()
        # Media playback clock (item 15) — canvas-wide, and reset when the
        # canvas CHANGES (not on every slot rebuild, which also fires for an
        # unrelated shape being added: that would restart every "once" video
        # mid-show). Freeze/blackout shift it by dt exactly like the effector
        # clock and every slot.t0, so video stops and resumes with everything
        # else instead of running on underneath.
        self._media_t0 = time.monotonic()
        self._media_canvas = None
        # Elapsed canvas time as of the current tick — set once at the top of
        # _loop so every layer built during that tick agrees, rather than
        # each one re-reading the clock a few microseconds apart.
        self._media_now = 0.0
        self._last_effector_values: dict = {}   # source id -> last sampled value, for UI meters
        # Live mic analysis (see effectors.py's "audio" source type) — a
        # browser (the control panel, not output windows) streams these up
        # via set_audio_bands; NOT a function of t, just "whatever was last
        # reported", same category as modulation.py's Value class.
        self._audio_bands = {"low": 0.0, "mid": 0.0, "high": 0.0}

    # lifecycle ---------------------------------------------------------
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def _enqueue(self, fn):
        with self._lock:
            self._queue.append(fn)

    # master gates (queued) ----------------------------------------------
    def set_active(self, value: bool):
        self._enqueue(lambda: setattr(self, "active", bool(value)))

    def set_frozen(self, value: bool):
        self._enqueue(lambda: setattr(self, "frozen", bool(value)))

    def disable_visuals(self, fade: float = 2.0):
        def apply():
            self.visuals_disabled = True
            self._gain_from = self.master_gain
            self._gain_to = 0.0
            self._gain_fade_dur = max(0.0, float(fade))
            self._gain_fade_pos = 0.0
            self._gain_fade_on_complete = None   # supersedes any pending crossfade swap
        self._enqueue(apply)

    def enable_visuals(self, fade: float = 2.0):
        def apply():
            self.visuals_disabled = False
            self._gain_from = self.master_gain
            self._gain_to = 1.0
            self._gain_fade_dur = max(0.0, float(fade))
            self._gain_fade_pos = 0.0
            self._gain_fade_on_complete = None
        self._enqueue(apply)

    def set_diagnostics(self, value: bool):
        self._enqueue(lambda: setattr(self, "_diag_enabled", bool(value)))

    # project switching/CRUD (queued) -------------------------------------
    def _do_set_project(self, name: str):
        """Not queued itself — call only from inside another queued
        callable, so a whole project switch (retarget canvases, retarget
        sequencer, load the active canvas, clear stale slots, rebind
        current_project) commits as one atomic step. Otherwise a broadcast
        tick reading `canvases.current` and `current_project` between two
        separate queued steps could see project A's name next to project
        B's canvas."""
        spec = self.projects.load(name)
        self.canvases.retarget(self.projects.canvas_dir(name))
        self.sequencer.retarget(self.projects.sequence_path(name))
        if spec.active_canvas and spec.active_canvas in self.canvases.names():
            self.canvases.current = self.canvases.load_spec(spec.active_canvas)
        self._slots = {}
        self._canvas_sig = None
        self.current_project = spec
        # Opening a project adopts its own frame rate (ProjectSpec.fps) —
        # otherwise the number shown in the Project tab would disagree with
        # what the loop is actually running at until you touched the field.
        self.fps = max(1, min(120, int(spec.fps)))

    def set_project(self, name: str):
        self._enqueue(lambda: self._do_set_project(name))

    def new_project(self, name: str):
        def apply():
            self.projects.create(name)
            self._do_set_project(name)
        self._enqueue(apply)

    def save_project(self, name: str):
        def apply():
            spec = self.current_project or ProjectSpec(name=name)
            spec.name = name
            # Only a canvas that's actually been saved makes sense as
            # "reopen here next time" — an untitled in-progress canvas isn't
            # addressable by name yet.
            cur = self.canvases.current
            if cur.name and cur.name != "untitled":
                self.canvases.save(cur.name)
                spec.active_canvas = cur.name
            self.projects.save(name, spec)
            self.current_project = spec
        self._enqueue(apply)

    def save_project_as(self, src_name: str, dst_name: str):
        def apply():
            self.projects.duplicate(src_name, dst_name)
            self._do_set_project(dst_name)
        self._enqueue(apply)

    def delete_project(self, name: str):
        self._enqueue(lambda: self.projects.delete(name))

    # canvas editing (queued) --------------------------------------------
    def new_canvas(self):
        self._enqueue(lambda: setattr(
            self.canvases, "current", CanvasSpec(name="untitled", polygons=[PolygonSpec()])))

    def set_project_resolution(self, width: int, height: int):
        # Lives on the project (see projects.py's ProjectSpec) — every
        # canvas in this project's sequencer shares one output resolution,
        # not a per-canvas setting. A no-op before any project is loaded
        # (shouldn't happen in practice — run.py bootstraps a default
        # project on first launch), rather than crashing the render thread.
        def apply():
            if self.current_project is None:
                return
            self.current_project.width = max(1, min(16384, int(width)))
            self.current_project.height = max(1, min(16384, int(height)))
        self._enqueue(apply)

    def set_project_notes(self, notes: str):
        def apply():
            if self.current_project is None:
                return
            self.current_project.notes = str(notes)[:20000]
        self._enqueue(apply)

    def set_output_config(self, index: int, key: str, value):
        # Per-output flip/keystone/viewport — see projects.py's
        # OutputMonitorConfig for the full rationale (moved off browser
        # localStorage, now lives on the project). Grows the list to fit
        # `index` rather than bounds-checking it away, so a future output
        # can set its config the moment it exists, no schema change needed
        # here — though in practice set_output_count (below) is what
        # actually changes "how many outputs" now.
        def apply():
            if self.current_project is None:
                return
            outs = self.current_project.outputs
            while len(outs) <= index:
                outs.append(OutputMonitorConfig())
            cfg = outs[index]
            if key == "flip_x":
                cfg.flip_x = bool(value)
            elif key == "flip_y":
                cfg.flip_y = bool(value)
            elif key == "keystone_h":
                cfg.keystone_h = max(-0.5, min(0.5, float(value)))
            elif key == "keystone_v":
                cfg.keystone_v = max(-0.5, min(0.5, float(value)))
            elif key == "viewport_x":
                cfg.viewport_x = max(0.0, min(1.0, float(value)))
            elif key == "viewport_y":
                cfg.viewport_y = max(0.0, min(1.0, float(value)))
            elif key == "viewport_w":
                cfg.viewport_w = max(0.01, min(1.0, float(value)))
            elif key == "viewport_h":
                cfg.viewport_h = max(0.01, min(1.0, float(value)))
            elif key == "duplicate_of":
                if value is None:
                    cfg.duplicate_of = None
                else:
                    v = int(value)
                    cfg.duplicate_of = v if 0 <= v < len(outs) and v != index else None
        self._enqueue(apply)

    def set_output_count(self, count: int):
        # "Number of outputs" (Project tab) — the ONE place that controls
        # how many outputs this project drives; the header's Output N
        # buttons and the Output Preview tab's tiles are both generated
        # from len(outputs), so this is what actually grows/shrinks them.
        # Shrinking drops the trailing configs outright (no undo here yet —
        # matches every other edit-in-memory-until-Save field, a re-open
        # without saving recovers them).
        def apply():
            if self.current_project is None:
                return
            n = max(1, min(8, int(count)))
            outs = self.current_project.outputs
            while len(outs) < n:
                outs.append(OutputMonitorConfig())
            del outs[n:]
            # An output can't duplicate an index that no longer exists.
            for cfg in outs:
                if cfg.duplicate_of is not None and cfg.duplicate_of >= n:
                    cfg.duplicate_of = None
        self._enqueue(apply)

    def set_viewports_enabled(self, value: bool):
        def apply():
            if self.current_project is None:
                return
            self.current_project.viewports_enabled = bool(value)
        self._enqueue(apply)

    def set_show_fps(self, value: bool):
        def apply():
            if self.current_project is None:
                return
            self.current_project.show_fps = bool(value)
        self._enqueue(apply)

    def set_project_fps(self, value: int):
        """Per-project render/broadcast rate (see ProjectSpec.fps). Writes
        BOTH the spec (so Save persists it) and self.fps (so it takes effect
        on the very next tick, without a restart) — server.py's broadcaster
        reads self.fps every iteration for exactly this reason."""
        def apply():
            fps = max(1, min(120, int(value)))
            self.fps = fps
            if self.current_project is not None:
                self.current_project.fps = fps
        self._enqueue(apply)

    def set_test_pattern(self, index: int, value: bool):
        self._enqueue(lambda: self._test_pattern.__setitem__(int(index), bool(value)))

    def load_canvas(self, name: str):
        def apply():
            self.canvases.current = self.canvases.load_spec(name)
        self._enqueue(apply)

    def save_canvas(self, name: str):
        self._enqueue(lambda: self.canvases.save(name))

    def delete_canvas(self, name: str):
        self._enqueue(lambda: self.canvases.delete(name))

    # polygon editing (queued) -------------------------------------------
    def default_half_height(self, target_aspect: float = 16 / 9) -> float:
        """Half-height, in canvas [-1,1] units, of a new quad that should
        LOOK `target_aspect` wide once drawn (18).

        The canvas is [-1,1] on both axes but renders into the project's own
        pixel rect, so normalized units are not square on screen: a quad of
        half-width hw and half-height hh appears with aspect
        (hw/hh) * (project_w/project_h). Solving that for hw=0.5 gives the
        expression below.

        The previous formula was `0.5 / aspect`, which is this inverted —
        it multiplied the project's distortion instead of cancelling it, so
        a new shape on a 1920x1080 project came out 3.16:1 rather than 16:9.
        That's the "very stretched" default; text and media were worst
        because their content has a definite shape of its own to disagree
        with.
        """
        if self.current_project is None or not self.current_project.height:
            return 0.5 * (1.0 / target_aspect)
        aspect = self.current_project.width / self.current_project.height
        return 0.5 * aspect / max(0.01, target_aspect)

    def add_polygon(self, scene: str | None = None):
        def apply():
            poly = PolygonSpec(scene=scene, label=scene or "")
            if self.current_project is not None and self.current_project.height:
                half_h = self.default_half_height()
                poly.corners = [[-0.5, half_h], [0.5, half_h], [0.5, -half_h], [-0.5, -half_h]]
            n = len(self.canvases.current.polygons)
            if n:
                # stagger successive adds so they don't stack exactly on
                # top of each other
                dx, dy = 0.06 * (n % 5), -0.06 * (n % 5)
                poly.corners = [[x + dx, y + dy] for x, y in poly.corners]
            self.canvases.current.polygons.append(poly)
        self._enqueue(apply)

    def delete_polygon(self, poly_id: str):
        def apply():
            self.canvases.current.polygons = [
                p for p in self.canvases.current.polygons if p.id != poly_id]
            self._slots.pop(poly_id, None)
        self._enqueue(apply)

    def set_polygon_corners(self, poly_id: str, corners: list):
        def apply():
            poly = self._find_polygon(poly_id)
            if poly is not None:
                poly.corners = [[float(x), float(y)] for x, y in corners]
        self._enqueue(apply)

    def reorder_polygon(self, poly_id: str, index: int):
        def apply():
            polys = self.canvases.current.polygons
            cur = next((i for i, p in enumerate(polys) if p.id == poly_id), None)
            if cur is None:
                return
            p = polys.pop(cur)
            polys.insert(max(0, min(index, len(polys))), p)
        self._enqueue(apply)

    def set_polygon(self, poly_id: str, key: str, value):
        def apply():
            poly = self._find_polygon(poly_id)
            if poly is None:
                return
            if key == "scene":
                poly.scene = value or None
            elif key == "source_type":
                poly.source_type = value if value in (
                    "scene", "media", "knockout", "webcam", "text") else "scene"
            elif key == "media":
                poly.media = value or None
            elif key == "media_link":
                # Validated on the way in for SHAPE (does it name a real
                # root, stay inside it, and point at a media extension) but
                # deliberately NOT for existence: a link whose file is
                # currently missing — external drive unplugged mid-set — must
                # survive, or any unrelated edit would silently destroy it.
                # Missing files surface as media_link_ok=False and an amber
                # "file not found" in the inspector instead.
                from .media_roots import resolve as _resolve_link
                poly.media_link = value if (value and _resolve_link(value)) else None
            elif key == "webcam_device":
                poly.webcam_device = value or None
            elif key == "text_content":
                poly.text_content = str(value or "")[:2000]
            elif key == "text_color":
                poly.text_color = str(value or "#ffffff")
            elif key == "text_size":
                poly.text_size = max(0.02, min(1.0, float(value)))
            elif key == "label":
                poly.label = str(value or "")
            elif key == "opacity":
                poly.opacity = max(0.0, min(1.0, float(value)))
            elif key in ("media_in", "media_out"):
                # None is meaningful here (= inherit the asset's own default
                # trim), so an empty value clears the override rather than
                # being coerced to 0.
                setattr(poly, key, None if value is None else max(0.0, float(value)))
            elif key == "media_mode":
                poly.media_mode = value if value in ("loop", "once", "once_hold") else "loop"
            elif key == "media_rate":
                poly.media_rate = max(0.05, min(8.0, float(value)))
            elif key == "media_offset":
                poly.media_offset = max(0.0, float(value))
            elif key == "time_offset":
                poly.time_offset = float(value)
            elif key == "clip_shape":
                poly.clip_shape = value if value in (
                    "circle", "hexagon", "triangle", "square", "custom") else None
                # Seed a new custom mask with the quad's own outline so
                # switching it on is a visual no-op the user then carves
                # away from — an empty point list would otherwise read as
                # "the shape just vanished".
                if poly.clip_shape == "custom" and len(poly.clip_points) < 3:
                    poly.clip_points = [list(p) for p in DEFAULT_CLIP_POINTS]
            elif key == "locked":
                poly.locked = bool(value)
            elif key == "clip_points":
                poly.clip_points = sanitize_clip_points(value)
            elif key == "clip_scale":
                poly.clip_scale = max(0.1, min(3.0, float(value)))
            elif key == "z_index":
                poly.z_index = max(1, min(10, int(value)))
            elif key == "fit":
                poly.fit = value if value in ("stretch", "fit", "crop") else "stretch"
            elif key == "transform_anchor":
                from .effectors import ANCHOR_POINTS
                poly.transform_anchor = value if value in ANCHOR_POINTS else poly.transform_anchor
            elif key in ("glow", "trail"):
                poly.overrides[key] = max(0.0, float(value))
            elif key in ("brightness", "contrast"):
                # Applied client-side via ctx.filter around a layer's whole
                # draw call (see paintCanvasEditor/paintComposite in the two
                # HTML files) — 1.0 = neutral, same convention as CSS's own
                # brightness()/contrast() filter functions. Unlike glow/trail
                # (vector-only, a stroke shadow/window-persistence effect)
                # this applies to any source_type, scene or raster alike.
                poly.overrides[key] = max(0.0, min(3.0, float(value)))
            elif key == "hue":
                # Degrees, same convention/range as CSS's own hue-rotate() —
                # 0 = neutral. Applies to any source_type, same as brightness/contrast.
                poly.overrides[key] = max(-180.0, min(180.0, float(value)))
            elif key == "saturation":
                poly.overrides[key] = max(0.0, min(3.0, float(value)))
            elif key == "colourize_amount":
                # 0 = original colours, 1 = fully tinted toward colourize_hue.
                # See paintCanvasEditor/paintComposite's ctx.filter comment for
                # the sepia()+hue-rotate() approximation this drives — a true
                # colour tint (collapse toward one hue), not a hue-rotate of
                # existing colours the way `hue` above is.
                poly.overrides[key] = max(0.0, min(1.0, float(value)))
            elif key == "colourize_hue":
                poly.overrides[key] = max(0.0, min(360.0, float(value)))
            elif key in ("mirror_x", "mirror_y", "disable_plane"):
                poly.overrides[key] = bool(value)
            elif key in ("mirror_x_point", "mirror_y_point"):
                # 1.0 = off (shows the original, untouched) — see the fold
                # maths in paintCanvasEditor/paintComposite (both HTML
                # files): a point below 1 keeps content up to that fraction
                # and mirrors it to fill the rest, clamping once the source
                # region runs out rather than tiling/repeating.
                poly.overrides[key] = max(0.0, min(1.0, float(value)))
            elif key == "mirror_pre_distort":
                poly.overrides[key] = bool(value)
            elif key == "scale":
                # Static authored scale (Transform drawer) — the BASE an
                # effector "scale" route then multiplies, see
                # _apply_effectors/_blackout_layer. 1.0 = original size.
                poly.overrides[key] = max(0.05, min(5.0, float(value)))
            elif key == "rotation":
                # Degrees, static authored (Transform drawer) — the BASE an
                # effector "rotation" route then adds to.
                poly.overrides[key] = max(-180.0, min(180.0, float(value)))
            elif key.startswith("layer0."):
                attr = key.split(".", 1)[1]
                poly.overrides.setdefault("layer0", {})[attr] = float(value)
                # apply live, without waiting for a slot rebuild
                slot = self._slots.get(poly_id)
                if slot is not None and slot.scene._gens:
                    slot.scene._gens[0][1][attr] = float(value)
            elif key.startswith("camera."):
                attr = key.split(".", 1)[1]
                poly.overrides.setdefault("camera", {})[attr] = value
                # apply live, without waiting for a slot rebuild — mirrors
                # the layer0 branch above
                slot = self._slots.get(poly_id)
                if slot is not None:
                    _apply_camera_overrides(slot.scene.camera, {attr: value})
        self._enqueue(apply)

    def _find_polygon(self, poly_id: str) -> PolygonSpec | None:
        return next((p for p in self.canvases.current.polygons if p.id == poly_id), None)

    # effector matrix editing (queued) — see effectors.py. All mutate
    # self.canvases.current.effectors in place, same discipline as polygon
    # editing above: queued, applied at the top of _loop.
    def set_tempo(self, bpm: float):
        self._enqueue(lambda: setattr(
            self.canvases.current.effectors, "tempo_bpm", max(1.0, min(400.0, float(bpm)))))

    def set_audio_bands(self, low: float, mid: float, high: float):
        # Not per-canvas (unlike sources/routes/tempo) — the mic feed is a
        # global live signal from whichever browser tab is capturing it, same
        # engine-wide scope as e.g. `active`/`frozen`, not creative config
        # that travels with one canvas.
        def apply():
            low_c = max(0.0, min(1.0, float(low)))
            mid_c = max(0.0, min(1.0, float(mid)))
            high_c = max(0.0, min(1.0, float(high)))
            # "master" (2) — the combined/overall level, derived here rather
            # than sent separately over the wire: it's a pure function of the
            # 3 bands the browser already sends, so there's nothing for the
            # client to compute or transmit beyond what it already does.
            self._audio_bands = {
                "low": low_c, "mid": mid_c, "high": high_c,
                "master": (low_c + mid_c + high_c) / 3.0,
            }
        self._enqueue(apply)

    def effector_source_add(self):
        self._enqueue(lambda: self.canvases.current.effectors.sources.append(EffectorSource()))

    def effector_source_remove(self, source_id: str):
        def apply():
            fx = self.canvases.current.effectors
            fx.sources = [s for s in fx.sources if s.id != source_id]
            fx.routes = [r for r in fx.routes if r.source_id != source_id]
        self._enqueue(apply)

    def effector_source_set(self, source_id: str, key: str, value):
        def apply():
            from .effectors import LFO_SHAPES, SOURCE_TYPES, AUDIO_BANDS, _SYNC_BEATS
            src = next((s for s in self.canvases.current.effectors.sources if s.id == source_id), None)
            if src is None:
                return
            if key == "name":
                src.name = str(value or "LFO")[:60]
            elif key == "type":
                src.type = value if value in SOURCE_TYPES else src.type
            elif key == "shape":
                src.shape = value if value in LFO_SHAPES else src.shape
            elif key == "rate_hz":
                src.rate_hz = max(0.001, min(50.0, float(value)))
            elif key == "sync":
                src.sync = value if (value in _SYNC_BEATS or value is None) else src.sync
            elif key == "phase":
                src.phase = float(value) % 1.0
            elif key == "pulse_width":
                src.pulse_width = max(0.01, min(0.99, float(value)))
            elif key == "band":
                src.band = value if value in AUDIO_BANDS else src.band
        self._enqueue(apply)

    def effector_route_add(self):
        self._enqueue(lambda: self.canvases.current.effectors.routes.append(EffectorRoute()))

    def effector_route_remove(self, route_id: str):
        def apply():
            fx = self.canvases.current.effectors
            fx.routes = [r for r in fx.routes if r.id != route_id]
        self._enqueue(apply)

    def effector_route_set(self, route_id: str, key: str, value):
        def apply():
            route = next((r for r in self.canvases.current.effectors.routes if r.id == route_id), None)
            if route is None:
                return
            if key == "source_id":
                route.source_id = str(value or "")
            elif key == "target_poly":
                route.target_poly = str(value or "")
            elif key == "target_param":
                from .effectors import TARGET_PARAMS
                route.target_param = value if value in TARGET_PARAMS else route.target_param
            elif key == "depth":
                # Wide range on purpose — depth multiplies a -1..1 source
                # output, and target params have wildly different natural
                # scales (opacity 0..1 vs. hue's -180..180), so it has to
                # comfortably cover the largest of them.
                route.depth = max(-360.0, min(360.0, float(value)))
            elif key == "offset":
                route.offset = max(-360.0, min(360.0, float(value)))
            elif key == "curve":
                from .effectors import CURVES
                route.curve = value if value in CURVES else route.curve
            elif key == "mode":
                from .effectors import MODES
                route.mode = value if value in MODES else route.mode
        self._enqueue(apply)

    def _media_playback(self, poly: PolygonSpec, t: float) -> dict:
        """Where a media shape's playhead should be right now, computed HERE
        rather than in the browser.

        The compositor already owns time — generated scenes are pure
        functions of t (that's what time_offset is for), and video is now the
        same: position is derived from the canvas clock, not from letting
        each <video> free-run. That matters for more than tidiness. Every
        output window is a separate page with its own <video> elements; left
        to themselves they drift apart within seconds, so one clip spanning
        two projectors through a viewport crop (see projects.py's
        OutputMonitorConfig) would show a different frame on each. Publishing
        one authoritative time per layer means every client converges on the
        same frame, and scrubbing the sequencer is reproducible.

        Returns {"t": seconds|None, "visible": bool, "span": seconds|None,
        "native_loop": bool}. t=None means "duration unknown, just let it
        play" — the honest answer before any client has reported how long the
        file is.

        `span` and `native_loop` exist for the client's seek logic, not for
        drawing. A clip whose trim covers the whole file wraps by itself via
        <video loop>, so when our target wraps the element is ALREADY there
        and a corrective seek would stall decode (0.5-1.2s on high-bitrate
        media) to fix nothing. `span` additionally lets the client fold the
        drift circularly across a wrap boundary, where element and server can
        briefly sit a whole span apart while both are effectively in the same
        place.
        """
        key = media_ref_key(poly)
        meta = media_meta.get(key) if key else {}
        duration = meta.get("duration")
        in_s = poly.media_in if poly.media_in is not None else meta.get("in", 0.0) or 0.0
        out_s = poly.media_out if poly.media_out is not None else meta.get("out")
        if out_s is None:
            out_s = duration
        if out_s is None or duration is None:
            return {"t": None, "visible": True, "span": None, "native_loop": False}
        in_s = max(0.0, min(float(in_s), duration))
        out_s = max(in_s, min(float(out_s), duration))
        span = out_s - in_s
        if span <= 0.001:
            return {"t": in_s, "visible": True, "span": 0.0, "native_loop": False}
        # Native only when the element's own wrap point coincides with ours,
        # i.e. the trim covers the whole file. Rate and offset don't need
        # checking: the client sets playbackRate to media_rate and keeps the
        # element on target, so an element sitting at `target` reaches
        # duration exactly when target reaches span. The client still
        # verifies the element actually landed before trusting this.
        native = (
            poly.media_mode == "loop"
            and in_s <= 0.001
            and out_s >= duration - 0.001
        )
        local = t * poly.media_rate + poly.media_offset
        if poly.media_mode == "loop":
            local = local % span
            return {"t": in_s + local, "visible": True, "span": span,
                    "native_loop": native}
        # once / once_hold: clamp at the out point; "once" then stops drawing
        # while "once_hold" keeps showing that last frame.
        if local >= span:
            return {"t": out_s, "visible": poly.media_mode == "once_hold",
                    "span": span, "native_loop": False}
        return {"t": in_s + local, "visible": True, "span": span,
                "native_loop": False}

    def _media_playback_payload(self, poly: PolygonSpec) -> dict:
        if poly.source_type != "media":
            return {"media_t": None, "media_visible": True,
                    "media_span": None, "media_native_loop": False}
        pb = self._media_playback(poly, self._media_now)
        return {"media_t": pb["t"], "media_visible": pb["visible"],
                "media_span": pb["span"], "media_native_loop": pb["native_loop"]}

    def _poly_layer_base(self, poly: PolygonSpec) -> dict:
        """The layer-payload fields that come straight off the PolygonSpec,
        common to all three call sites below (blackout / missing-scene /
        rendered) — factored out so adding a new per-polygon field (as with
        webcam/text/clip_scale) is one edit instead of three."""
        return {
            "id": poly.id, "corners": poly.corners, "opacity": poly.opacity,
            "clip_shape": poly.clip_shape, "clip_scale": poly.clip_scale,
            "clip_points": poly.clip_points,
            "source_type": poly.source_type, "z_index": poly.z_index, "media": poly.media,
            "webcam_device": poly.webcam_device, "text_content": poly.text_content,
            "media_link": poly.media_link,
            "media_mode": poly.media_mode, "media_rate": poly.media_rate,
            # Playback config signature: the CLIENT keys its <video> cache on
            # this, so two shapes with identical playback share one decoder
            # (and stay frame-identical), while differing ones get their own.
            "media_cfg": media_cfg_sig(poly),
            **self._media_playback_payload(poly),
            # Resolved server-side (cached, see media_roots._EXISTS_TTL) so
            # the inspector can show the real path plus a found/missing
            # state without the browser needing filesystem access.
            "media_link_path": media_link_path(poly.media_link),
            "media_link_ok": bool(poly.media_link) and link_exists(poly.media_link),
            "text_color": poly.text_color, "text_size": poly.text_size,
            "fit": poly.fit, "locked": poly.locked,
        }

    def _update_effector_meters(self, t: float):
        """Samples sources for the UI's live meters ONLY — no route
        resolution, nothing written to any layer. Used while stopped (5): a
        source's meter should still read live so you can preview/tune an
        LFO before ever pressing Start, but the actual shape params must
        NOT be modulated while stopped — see the blackout branch in _loop,
        which deliberately calls this instead of _apply_effectors so
        blacked-out layers keep the ORIGINAL, unmodulated corners/hue/etc
        that _blackout_layer already built them with, rather than getting
        overwritten with wherever an LFO happened to leave them."""
        canvas = self.canvases.current
        self._last_effector_values = canvas.effectors.source_values(t, self._audio_bands)

    def _apply_effectors(self, layers: list, t: float):
        """Post-processes already-built layer dicts in place (2/3) — cheaper
        than threading effector resolution through every one of
        _poly_layer_base's three call sites, and correct regardless of
        which of them produced a given layer (scene slot, missing slot, or
        blackout — see effectors.py's own docstring on why this covers
        media/webcam/text too, not just scene shapes). A layer whose id has
        no route targeting it just gets its own existing value back — a
        no-op, not a special case."""
        canvas = self.canvases.current
        polys_by_id = {p.id: p for p in canvas.polygons}
        base = {}
        for p in canvas.polygons:
            ov = p.overrides
            base[(p.id, "hue")] = float(ov.get("hue", 0.0))
            base[(p.id, "saturation")] = float(ov.get("saturation", 1.0))
            base[(p.id, "brightness")] = float(ov.get("brightness", 1.0))
            base[(p.id, "opacity")] = float(p.opacity)
            # Geometry (2) — deltas around transform_anchor, neutral at
            # (0, 0, 1, 0); see effectors.py's transform_corners.
            base[(p.id, "position_x")] = 0.0
            base[(p.id, "position_y")] = 0.0
            # Static authored scale/rotation (Transform drawer) are now the
            # BASE an effector route modulates around, not always-neutral —
            # e.g. a "multiply" scale route multiplies THIS value, not a
            # hardcoded 1.0. A route-free polygon just gets its own authored
            # value back unchanged (see this method's own docstring), which
            # is what makes a static (no effector) rotate/scale work at all:
            # the gate below already fires on "differs from neutral", and an
            # authored 15° rotation already does that with zero routes.
            base[(p.id, "scale")] = float(ov.get("scale", 1.0))
            base[(p.id, "rotation")] = float(ov.get("rotation", 0.0))
        fx = canvas.effectors.resolve(t, base, self._audio_bands)
        self._last_effector_values = canvas.effectors.source_values(t, self._audio_bands)
        for layer in layers:
            pid = layer["id"]
            layer["hue"] = fx.get((pid, "hue"), layer.get("hue", 0.0))
            layer["saturation"] = fx.get((pid, "saturation"), layer.get("saturation", 1.0))
            layer["brightness"] = fx.get((pid, "brightness"), layer.get("brightness", 1.0))
            layer["opacity"] = fx.get((pid, "opacity"), layer.get("opacity", 1.0))
            dx = fx.get((pid, "position_x"), 0.0)
            dy = fx.get((pid, "position_y"), 0.0)
            scale = fx.get((pid, "scale"), 1.0)
            rotation = fx.get((pid, "rotation"), 0.0)
            if dx or dy or scale != 1.0 or rotation:
                poly = polys_by_id.get(pid)
                if poly is not None:
                    layer["corners"] = transform_corners(
                        poly.corners, poly.transform_anchor, dx, dy, scale, rotation)

    # sequencer editing/transport (queued) --------------------------------
    def seq_add(self, canvas: str, duration: float = 8.0, crossfade: float = 0.0):
        self._enqueue(lambda: self.sequencer.add_step(canvas, duration, crossfade))

    def seq_remove(self, step_id: str):
        self._enqueue(lambda: self.sequencer.remove_step(step_id))

    def seq_reorder(self, step_id: str, index: int):
        self._enqueue(lambda: self.sequencer.reorder_step(step_id, index))

    def seq_set(self, step_id: str, canvas: str | None = None, duration: float | None = None,
                crossfade: float | None = None):
        self._enqueue(lambda: self.sequencer.set_step(
            step_id, canvas=canvas, duration=duration, crossfade=crossfade))

    def seq_set_loop(self, value: bool):
        self._enqueue(lambda: self.sequencer.set_loop(value))

    def seq_play(self):
        self._enqueue(self.sequencer.play)

    def seq_pause(self):
        self._enqueue(self.sequencer.pause)

    def seq_stop(self):
        self._enqueue(self.sequencer.stop)

    # Manual transport (next/prev/goto) goes through the same crossfade-
    # aware swap as auto-advance (_swap_sequencer_canvas), not a bare
    # sequencer.next()/prev() — that old version only ever moved the
    # sequencer's own index/elapsed bookkeeping and never actually touched
    # canvases.current, so the buttons updated the step label but the
    # rendered/output canvas silently never changed until the NEXT natural
    # duration timeout. A step's own crossfade value now governs how you
    # arrive at it whether by timer or by clicking.
    def seq_next(self):
        def apply():
            self.sequencer.next()
            self._swap_sequencer_canvas()
        self._enqueue(apply)

    def seq_prev(self):
        def apply():
            self.sequencer.prev()
            self._swap_sequencer_canvas()
        self._enqueue(apply)

    def seq_goto(self, index: int):
        def apply():
            self.sequencer.goto(index)
            self._swap_sequencer_canvas()
        self._enqueue(apply)

    def _swap_sequencer_canvas(self):
        step = self.sequencer.current_step()
        if step is None or not step.canvas:
            return
        xfade = max(0.0, float(getattr(step, "crossfade", 0.0) or 0.0))
        name = step.canvas
        if xfade <= 0:
            # Also snaps master_gain outright (not just clearing the
            # completion hook) — a step whose OWN duration is shorter than
            # the crossfade it interrupts would otherwise leave a stale fade
            # still animating toward whatever gain the interrupted one
            # wanted, a step behind this instant swap. Target is 0 rather
            # than 1 if visuals are deliberately disabled — this instant
            # swap must not un-dim a manual Disable Visuals.
            target = 0.0 if self.visuals_disabled else 1.0
            self._gain_fade_on_complete = None
            self._gain_to = target
            self._gain_fade_dur = 0.0
            self._gain_fade_pos = 0.0
            self.master_gain = target
            try:
                self.canvases.current = self.canvases.load_spec(name)
            except Exception as e:
                print(f"[lightsaber] sequencer: could not load canvas {name!r}: {e}")
            return
        # Dip through black rather than a true cross-dissolve (that would
        # need rendering the outgoing AND incoming canvas simultaneously —
        # a materially bigger change to the render loop): fade master_gain
        # to 0 over half the crossfade, swap the (invisible) canvas, then
        # fade back to 1 over the other half. Reuses the exact same fade
        # machinery as Disable Visuals (see disable_visuals/enable_visuals).
        def _swap_in(name=name, half=xfade / 2):
            try:
                self.canvases.current = self.canvases.load_spec(name)
            except Exception as e:
                print(f"[lightsaber] sequencer: could not load canvas {name!r}: {e}")
            self._gain_from = 0.0
            self._gain_to = 0.0 if self.visuals_disabled else 1.0
            self._gain_fade_dur = half
            self._gain_fade_pos = 0.0
        self._gain_from = self.master_gain
        self._gain_to = 0.0
        self._gain_fade_dur = xfade / 2
        self._gain_fade_pos = 0.0
        self._gain_fade_on_complete = _swap_in

    # slot management -------------------------------------------------------
    def _rebuild_if_needed(self, now: float):
        canvas = self.canvases.current
        if canvas.name != self._media_canvas:
            self._media_canvas = canvas.name
            self._media_t0 = now
        sig = (canvas.name, tuple((p.id, p.scene) for p in canvas.polygons))
        if sig == self._canvas_sig:
            return
        self._canvas_sig = sig
        new_slots: dict[str, _Slot] = {}
        for poly in canvas.polygons:
            if not poly.scene:
                continue
            existing = self._slots.get(poly.id)
            if existing is not None and existing.scene_name == poly.scene:
                new_slots[poly.id] = existing
                continue
            try:
                spec = self._scene_loader(poly.scene)
            except Exception as e:
                print(f"[lightsaber] composite: could not load scene {poly.scene!r}: {e}")
                continue
            matrix = ModMatrix()
            matrix.add_source("lfo_slow", LFO(rate=0.05, shape="sine"))
            matrix.add_source("lfo_mid", LFO(rate=0.2, shape="triangle"))
            for r in spec.modulation:
                matrix.add_route(r.get("source", "lfo_slow"), r.get("dest", ""),
                                  float(r.get("depth", 1.0)), float(r.get("bias", 0.0)))
            slot = _Slot(Scene(spec), matrix, now, poly.scene)
            layer0 = poly.overrides.get("layer0")
            if layer0 and slot.scene._gens:
                slot.scene._gens[0][1].update(layer0)
            camera_ov = poly.overrides.get("camera")
            if camera_ov:
                _apply_camera_overrides(slot.scene.camera, camera_ov)
            new_slots[poly.id] = slot
        self._slots = new_slots

    # loop --------------------------------------------------------------
    def _loop(self):
        period = 1.0 / self.fps
        prev = time.monotonic()
        while self._running:
            now = time.monotonic()
            dt = now - prev
            prev = now

            with self._lock:
                q, self._queue = self._queue, []
            for fn in q:
                try:
                    fn()
                except Exception as e:
                    print(f"[lightsaber] composite action error: {e}")

            if not self.active:
                # Master Start/Stop: freeze every slot's clock (shift t0
                # forward by this tick's dt, same trick Engine._loop used)
                # and publish nothing, so output windows go black — the
                # show's blackout. Sequencer does not advance either.
                for slot in self._slots.values():
                    slot.t0 += dt
                self._effector_t0 += dt
                self._media_t0 += dt
                def _blackout_layer(p):
                    ov = p.overrides
                    # Static authored scale/rotation (Transform drawer) are a
                    # base-pose property, not a live effector animation — it
                    # must keep showing while stopped, unlike the corner
                    # modulation _apply_effectors does (which this blackout
                    # branch deliberately skips so LIVE routes reset to the
                    # unmodulated pose — see this function's own comment
                    # further down). So it's applied here by hand instead.
                    static_scale = float(ov.get("scale", 1.0))
                    static_rotation = float(ov.get("rotation", 0.0))
                    corners = p.corners
                    if static_scale != 1.0 or static_rotation:
                        corners = transform_corners(
                            p.corners, p.transform_anchor, 0.0, 0.0, static_scale, static_rotation)
                    return {
                        **self._poly_layer_base(p),
                        "corners": corners,
                        "missing": False, "is_3d": False,
                        "glow": float(ov.get("glow", 0.0)), "trail": float(ov.get("trail", 0.0)),
                        "mirror_x": bool(ov.get("mirror_x", False)), "mirror_y": bool(ov.get("mirror_y", False)),
                        "mirror_x_point": float(ov.get("mirror_x_point", 1.0)),
                        "mirror_y_point": float(ov.get("mirror_y_point", 1.0)),
                        "mirror_pre_distort": bool(ov.get("mirror_pre_distort", True)),
                        "brightness": float(ov.get("brightness", 1.0)), "contrast": float(ov.get("contrast", 1.0)),
                        "hue": float(ov.get("hue", 0.0)), "saturation": float(ov.get("saturation", 1.0)),
                        "colourize_amount": float(ov.get("colourize_amount", 0.0)),
                        "colourize_hue": float(ov.get("colourize_hue", 0.0)),
                        "aspect": "1:1",
                        "frame": [],
                    }
                self._last_layers = [_blackout_layer(p) for p in self.canvases.current.polygons]
                # Meters only (5) — NOT the full _apply_effectors: while
                # stopped, shapes must show their ORIGINAL, unmodulated
                # corners/hue/etc (_blackout_layer already built them that
                # way from poly.overrides/poly.corners directly), not
                # wherever an LFO happened to leave them. Source meters
                # still read live though — the UI's own debugging affordance
                # (PROMPT-effectors.md), useful to preview/tune before Start.
                self._update_effector_meters(now - self._effector_t0)
                self.perf.tick()
                sleep = period - (time.monotonic() - now)
                if sleep > 0:
                    time.sleep(sleep)
                continue

            if self.frozen:
                # Same t0-shift trick the `active=False` branch above uses,
                # just without skipping the render/publish below — content
                # stays on screen, at whatever instant it was frozen at.
                # Also holds the sequencer's playhead, so the canvas being
                # edited can't get swapped out from under you mid-edit.
                for slot in self._slots.values():
                    slot.t0 += dt
                self._effector_t0 += dt
                self._media_t0 += dt
            elif self.sequencer.tick(dt):
                self._swap_sequencer_canvas()

            self._rebuild_if_needed(now)
            # One clock reading per tick, so every layer built below agrees.
            self._media_now = now - self._media_t0

            if self._diag_enabled:
                t_render0 = time.monotonic()

            # "Disable Visuals" fade — see disable_visuals/enable_visuals;
            # published as master_gain, applied client-side (both painters
            # multiply it into each shape's opacity) rather than scaling
            # colours here the way the old single-scene loop did per point.
            if self._gain_fade_pos < self._gain_fade_dur:
                self._gain_fade_pos += dt
                prog = min(1.0, self._gain_fade_pos / self._gain_fade_dur)
                self.master_gain = self._gain_from + (self._gain_to - self._gain_from) * prog
                if self._gain_fade_pos >= self._gain_fade_dur and self._gain_fade_on_complete:
                    on_complete = self._gain_fade_on_complete
                    self._gain_fade_on_complete = None
                    on_complete()
            else:
                self.master_gain = self._gain_to

            layers = []
            for poly in self.canvases.current.polygons:
                slot = self._slots.get(poly.id)
                if slot is None:
                    # No scene slot — either this polygon has no scene at
                    # all (media/webcam/text/knockout, the normal case for
                    # those source_types) or its assigned scene failed to
                    # load ("missing" below). Either way, still read glow/
                    # trail/mirror/brightness/contrast from its OWN
                    # overrides rather than hardcoding neutral — those apply
                    # regardless of source_type (see paintCanvasEditor/
                    # paintComposite in the two HTML files), and this branch
                    # is exactly the one media/webcam/text shapes always
                    # take, so hardcoding here silently dropped their
                    # brightness/contrast/mirror on the real output while
                    # the editor (which reads overrides straight off the
                    # polygon, not this broadcast layer) looked correct.
                    ov = poly.overrides
                    layers.append({
                        **self._poly_layer_base(poly),
                        "missing": bool(poly.scene), "is_3d": False,
                        "glow": float(ov.get("glow", 0.0)), "trail": float(ov.get("trail", 0.0)),
                        "mirror_x": bool(ov.get("mirror_x", False)), "mirror_y": bool(ov.get("mirror_y", False)),
                        "mirror_x_point": float(ov.get("mirror_x_point", 1.0)),
                        "mirror_y_point": float(ov.get("mirror_y_point", 1.0)),
                        "mirror_pre_distort": bool(ov.get("mirror_pre_distort", True)),
                        "brightness": float(ov.get("brightness", 1.0)), "contrast": float(ov.get("contrast", 1.0)),
                        "hue": float(ov.get("hue", 0.0)), "saturation": float(ov.get("saturation", 1.0)),
                        "colourize_amount": float(ov.get("colourize_amount", 0.0)),
                        "colourize_hue": float(ov.get("colourize_hue", 0.0)),
                        "aspect": "1:1",
                        "frame": [],
                    })
                    continue
                t = (now - slot.t0) + poly.time_offset
                slot.matrix.update(t, dt)
                ov = poly.overrides
                frame = slot.scene.render(t, dt, slot.matrix,
                                           disable_plane=bool(ov.get("disable_plane", False)))
                layers.append({
                    **self._poly_layer_base(poly),
                    "missing": False, "is_3d": slot.scene.is_3d,
                    "glow": float(ov.get("glow", 0.0)), "trail": float(ov.get("trail", 0.0)),
                    "mirror_x": bool(ov.get("mirror_x", False)),
                    "mirror_y": bool(ov.get("mirror_y", False)),
                    "mirror_x_point": float(ov.get("mirror_x_point", 1.0)),
                    "mirror_y_point": float(ov.get("mirror_y_point", 1.0)),
                    "mirror_pre_distort": bool(ov.get("mirror_pre_distort", True)),
                    "brightness": float(ov.get("brightness", 1.0)),
                    "contrast": float(ov.get("contrast", 1.0)),
                    "hue": float(ov.get("hue", 0.0)), "saturation": float(ov.get("saturation", 1.0)),
                    "colourize_amount": float(ov.get("colourize_amount", 0.0)),
                    "colourize_hue": float(ov.get("colourize_hue", 0.0)),
                    "aspect": slot.scene.spec.aspect,
                    "frame": frame,
                })
            self._apply_effectors(layers, now - self._effector_t0)
            self._last_layers = layers
            self.perf.tick()   # cheap interval/fps tracking — always on, see LoopStats.tick
            if self._diag_enabled:
                render_dur = time.monotonic() - t_render0
                total_dur = time.monotonic() - now
                n_points = sum(len(p.points) for l in layers for p in l["frame"])
                # No separate "output" stage any more (no DAC write) — the
                # per-slot render above already covers what this loop does.
                self.perf.record(render_s=render_dur, output_s=0.0, total_s=total_dur,
                                 n_points=n_points, crossfading=False)

            sleep = period - (time.monotonic() - now)
            if sleep > 0:
                time.sleep(sleep)

    # introspection for the UI -----------------------------------------
    def state(self) -> dict:
        return {
            "active": self.active,
            "frozen": self.frozen,
            "visuals_disabled": self.visuals_disabled,
            "master_gain": round(self.master_gain, 3),
            "perf_diag": self.perf.summary(),
            "diagnostics_enabled": self._diag_enabled,
            "project": self.current_project.name if self.current_project else None,
            "project_width": self.current_project.width if self.current_project else 1920,
            "project_height": self.current_project.height if self.current_project else 1080,
            "project_notes": self.current_project.notes if self.current_project else "",
            "outputs": [o.to_dict() for o in self.current_project.outputs] if self.current_project
                       else [OutputMonitorConfig().to_dict(), OutputMonitorConfig().to_dict()],
            "viewports_enabled": self.current_project.viewports_enabled if self.current_project else False,
            "show_fps": self.current_project.show_fps if self.current_project else False,
            # The live loop rate, not the saved one — they only differ in the
            # window between changing the field and hitting Save, which is
            # exactly when the UI should show what's actually running.
            "project_fps": self.fps,
            # Calibration-only, engine-runtime (see set_test_pattern above,
            # not persisted) — a plain bool per output index, defaulting
            # False for any index nothing's toggled yet.
            "test_pattern": [self._test_pattern.get(i, False) for i in range(
                len(self.current_project.outputs) if self.current_project else 2)],
            "project_library": self.projects.names(),
            # Effector source meters (see effectors.py) — the UI's own live
            # debugging affordance (PROMPT-effectors.md: "without it,
            # diagnosing a silent route is guesswork"). The sources/routes/
            # tempo themselves are already in state.canvas.effectors
            # (CanvasSpec.to_dict), this is just each source's CURRENT
            # sampled value, refreshed by the same tick that resolves routes.
            "effector_values": {k: round(v, 3) for k, v in self._last_effector_values.items()},
        }

    def composite_preview(self, max_points: int = 4000, stroke_thin: int = 200):
        """Per-polygon polylines for the browser, thinned to a flat
        per-polygon share of `max_points` so payload size stays roughly
        constant regardless of how many polygons are live — mirrors
        Engine.preview()'s thinning, just budgeted per layer instead of
        globally."""
        layers = self._last_layers
        budget = max(40, max_points // max(1, len(layers)))
        out = []
        for layer in layers:
            strokes = []
            sent = 0
            for p in layer["frame"]:
                pts = p.points
                if len(pts) > stroke_thin:
                    pts = pts[:: max(1, len(pts) // stroke_thin)]
                strokes.append({
                    "c": [round(float(v), 3) for v in p.color],
                    "p": [[round(float(x), 3), round(float(y), 3)] for x, y in pts],
                })
                sent += len(pts)
                if sent > budget:
                    break
            # Pass every layer field through as-is except the raw `frame`
            # (replaced by the thinned `strokes` above) — avoids hand-listing
            # the same field set a 4th time; see _poly_layer_base.
            out.append({**{k: v for k, v in layer.items() if k != "frame"}, "strokes": strokes})
        return out
