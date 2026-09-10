"""Canvases: a screen composed of scene-filled polygons for the monitor
outputs (browser windows — see composite.py). A project (see projects.py)
owns a directory of these; CanvasManager is retargeted to it on project
switch (see `retarget` below) rather than being reconstructed, since
CompositeRenderer holds a live reference to it.

A PolygonSpec is plain JSON-serialisable data: four corners (a quad, not an
arbitrary N-gon — a quad has a canonical unit-square warp, which is what the
corner-drag handles distort) plus which scene fills it and a sparse set of
per-polygon display/motion overrides (absent key = inherit the scene's own
settings). A CanvasSpec is an ordered list of polygons — list order is
z-order, index 0 drawn first/backmost. CanvasManager owns the on-disk
library and the canvas currently open in the editor, the same shape as
SceneManager in scenes.py.
"""

from __future__ import annotations

import json
import os

from .effectors import EffectorMatrix, ANCHOR_POINTS
import uuid
from dataclasses import dataclass, field, asdict

# clockwise from top-left — the default 16:9 rect a new polygon starts as
# (1a; widened from a square since 16:9 is the common video/scene frame
# shape — see SceneSpec.aspect). Same 1.0 total width span as the old
# square, height scaled to 16:9 (0.5625 = 9/16).
DEFAULT_CORNERS = [[-0.5, 0.28125], [0.5, 0.28125], [0.5, -0.28125], [-0.5, -0.28125]]

# The quad's own outline in UV space — what a freshly-switched-on custom
# mask starts as (see PolygonSpec.clip_points), so turning it on is a visual
# no-op you then carve away from rather than an instant blank-out.
DEFAULT_CLIP_POINTS = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
MAX_CLIP_POINTS = 64


def sanitize_clip_points(value) -> list:
    """Coerce a wire payload into a valid clip_points list, or [] (= no
    custom mask). Fewer than 3 points can't bound an area, so that degrades
    to 'no mask' rather than clipping everything away to nothing."""
    if not isinstance(value, (list, tuple)):
        return []
    pts = []
    for p in list(value)[:MAX_CLIP_POINTS]:
        try:
            u, v = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        # Clamped to the quad: content only exists inside the quad, so a
        # mask point beyond it can only ever be a no-op anyway.
        pts.append([max(0.0, min(1.0, u)), max(0.0, min(1.0, v))])
    return pts if len(pts) >= 3 else []


