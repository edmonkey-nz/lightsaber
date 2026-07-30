"""The configuration format from ARCHITECTURE.md §6 — profiles (the rig:
outputs, groups, overlaps, corners, blend params) as actual load/save-able
JSON, matching the doc's own example shape as closely as possible, instead
of the CLI-args/Python-constants the two demo scripts use.

This is deliberately scoped to §9 build-order step 3 (profile load/save +
detection reconciliation) — NOT step 4 (alignment UI). Nothing here edits a
profile interactively; `--from-real` bootstraps one from currently-detected
displays as a starting point, `--validate` checks one against detected
hardware, and demo_profile.py renders one. Hand-editing the JSON (or a
future UI) is how corners/overlap/gamma actually get tuned.

Unit note: ARCHITECTURE.md §6's example encodes `corners` in the output's
own pixel space (`[[0,0],[1920,0],[1920,1024],[0,1024]]` for a 1920×1024
output) — NOT the `[0,1]` UV space correction_chain.homography() expects
(see that module's docstring for why UV space was chosen there). This
module converts on load/save so the on-disk format matches the doc exactly
while correction_chain stays unit-agnostic-but-self-consistent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

# See demo_blend.py's identical guard for why: supports both
# `python3 -m renderhost.profile` and running this file directly.
if __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from renderhost import correction_chain as cc
    from renderhost import displays as displaymod
else:
    from . import correction_chain as cc
    from . import displays as displaymod


@dataclass
class BlendConfig:
    overlap: int = 0     # pixels, interior seams only — matches §6's `"overlap": 288` example
    gamma: float = 2.2
    lift: float = 0.0

    @staticmethod
    def from_dict(d: dict) -> "BlendConfig":
        return BlendConfig(
            overlap=int(d.get("overlap", 0)),
            gamma=float(d.get("gamma", 2.2)),
            lift=float(d.get("lift", 0.0)),
        )


@dataclass
class OutputConfig:
    name: str
    viewport: tuple[int, int, int, int]           # x, y, w, h — real desktop/RandR coordinates
    corners: list[list[float]] = field(default_factory=list)   # pixel-space, sized to viewport w/h; [] = identity (no keystone)
    mesh: dict = field(default_factory=dict)       # opaque for now — mesh warp isn't implemented (see README)

    def to_dict(self) -> dict:
        return {"name": self.name, "viewport": list(self.viewport),
                "corners": self.corners, "mesh": self.mesh}

    @staticmethod
    def from_dict(d: dict) -> "OutputConfig":
        return OutputConfig(
            name=d["name"],
            viewport=tuple(d["viewport"]),
            corners=[list(c) for c in d.get("corners", [])],
            mesh=dict(d.get("mesh", {})),
        )

    def corners_uv(self) -> list[list[float]]:
        """Pixel-space `corners` -> the [0,1] UV space correction_chain.py's
        homography() expects. Empty `corners` (no keystone configured) ->
        identity."""
        if not self.corners:
            return [list(c) for c in cc.IDENTITY_CORNERS]
        w, h = self.viewport[2], self.viewport[3]
        return [[x / w, y / h] for x, y in self.corners]


@dataclass
class GroupConfig:
    id: str
    scene: str = ""     # which content this group shows — carried through, not yet consumed (no video/vector layer here; see README)
    blend: BlendConfig = field(default_factory=BlendConfig)
    outputs: list[OutputConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "scene": self.scene,
                "blend": asdict(self.blend), "outputs": [o.to_dict() for o in self.outputs]}

    @staticmethod
    def from_dict(d: dict) -> "GroupConfig":
        return GroupConfig(
            id=d["id"],
            scene=d.get("scene", ""),
            blend=BlendConfig.from_dict(d.get("blend", {})),
            outputs=[OutputConfig.from_dict(o) for o in d.get("outputs", [])],
        )


@dataclass
class ProfileSpec:
    canvas: dict = field(default_factory=dict)   # opaque passthrough — §6's example only ever sets {"height": ...}, not load-bearing here
    groups: list[GroupConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"canvas": self.canvas, "groups": [g.to_dict() for g in self.groups]}

    @staticmethod
    def from_dict(d: dict) -> "ProfileSpec":
        return ProfileSpec(
            canvas=dict(d.get("canvas", {})),
            groups=[GroupConfig.from_dict(g) for g in d.get("groups", [])],
        )


def load_profile(path: str) -> ProfileSpec:
    with open(path) as f:
        return ProfileSpec.from_dict(json.load(f))


def save_profile(path: str, spec: ProfileSpec):
    with open(path, "w") as f:
        json.dump(spec.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# Layout: turn one group's outputs into renderable OutputSpecs
# ---------------------------------------------------------------------------

def layout_group(group: GroupConfig):
    """Same halving-the-overlap-per-seam accounting as
    correction_chain.layout_from_real_displays() (see that function's
    comment for why: an earlier version padded each side by the FULL
    overlap, doubling every interior seam) — reimplemented here against
    OutputConfig.viewport instead of displays.DisplayInfo, and honouring
    each output's own configured `corners` instead of always assuming
    identity."""
    outputs = sorted(group.outputs, key=lambda o: o.viewport[0])
    if not outputs:
        return [], 0, 0, 0, 0
    origin_x = outputs[0].viewport[0]
    origin_y = min(o.viewport[1] for o in outputs)
    total_w = max(o.viewport[0] + o.viewport[2] for o in outputs) - origin_x
    total_h = max(o.viewport[1] + o.viewport[3] for o in outputs) - origin_y

    specs = []
    for i, o in enumerate(outputs):
        x, y, w, h = o.viewport
        half = group.blend.overlap // 2
        left_overlap = half if i > 0 else 0
        right_overlap = half if i < len(outputs) - 1 else 0
        crop_x = (x - origin_x - left_overlap) / total_w
        crop_w = (w + left_overlap + right_overlap) / total_w
        specs.append(cc.OutputSpec(
            name=o.name,
            window_rect=(x - origin_x, y - origin_y, w, h),
            crop_rect=(crop_x, 0.0, crop_w, 1.0),
            corners=o.corners_uv(),
            left_overlap=(left_overlap / w) if left_overlap else 0.0,
            right_overlap=(right_overlap / w) if right_overlap else 0.0,
        ))
    return specs, total_w, total_h, origin_x, origin_y


# ---------------------------------------------------------------------------
# Validation (§6 "Loader responsibilities" / "Detection and reconciliation")
# ---------------------------------------------------------------------------

def validate_profile(spec: ProfileSpec, detected) -> list[str]:
    """Returns a list of human-readable warnings — never raises, since a
    profile that doesn't perfectly match detected hardware is an expected,
    named situation (§6), not a crash. `detected` is displays.list_displays()'s
    result, or anything with the same `.name/.x/.y/.w/.h` shape."""
    warnings = []

    seen = {}
    for g in spec.groups:
        for o in g.outputs:
            if o.name in seen:
                warnings.append(f"output {o.name!r} appears in two groups: "
                                 f"{seen[o.name]!r} and {g.id!r}")
            seen[o.name] = g.id

    by_name = {d.name: d for d in detected}
    for g in spec.groups:
        for o in g.outputs:
            d = by_name.get(o.name)
            if d is None:
                warnings.append(f"output {o.name!r} (group {g.id!r}) not found in detected "
                                 f"hardware — {[d.name for d in detected]}")
                continue
            real = (d.x, d.y, d.w, d.h)
            if tuple(o.viewport) != real:
                warnings.append(f"output {o.name!r} viewport {tuple(o.viewport)} does not match "
                                 f"detected geometry {real} — hardware may have moved/changed mode")

    configured = seen.keys()
    for d in detected:
        if d.name not in configured:
            warnings.append(f"detected display {d.name!r} is not assigned to any group (unassigned)")

    return warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_from_real(args):
    detected = displaymod.list_displays()
    group = GroupConfig(
        id=args.group_id,
        blend=BlendConfig(overlap=args.overlap, gamma=args.gamma, lift=args.lift),
        outputs=[OutputConfig(name=d.name, viewport=(d.x, d.y, d.w, d.h)) for d in detected],
    )
    spec = ProfileSpec(canvas={"height": detected[0].h if detected else 1080}, groups=[group])
    save_profile(args.out, spec)
    print(f"wrote {args.out}: 1 group ({args.group_id!r}) with {len(detected)} output(s) "
          f"from currently-detected displays")


def _cmd_validate(args):
    spec = load_profile(args.path)
    detected = displaymod.list_displays()
    warnings = validate_profile(spec, detected)
    n_outputs = sum(len(g.outputs) for g in spec.groups)
    print(f"{args.path}: {len(spec.groups)} group(s), {n_outputs} output(s) configured; "
          f"{len(detected)} display(s) detected")
    if not warnings:
        print("OK — no mismatches")
    else:
        for w in warnings:
            print(f"  ! {w}")


def _cmd_show(args):
    spec = load_profile(args.path)
    for g in spec.groups:
        print(f"group {g.id!r}  scene={g.scene!r}  blend=overlap:{g.blend.overlap}px "
              f"gamma:{g.blend.gamma} lift:{g.blend.lift}")
        for o in g.outputs:
            keystone = "keystoned" if o.corners else "identity"
            print(f"    {o.name:12s} viewport={o.viewport}  corners={keystone}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("from-real", help="bootstrap a profile from currently-detected displays")
    p.add_argument("out")
    p.add_argument("--group-id", default="main")
    p.add_argument("--overlap", type=int, default=0, help="pixels")
    p.add_argument("--gamma", type=float, default=2.2)
    p.add_argument("--lift", type=float, default=0.0)
    p.set_defaults(func=_cmd_from_real)

    p = sub.add_parser("validate", help="check a profile against currently-detected displays")
    p.add_argument("path")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("show", help="print a profile's groups/outputs")
    p.add_argument("path")
    p.set_defaults(func=_cmd_show)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
