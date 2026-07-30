"""The per-output correction chain from ARCHITECTURE.md §5: crop, corner-pin
homography, edge blend (gamma-corrected ramp + black-level lift). Mesh warp
(step 3 in the doc) is NOT implemented yet — see README.md for why it's
deferred and what's here instead.

Pure math + GLSL source, no GL calls of its own — demo_blend.py (or a future
real render host) owns the context, FBO and draw calls. Kept dependency-free
on purpose so this same module can eventually be reused by a KMSDRM/headless
render path with no windowing library in the import chain at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def homography(corners: list[list[float]]) -> dict[str, float]:
    """Unit-square -> quad projective mapping — the same Heckbert
    construction index.html/output.html's own `homography()` uses (see
    those files' comments for the derivation); the construction itself is
    coordinate-space-agnostic; it treats (u,v) as a [0,1] parametrization of
    the unit square by definition.

    `corners` = [[x,y], ...] four points, clockwise from top-left, in [0,1]
    UV space (NOT the browser app's [-1,1] generator space — this module
    feeds (u,v) straight into the matrix with no extra conversion, so its
    own corners have to already be in the space the matrix expects).
    Identity corners ([[0,0],[1,0],[1,1],[0,1]]) give the identity
    homography (no keystone) — every OutputSpec defaults to this.
    """
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = corners
    dx1, dy1 = x1 - x2, y1 - y2
    dx2, dy2 = x3 - x2, y3 - y2
    sx, sy = x0 - x1 + x2 - x3, y0 - y1 + y2 - y3
    den = dx1 * dy2 - dx2 * dy1
    g = (sx * dy2 - dx2 * sy) / den if den else 0.0
    h = (dx1 * sy - sx * dy1) / den if den else 0.0
    return {
        "a": x1 - x0 + g * x1, "b": x3 - x0 + h * x3, "c": x0,
        "d": y1 - y0 + g * y1, "e": y3 - y0 + h * y3, "f": y0,
        "g": g, "h": h,
    }


IDENTITY_CORNERS = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


@dataclass
class OutputSpec:
    """One virtual projector. Mirrors ARCHITECTURE.md §6's per-output config
    shape (name/viewport/corners) closely enough to load straight from that
    JSON later — this prototype just constructs them in Python instead of
    reading a profile file, since there's no real hardware/RandR to detect
    yet (see README.md)."""
    name: str
    window_rect: tuple[int, int, int, int]     # (x, y, w, h) in the demo window — stands in for a real display's viewport
    crop_rect: tuple[float, float, float, float]  # (x, y, w, h), normalized [0,1] within the shared FBO scene texture
    corners: list[list[float]] = field(default_factory=lambda: [list(c) for c in IDENTITY_CORNERS])
    left_overlap: float = 0.0     # fraction of this output's own width, 0..1
    right_overlap: float = 0.0

    def homography_uniforms(self) -> dict[str, float]:
        return homography(self.corners)


def compute_overlap_layout(n_outputs: int, output_w: int, output_h: int,
                            overlap_frac: float) -> list[OutputSpec]:
    """Lays out `n_outputs` side-by-side outputs with a uniform interior
    overlap, and derives their FBO crop rects from it — the same
    `group_width = Σ output_widths − Σ interior_overlaps` accounting
    ARCHITECTURE.md §5 specifies, just computed here instead of a config
    loader. Overlapping crops are "the source side of edge blending for
    free" (§5.1) — adjacent outputs literally read the same FBO columns in
    their shared band, so blending them is just a matter of what each draws
    with what alpha, not sourcing different data.
    """
    stride = int(output_w * (1 - overlap_frac))
    overlap_px = output_w - stride
    canvas_w = stride * (n_outputs - 1) + output_w if n_outputs > 1 else output_w

    outputs = []
    for i in range(n_outputs):
        x = i * stride
        outputs.append(OutputSpec(
            name=f"output{i}",
            window_rect=(x, 0, output_w, output_h),
            crop_rect=(x / canvas_w, 0.0, output_w / canvas_w, 1.0),
            left_overlap=(overlap_px / output_w) if i > 0 else 0.0,
            right_overlap=(overlap_px / output_w) if i < n_outputs - 1 else 0.0,
        ))
    return outputs, canvas_w


def layout_from_real_displays(displays, overlap_frac: float = 0.0):
    """Builds OutputSpecs from REAL detected display geometry (see
    displays.py) instead of inventing positions the way
    compute_overlap_layout does for the single-window simulation.

    `displays` — any objects with `.name/.x/.y/.w/.h` (displays.DisplayInfo
    fits; this module doesn't import it, to stay GL/SDL-dependency-free).

    Window rects are the real, non-overlapping desktop positions —
    ARCHITECTURE.md §4 has X/xrandr already compose real projector outputs
    side-by-side into one virtual screen before this process ever starts,
    so from here they're just adjacent rectangles, never overlapping ones.
    Only the FBO CROP each output samples is widened into its neighbours,
    to give edge blend something to blend (§5.1: "overlapping crops give
    the source side of edge blending for free") — this is the opposite of
    compute_overlap_layout's demo trick (simulating physical light-summing
    via overlapping window viewports + additive blending), because real
    monitors/projectors don't overlap in desktop coordinate space; only
    their physical light output does, once actually aimed at a shared
    surface. Callers should draw normally (no additive blend_func) in this
    mode — see demo_real_displays.py.
    """
    ordered = sorted(displays, key=lambda d: d.x)
    origin_x = ordered[0].x
    origin_y = min(d.y for d in ordered)
    total_w = max(d.x + d.w for d in ordered) - origin_x
    total_h = max(d.y + d.h for d in ordered) - origin_y

    outputs = []
    for i, d in enumerate(ordered):
        # Split the configured overlap in half between the two neighbours at
        # each seam — each side pads by half, so the two contributions sum
        # to exactly `overlap_frac`, not double it. (An earlier version had
        # each side pad by the FULL amount, so every interior seam ended up
        # twice as wide as configured — caught by testing the multi-display
        # math directly with synthetic displays, since this machine only
        # has one real display to verify against.)
        half = int(d.w * overlap_frac) // 2
        left_overlap = half if i > 0 else 0
        right_overlap = half if i < len(ordered) - 1 else 0
        crop_x = (d.x - origin_x - left_overlap) / total_w
        crop_w = (d.w + left_overlap + right_overlap) / total_w
        outputs.append(OutputSpec(
            name=d.name,
            window_rect=(d.x - origin_x, d.y - origin_y, d.w, d.h),
            crop_rect=(crop_x, 0.0, crop_w, 1.0),
            left_overlap=(left_overlap / d.w) if left_overlap else 0.0,
            right_overlap=(right_overlap / d.w) if right_overlap else 0.0,
        ))
    return outputs, total_w, total_h, origin_x, origin_y


# ---------------------------------------------------------------------------
# Shaders
# ---------------------------------------------------------------------------

# Renders the shared "scene" into the FBO — stands in for the real vector +
# video content layers (ARCHITECTURE.md §5's "Content layers"). A test
# pattern is more useful than real content here: a split field (bright bars
# + fine grid on top, a FLAT near-black field on the bottom, deliberately
# uniform rather than a PLUGE step ramp) exercises both halves of the
# correction chain at once — the grid shows blend-ramp/keystone geometry,
# the flat near-black band shows black-level lift with nothing else varying
# to confuse it, since that failure is invisible against bright content.
SCENE_FRAGMENT_SHADER = """
#version 330
in vec2 v_uv;
out vec4 f_color;
uniform float u_canvas_aspect;   // canvas_w / canvas_h, for square-ish grid cells

vec3 bars(float u) {
    float n = 7.0;
    float i = floor(u * n);
    vec3 cols[7] = vec3[7](
        vec3(0.75,0.75,0.75), vec3(0.75,0.75,0.0), vec3(0.0,0.75,0.75),
        vec3(0.0,0.6,0.0), vec3(0.75,0.0,0.75), vec3(0.6,0.0,0.0), vec3(0.0,0.0,0.6)
    );
    return cols[int(clamp(i, 0.0, 6.0))];
}

void main() {
    if (v_uv.y > 0.28) {
        vec3 col = bars(v_uv.x);
        vec2 grid = fract(vec2(v_uv.x * u_canvas_aspect, v_uv.y) * 24.0);
        float line = step(grid.x, 0.06) + step(grid.y, 0.06);
        col = mix(col, vec3(1.0), clamp(line, 0.0, 1.0) * 0.5);
        f_color = vec4(col, 1.0);
    } else {
        // Flat near-black field — deliberately uniform (not a PLUGE step
        // ramp) so ANY brightness variation visible in this band is the
        // leak/lift effect itself, not confused with test-pattern content.
        f_color = vec4(vec3(0.03), 1.0);
    }
}
"""

FULLSCREEN_VERTEX_SHADER = """
#version 330
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos * 0.5 + 0.5;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

# One output's correction chain: homography (corner-pin) -> crop -> sample
# scene FBO -> edge-blend alpha (gamma-corrected ramp, toggleable) ->
# black-level leak + lift compensation (both toggleable, so the failure mode
# and the fix are both directly visible — see demo_blend.py's key bindings).
OUTPUT_FRAGMENT_SHADER = """
#version 330
in vec2 v_uv;              // this output's own local space, 0..1
out vec4 f_color;

uniform sampler2D u_scene;
uniform vec4 u_crop;       // x, y, w, h — normalized rect within u_scene
uniform mat3 u_homography; // columns a,d,g / b,e,h / c,f,1 — see apply_homography below
uniform float u_left_overlap;
uniform float u_right_overlap;
uniform bool u_blend_enabled;
uniform float u_blend_gamma;
uniform bool u_lift_enabled;
uniform float u_black_leak;
uniform float u_lift;

// A projective (not just affine) map of this output's own [0,1] UV space
// through its corner-pin homography — so a real keystone is possible, not
// only pan/scale. With identity corners this is a no-op (see
// correction_chain.py's homography() docstring for why identity is [0,1],
// not [-1,1] — a mismatch there previously caused UVs to leave [0,1] and
// wrap via the texture's GL_REPEAT sampling, showing up as a duplicated,
// tiled scene instead of the correct crop).
vec2 apply_homography(mat3 H, vec2 uv) {
    vec3 p = H * vec3(uv, 1.0);
    return p.xy / p.z;
}

float blend_alpha(float u) {
    if (!u_blend_enabled) return 1.0;   // naive overlap: both outputs at full opacity — the double-bright band this whole chain exists to fix
    float a = 1.0;
    if (u_left_overlap > 0.0 && u < u_left_overlap) {
        a = u / u_left_overlap;
    } else if (u_right_overlap > 0.0 && u > 1.0 - u_right_overlap) {
        a = (1.0 - u) / u_right_overlap;
    }
    // Gamma-corrected ramp (ARCHITECTURE.md §5.4) — a naive linear alpha
    // crossfade dips at the seam once passed through display gamma; raising
    // it to 1/gamma compensates. Tunable rather than a fixed derivation,
    // same as the doc's own per-group "gamma" config value — real rigs tune
    // this by eye against the actual projectors' response curve.
    return pow(clamp(a, 0.0, 1.0), 1.0 / u_blend_gamma);
}

void main() {
    vec2 uv = apply_homography(u_homography, v_uv);
    vec2 scene_uv = u_crop.xy + uv * u_crop.zw;
    vec3 content = texture(u_scene, scene_uv).rgb;

    float alpha = blend_alpha(v_uv.x);

    // Black-level lift (§5.4) — every real projector leaks some light even
    // at "black" (u_black_leak), so two overlapping projectors' leaks add,
    // producing a brighter floor in the overlap than everywhere else. You
    // can't subtract light from a projector, so the fix is the opposite:
    // raise the SINGLE-coverage floor to match, via u_lift, so the floor is
    // uniform across the whole canvas instead of banded at the seams.
    bool in_overlap = (u_left_overlap > 0.0 && v_uv.x < u_left_overlap) ||
                       (u_right_overlap > 0.0 && v_uv.x > 1.0 - u_right_overlap);
    float leak = u_black_leak;
    if (u_lift_enabled && !in_overlap) {
        leak += u_lift;
    }

    f_color = vec4(content * alpha + vec3(leak), 1.0);
}
"""