@dataclass
class PolygonSpec:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    label: str = ""
    corners: list = field(default_factory=lambda: [list(c) for c in DEFAULT_CORNERS])
    scene: str | None = None   # SceneManager library key — NOT spec.name (see
                                # engine.py's _current_library_name docstring:
                                # a handful of shipped scenes have an internal
                                # name that doesn't match their filename)
    # What this polygon shows: "scene" (the live generator system, via
    # `scene` above), "media" (an uploaded image/video, via `media` below —
    # warped into the quad client-side, same homography as a scene's strokes;
    # the compositor itself doesn't touch pixels, it just carries the
    # filename on the layer payload — see drawPolyMedia/paintComposite in the
    # two HTML files), "webcam" (a single still frame grabbed from a camera
    # device at load time, via `webcam_device`
    # below — same client-side warp pipeline as media, just sourced from
    # getUserMedia instead of an uploaded file), "text" (rendered from
    # `text_content`/`text_color`/`text_size` below onto an offscreen canvas,
    # then warped exactly like media/webcam), or "knockout" — a boolean
    # cutout: an arbitrary-point (not just a quad) opaque black shape with no
    # content of its own, purely there to blank out whatever it overlaps on
    # the monitor output. Stacking against other shapes is controlled by
    # z_index below.
    source_type: str = "scene"
    media: str | None = None   # filename under media_dir, meaningful iff source_type=="media"
    # Linked media (see media_roots.py) — "<root-name>::<relative/path.ext>",
    # referencing a file where it already lives instead of copying a
    # multi-gigabyte video into the app's own media/ folder. Takes precedence
    # over `media` above when both are set; the two are kept as separate
    # fields (rather than one overloaded string) so existing projects need no
    # migration and "which kind of source is this" is never a guess.
    media_link: str | None = None
    # Per-INSTANCE video playback (item 15). Two shapes can show the same
    # file with different trims and modes, so these live on the polygon, not
    # on the asset. in/out are None = "inherit this file's default trim"
    # (see media_meta.py) rather than 0/end, so re-trimming the asset moves
    # every instance that hasn't overridden it.
    #   mode: "loop" | "once" | "once_hold"
    #     once      — play the trimmed region, then show nothing
    #     once_hold — play it, then hold the last frame
    #   (no ping-pong: browsers have no reverse playback, and faking it with
    #    a backward seek per frame stutters badly on exactly the long files
    #    this feature exists for.)
    #   offset — start phase within the trimmed region, so two instances of
    #     one clip can run deliberately out of step (the video counterpart of
    #     time_offset above, which does the same job for generated scenes).
    media_in: float | None = None
    media_out: float | None = None
    media_mode: str = "loop"
    media_rate: float = 1.0
    media_offset: float = 0.0
    webcam_device: str | None = None   # MediaDeviceInfo.deviceId, meaningful iff source_type=="webcam";
                                        # None = browser's default camera
    text_content: str = ""     # meaningful iff source_type=="text"
    text_color: str = "#ffffff"
    text_size: float = 0.2     # font size as a fraction of the rendered text raster's height
    opacity: float = 1.0
    # seconds — without this, two polygons showing the same scene render
    # pixel-identical (2D generators are pure functions of t; see composite.py)
    time_offset: float = 0.0
    # sparse: "glow"/"trail"/"mirror_x"/"mirror_y"/"disable_plane"/"layer0"
    # (a dict of generator param overrides). Absent = inherit the scene's own
    # spec.camera settings / base params.
    overrides: dict = field(default_factory=dict)
    # Clip Shape: a secondary shape (independent of the quad corners above)
    # that the rendered content is hard-clipped to — pure client-side
    # compositing, see paintCanvasEditor/paintComposite in the two HTML
    # files. None/"" = no clip (draw the full quad). Used to be feathered
    # (soft-edged, via a CSS blur on an offscreen canvas) but that was
    # expensive enough to visibly stall the render loop with more than a
    # couple of shapes on screen, so it's hard-edged only now.
    clip_shape: str | None = None   # None | "circle" | "hexagon" | "triangle" | "square" | "custom"
    # Custom mask (8) — the point list for clip_shape=="custom", stored in
    # the quad's own UV space ([0,1]^2: u runs corners[0]->corners[1], v runs
    # corners[0]->corners[3]), NOT canvas space. That's the whole point of
    # the feature: mapping these through the same homography the content
    # itself is warped by makes the silhouette ride the corner pins, so
    # dragging a pin distorts the mask and the content together as one
    # object. (Same model as an After Effects mask, which lives in layer
    # space and is therefore warped by a Corner Pin effect applied after it.)
    # A preset clip_shape above is bbox-based by contrast and deliberately
    # stays that way — a circle clip stays a circle under keystone.
    # Empty = fall back to the quad outline, i.e. no visible masking.
    # If a canvas-space ("stencil the content slides behind") variant is
    # ever wanted too, it wants a separate clip_space field rather than a
    # different interpretation of these numbers.
    clip_points: list = field(default_factory=list)
    # Clip Shape size (6) — scales the clip shape about its own centre,
    # independent of the quad's own corners. 1.0 = exactly inscribed in the
    # quad's bounding box (the original, only-ever behaviour); <1 shrinks
    # the cutout, >1 grows it past the quad's own edges (still hard-clipped
    # by the quad/crop path wherever one applies).
    clip_scale: float = 1.0
    # Keep a PRESET clip shape's own proportions instead of stretching it to
    # the quad's bounding box (44). A preset is bbox-derived, so on a 2:1
    # shape "circle" is really an ellipse — fine when you want the mask to
    # follow the shape, wrong when you wanted a round porthole. On, the
    # smaller half-axis is used for both, so the shape is true.
    # Custom masks are unaffected: their points are already placed
    # individually and have no proportions to preserve.
    clip_uniform: bool = False
    # Feather (0-20 px, in PROJECT-CANVAS pixels, so a value means the same
    # thing whatever size the editor preview or the output window happens to
    # be). Softens the edge this shape presents: its mask/clip edge on a
    # content shape, its cutout edge on a knockout (either mode). 0 = the
    # hard edge that was the only behaviour before.
    #
    # The ramp is SYMMETRIC about the authored edge, the way feather works
    # in an image editor: content bleeds up to ~feather px outside the mask
    # at falling alpha, and fades to nothing that far inside it. Worth
    # knowing when a mask is aligned to a physical surface — a feathered
    # edge spills slightly past the line you drew.
    feather: float = 0.0
    # Chroma/luma key (raster sources only — media and webcam; a scene or
    # text shape already draws on transparent, so there is nothing to key).
    # "off" | "colour" (key a hue) | "luma" (key black, for footage shot or
    # generated on a black field).
    #
    # Applied AFTER the quad warp, over the shape's own side buffer, as one
    # SVG filter pass. Keying BEFORE the warp would match pristine source
    # pixels and give slightly cleaner edges, but it would have to push
    # per-pixel alpha through the 72-triangle mesh — whose 1.5px overlap
    # skirts composite twice, turning any soft matte edge into a visible
    # grid (the same artifact class as the old per-primitive opacity bug).
    key_mode: str = "off"
    key_color: str = "#00b140"     # standard chroma-green; ignored in luma mode
    # How much of the key hue counts as background. UP = key MORE: a pixel
    # drops out once its resemblance to the key colour passes (1 - tolerance).
    key_tolerance: float = 0.65
    key_softness: float = 0.15     # width of the ramp above that threshold
    # Spill suppression: pulls the key channel down toward the mean of the
    # other two, never up (a per-channel min — see the renderers' feBlend
    # "darken"), so a green fringe on a retained edge loses its cast without
    # tinting reds or neutrals.
    key_spill: float = 0.5
    # Softens the MATTE edge, in project-canvas px like `feather` above.
    # Distinct from `feather`, which softens the shape's mask/outline: this
    # one blurs the key's own alpha, so it smooths a ragged key rather than
    # the silhouette.
    key_feather: float = 0.0
    # FX — cheap post-warp treatments, applied over the shape's own side
    # buffer alongside the key. All of these are per-pixel and content
    # agnostic, so they work on any fill, not just raster ones.
    #
    # Order is fixed and deliberate: pixelate, then solarise -> posterise ->
    # invert (one shared tone curve), then duotone. Tone before duotone so
    # posterising bands the luminance the duotone then maps.
    #
    # Pixelate is a block size in project-canvas px (0 = off) and is NOT a
    # filter — it is a downscale-and-redraw with smoothing off, which
    # measured cheaper than any filter primitive.
    fx_pixelate: float = 0.0
    fx_posterize: int = 0          # 0 = off, else 2-16 levels per channel
    fx_solarize: float = 0.0       # 0-1, blends toward an inverted-highlights curve
    fx_invert: float = 0.0         # 0-1
    fx_duotone: float = 0.0        # 0-1 mix; maps luminance onto the two colours below
    fx_duotone_dark: str = "#0a1a2f"
    fx_duotone_light: str = "#ffd166"
    # Paint order (3): 1-10, 10 = furthest back, 1 = furthest front. Lets a
    # "knockout" cutout shape (see source_type above) sit in front of the
    # shapes it should blank out.
    z_index: int = 5
    # Cutout mode (knockout shapes only). False (the default, and the only
    # behaviour before this existed) = BLACK OUT: an opaque black fill is
    # painted at this shape's z-index, hiding everything behind it all the
    # way to the output. True = PUNCH THROUGH: nothing is painted at all;
    # instead this shape's outline is ERASED from the layers immediately
    # behind it, so whatever is further back shows through the hole. The
    # difference only shows once there's something to reveal — with nothing
    # behind it, a punch and a blackout both land on the same black canvas.
    knockout_punch: bool = False
    # How deep the punch reaches, in z-LEVELS (not shapes): a cutout at
    # z_index k erases layers whose z_index falls in [k+1, k+knockout_depth],
    # and leaves anything further back untouched to show through. z_index
    # only runs to 10, so a depth of 10 always reaches the back of the stack
    # ("punch all the way through", the default); smaller values are what
    # let a punch reveal one layer while stopping short of the one behind
    # it. Shapes at the SAME z as the cutout are never punched — order
    # within a z-level is arbitrary (array order), so punching there would
    # depend on which shape happened to be created first.
    knockout_depth: int = 10
    # How the assigned scene's own frame shape (SceneSpec.aspect) is fitted
    # into this polygon's quad, which can be any aspect ratio once dragged —
    # without this, content just stretches to fill the quad. "stretch" (the
    # old, only behaviour) fills exactly, distorting; "fit" letterboxes,
    # preserving the scene's aspect; "crop" fills the quad, cropping
    # overflow (needs a clip to the quad's own screen path — see
    # paintCanvasEditor/paintComposite). Applies equally to a future
    # media-sourced polygon's own native aspect.
    fit: str = "stretch"   # "stretch" | "fit" | "crop"
    # Edit lock — a pure EDITOR guard, not a render property: a locked shape
    # renders exactly as before, it just can't be selected, dragged, corner-
    # pinned, mask-edited or body-moved on the canvas, so a finished piece of
    # mapping can't be nudged out of alignment by a stray click mid-set. Its
    # settings stay fully editable from Shape settings (that's how you unlock
    # it again), and effectors still animate it — locking is about the mouse,
    # not about freezing the shape.
    locked: bool = False
    # Where position/scale/rotation effector routes pivot from (see
    # effectors.py's transform_corners/ANCHOR_FRACTIONS) — one of the 3x3
    # grid positions, a fraction of this polygon's own bounding box.
    # Irrelevant unless a route actually targets one of those params.
    transform_anchor: str = "center"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PolygonSpec":
        return PolygonSpec(
            id=d.get("id") or uuid.uuid4().hex[:8],
            label=d.get("label", ""),
            corners=[list(c) for c in d.get("corners", DEFAULT_CORNERS)],
            scene=d.get("scene"),
            source_type=d.get("source_type", "scene"),
            media=d.get("media"),
            media_link=d.get("media_link"),
            media_in=(float(d["media_in"]) if d.get("media_in") is not None else None),
            media_out=(float(d["media_out"]) if d.get("media_out") is not None else None),
            media_mode=(d.get("media_mode") if d.get("media_mode") in
                        ("loop", "once", "once_hold") else "loop"),
            media_rate=max(0.05, min(8.0, float(d.get("media_rate", 1.0)))),
            media_offset=float(d.get("media_offset", 0.0)),
            webcam_device=d.get("webcam_device"),
            text_content=d.get("text_content", ""),
            text_color=d.get("text_color", "#ffffff"),
            text_size=float(d.get("text_size", 0.2)),
            opacity=float(d.get("opacity", 1.0)),
            time_offset=float(d.get("time_offset", 0.0)),
            overrides=dict(d.get("overrides", {})),
            clip_shape=d.get("clip_shape"),
            clip_points=sanitize_clip_points(d.get("clip_points")),
            clip_scale=float(d.get("clip_scale", 1.0)),
            clip_uniform=bool(d.get("clip_uniform", False)),
            feather=max(0.0, min(20.0, float(d.get("feather", 0.0)))),
            key_mode=(d.get("key_mode") if d.get("key_mode") in ("off", "colour", "luma") else "off"),
            key_color=d.get("key_color", "#00b140"),
            key_tolerance=max(0.0, min(1.0, float(d.get("key_tolerance", 0.65)))),
            key_softness=max(0.0, min(1.0, float(d.get("key_softness", 0.15)))),
            key_spill=max(0.0, min(1.0, float(d.get("key_spill", 0.5)))),
            key_feather=max(0.0, min(20.0, float(d.get("key_feather", 0.0)))),
            fx_pixelate=max(0.0, min(64.0, float(d.get("fx_pixelate", 0.0)))),
            fx_posterize=(0 if not d.get("fx_posterize") else max(2, min(16, int(d["fx_posterize"])))),
            fx_solarize=max(0.0, min(1.0, float(d.get("fx_solarize", 0.0)))),
            fx_invert=max(0.0, min(1.0, float(d.get("fx_invert", 0.0)))),
            fx_duotone=max(0.0, min(1.0, float(d.get("fx_duotone", 0.0)))),
            fx_duotone_dark=d.get("fx_duotone_dark", "#0a1a2f"),
            fx_duotone_light=d.get("fx_duotone_light", "#ffd166"),
            locked=bool(d.get("locked", False)),
            z_index=max(1, min(10, int(d.get("z_index", 5)))),
            knockout_punch=bool(d.get("knockout_punch", False)),
            knockout_depth=max(1, min(10, int(d.get("knockout_depth", 10)))),
            fit=d.get("fit", "stretch"),
            transform_anchor=d.get("transform_anchor")
                if d.get("transform_anchor") in ANCHOR_POINTS else "center",
        )


