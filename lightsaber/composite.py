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
from .canvases import CanvasManager, CanvasSpec, PolygonSpec
from .sequencer import Sequencer
from .projects import ProjectManager, ProjectSpec
from .perf import LoopStats


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
        self.master_gain = 1.0
        self._gain_from = 1.0
        self._gain_to = 1.0
        self._gain_fade_dur = 0.0
        self._gain_fade_pos = 0.0

        self.perf = LoopStats(fps)
        self._diag_enabled = False

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
        self._enqueue(apply)

    def enable_visuals(self, fade: float = 2.0):
        def apply():
            self.visuals_disabled = False
            self._gain_from = self.master_gain
            self._gain_to = 1.0
            self._gain_fade_dur = max(0.0, float(fade))
            self._gain_fade_pos = 0.0
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

    def set_canvas_resolution(self, width: int, height: int):
        def apply():
            self.canvases.current.width = max(1, min(16384, int(width)))
            self.canvases.current.height = max(1, min(16384, int(height)))
        self._enqueue(apply)

    def load_canvas(self, name: str):
        def apply():
            self.canvases.current = self.canvases.load_spec(name)
        self._enqueue(apply)

    def save_canvas(self, name: str):
        self._enqueue(lambda: self.canvases.save(name))

    def delete_canvas(self, name: str):
        self._enqueue(lambda: self.canvases.delete(name))

    # polygon editing (queued) -------------------------------------------
    def add_polygon(self, scene: str | None = None):
        def apply():
            poly = PolygonSpec(scene=scene, label=scene or "")
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
                poly.source_type = value if value in ("scene", "media", "knockout") else "scene"
            elif key == "media":
                poly.media = value or None
            elif key == "label":
                poly.label = str(value or "")
            elif key == "opacity":
                poly.opacity = max(0.0, min(1.0, float(value)))
            elif key == "time_offset":
                poly.time_offset = float(value)
            elif key == "clip_shape":
                poly.clip_shape = value if value in ("circle", "hexagon", "triangle", "square") else None
            elif key == "z_index":
                poly.z_index = max(1, min(10, int(value)))
            elif key == "fit":
                poly.fit = value if value in ("stretch", "fit", "crop") else "stretch"
            elif key in ("glow", "trail"):
                poly.overrides[key] = max(0.0, float(value))
            elif key in ("mirror_x", "mirror_y", "disable_plane"):
                poly.overrides[key] = bool(value)
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

    # sequencer editing/transport (queued) --------------------------------
    def seq_add(self, canvas: str, duration: float = 8.0):
        self._enqueue(lambda: self.sequencer.add_step(canvas, duration))

    def seq_remove(self, step_id: str):
        self._enqueue(lambda: self.sequencer.remove_step(step_id))

    def seq_reorder(self, step_id: str, index: int):
        self._enqueue(lambda: self.sequencer.reorder_step(step_id, index))

    def seq_set(self, step_id: str, canvas: str | None = None, duration: float | None = None):
        self._enqueue(lambda: self.sequencer.set_step(step_id, canvas=canvas, duration=duration))

    def seq_set_loop(self, value: bool):
        self._enqueue(lambda: self.sequencer.set_loop(value))

    def seq_play(self):
        self._enqueue(self.sequencer.play)

    def seq_pause(self):
        self._enqueue(self.sequencer.pause)

    def seq_stop(self):
        self._enqueue(self.sequencer.stop)

    def seq_next(self):
        self._enqueue(self.sequencer.next)

    def seq_prev(self):
        self._enqueue(self.sequencer.prev)

    def seq_goto(self, index: int):
        self._enqueue(lambda: self.sequencer.goto(index))

    # slot management -------------------------------------------------------
    def _rebuild_if_needed(self, now: float):
        canvas = self.canvases.current
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
                self._last_layers = [{
                    "id": p.id, "corners": p.corners, "opacity": p.opacity,
                    "missing": False, "is_3d": False,
                    "glow": 0.0, "trail": 0.0, "mirror_x": False, "mirror_y": False,
                    "clip_shape": p.clip_shape, "source_type": p.source_type, "z_index": p.z_index, "media": p.media,
                    "fit": p.fit, "aspect": "1:1",
                    "frame": [],
                } for p in self.canvases.current.polygons]
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
            elif self.sequencer.tick(dt):
                step = self.sequencer.current_step()
                if step is not None and step.canvas:
                    try:
                        self.canvases.current = self.canvases.load_spec(step.canvas)
                    except Exception as e:
                        print(f"[lightsaber] sequencer: could not load canvas "
                              f"{step.canvas!r}: {e}")

            self._rebuild_if_needed(now)

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
            else:
                self.master_gain = self._gain_to

            layers = []
            for poly in self.canvases.current.polygons:
                slot = self._slots.get(poly.id)
                if slot is None:
                    layers.append({
                        "id": poly.id, "corners": poly.corners, "opacity": poly.opacity,
                        "missing": bool(poly.scene), "is_3d": False,
                        "glow": 0.0, "trail": 0.0, "mirror_x": False, "mirror_y": False,
                        "clip_shape": poly.clip_shape, "source_type": poly.source_type, "z_index": poly.z_index, "media": poly.media,
                        "fit": poly.fit, "aspect": "1:1",
                        "frame": [],
                    })
                    continue
                t = (now - slot.t0) + poly.time_offset
                slot.matrix.update(t, dt)
                ov = poly.overrides
                frame = slot.scene.render(t, dt, slot.matrix,
                                           disable_plane=bool(ov.get("disable_plane", False)))
                layers.append({
                    "id": poly.id, "corners": poly.corners, "opacity": poly.opacity,
                    "missing": False, "is_3d": slot.scene.is_3d,
                    "glow": float(ov.get("glow", 0.0)), "trail": float(ov.get("trail", 0.0)),
                    "mirror_x": bool(ov.get("mirror_x", False)),
                    "mirror_y": bool(ov.get("mirror_y", False)),
                    "clip_shape": poly.clip_shape, "source_type": poly.source_type, "z_index": poly.z_index, "media": poly.media,
                    "fit": poly.fit, "aspect": slot.scene.spec.aspect,
                    "frame": frame,
                })
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
            "project_library": self.projects.names(),
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
            out.append({
                "id": layer["id"], "corners": layer["corners"], "opacity": layer["opacity"],
                "missing": layer["missing"], "is_3d": layer["is_3d"],
                "glow": layer["glow"], "trail": layer["trail"],
                "mirror_x": layer["mirror_x"], "mirror_y": layer["mirror_y"],
                "clip_shape": layer["clip_shape"], "source_type": layer["source_type"], "z_index": layer["z_index"], "media": layer["media"],
                "fit": layer["fit"], "aspect": layer["aspect"],
                "strokes": strokes,
            })
        return out