@dataclass
class CanvasSpec:
    name: str = "untitled"
    polygons: list = field(default_factory=list)   # list[PolygonSpec]
    # The effector/modulation matrix (see effectors.py) — creative config,
    # lives with the canvas the same way its polygons do (PROMPT-effectors.md:
    # "Sources and routes are creative config — they save into the show
    # file, not the profile"). Routes target a specific polygon on THIS
    # canvas by id, so it travels with the canvas, not the project.
    effectors: EffectorMatrix = field(default_factory=EffectorMatrix)

    def to_dict(self) -> dict:
        return {"name": self.name, "polygons": [p.to_dict() for p in self.polygons],
                "effectors": self.effectors.to_dict()}

    @staticmethod
    def from_dict(d: dict) -> "CanvasSpec":
        # Ignores a "width"/"height" key if present — pre-migration canvas
        # files on disk may still carry their old per-canvas resolution;
        # that's now ProjectSpec's (see projects.py), harmless leftover data.
        return CanvasSpec(
            name=d.get("name", "untitled"),
            polygons=[PolygonSpec.from_dict(p) for p in d.get("polygons", [])],
            effectors=EffectorMatrix.from_dict(d.get("effectors", {})),
        )


class CanvasManager:
    """On-disk canvas library, same persistence shape as scenes.SceneManager
    (cached names(), atomic tmp+replace save, sanitised filenames) — plus
    `current`, the canvas presently open in the editor. `current` is what the
    compositor renders; loading a different canvas (or a sequencer step
    advancing) replaces it wholesale rather than mutating in place."""

    def __init__(self, library_dir: str):
        self.library_dir = library_dir
        os.makedirs(library_dir, exist_ok=True)
        self.current: CanvasSpec = CanvasSpec(name="untitled", polygons=[PolygonSpec()])
        self._names_cache: list[str] | None = None

    def retarget(self, library_dir: str):
        """Re-point this manager at a different project's canvas directory
        in place, rather than constructing a new CanvasManager — composite.py
        holds a live reference to this instance. Always resets `current` to
        a fresh untitled canvas: a canvas open from the OLD project must not
        stay open under the new one, since saving it would write into the
        new project's namespace under the old project's name."""
        self.library_dir = library_dir
        os.makedirs(library_dir, exist_ok=True)
        self._names_cache = None
        self.current = CanvasSpec(name="untitled", polygons=[PolygonSpec()])

    def names(self) -> list[str]:
        # same rationale as SceneManager.names(): cached so the ~20Hz state
        # broadcast doesn't do a disk listdir every tick.
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

    def thumb_path_for(self, name: str) -> str:
        # Sibling file, same sanitised base name as the canvas's own .json —
        # see save_thumbnail's comment for why this is a file on disk rather
        # than a field on CanvasSpec (avoids bloating every canvas load/save
        # with an unrelated image blob nothing else reads).
        safe = "".join(c for c in name if c.isalnum() or c in " _-").strip()
        return os.path.join(self.library_dir, f"{safe or 'untitled'}.thumb.jpg")

    def save(self, name: str, spec: CanvasSpec | None = None):
        spec = spec or self.current
        spec.name = name
        tmp = self.path_for(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(spec.to_dict(), f, indent=2)
        os.replace(tmp, self.path_for(name))
        self._names_cache = None

    def save_thumbnail(self, name: str, jpeg_bytes: bytes):
        # A low-res preview of this canvas's editor view, captured
        # client-side (cv-edit's own rendered pixels — see index.html's
        # canvas-save handler) and pushed up as a separate small message
        # right after canvas_save. Kept as its own file rather than a
        # CanvasSpec field: it's a presentation artifact for the sequencer
        # list (see server.py's /canvas-thumb/ route), not canvas content —
        # bloating every save/load with an embedded image nobody but that
        # one <img> tag reads would be wasteful, and canvases are hand-
        # editable JSON that shouldn't need a giant base64 blob in them.
        tmp = self.thumb_path_for(name) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(jpeg_bytes)
        os.replace(tmp, self.thumb_path_for(name))

    def load_spec(self, name: str) -> CanvasSpec:
        """Read-only fetch — does NOT touch `current`. Used both by the
        editor (which then assigns the result to `current`) and by the
        sequencer (which renders steps without disturbing whatever's open
        for editing)."""
        with open(self.path_for(name)) as f:
            return CanvasSpec.from_dict(json.load(f))

    def delete(self, name: str):
        p = self.path_for(name)
        if os.path.exists(p):
            os.remove(p)
        tp = self.thumb_path_for(name)
        if os.path.exists(tp):
            os.remove(tp)
        self._names_cache = None
