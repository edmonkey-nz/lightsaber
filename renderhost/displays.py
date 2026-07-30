"""Real display enumeration — ARCHITECTURE.md §9 build-order step 2 ("RandR
enumeration; drive viewport layout from real geometry") and the hard rule
in §4: "Do not assume 1920×1024 exactly... The app must read actual
geometry from RandR at startup and lay out from that."

Uses SDL2's display API rather than shelling out to `xrandr` or hand-rolling
Xlib/XRandR calls — under X11 this is backed by XRandR already (confirmed
against this machine's real internal panel: name `eDP-1 15"`, not a made-up
placeholder), and it's the same API this whole prototype already depends on
for windowing. Swap for real Xlib/XRandR calls only if SDL's info proves
insufficient (EDID model/serial, §6's "Detection and reconciliation", isn't
available through this API and would need that lower-level path).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import sdl2


@dataclass
class DisplayInfo:
    index: int
    name: str
    x: int
    y: int
    w: int
    h: int
    refresh_hz: int


def list_displays() -> list[DisplayInfo]:
    """SDL_Init(SDL_INIT_VIDEO) is safe to call even if already initialized
    (SDL ref-counts subsystems) — this does NOT call SDL_Quit, so it never
    tears down a video subsystem some other part of the process is using."""
    sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO)
    out = []
    for i in range(sdl2.SDL_GetNumVideoDisplays()):
        name = sdl2.SDL_GetDisplayName(i)
        rect = sdl2.SDL_Rect()
        sdl2.SDL_GetDisplayBounds(i, ctypes.byref(rect))
        mode = sdl2.SDL_DisplayMode()
        sdl2.SDL_GetCurrentDisplayMode(i, ctypes.byref(mode))
        out.append(DisplayInfo(
            index=i,
            name=name.decode() if name else f"display{i}",
            x=rect.x, y=rect.y, w=rect.w, h=rect.h,
            refresh_hz=mode.refresh_rate,
        ))
    return out


def _main():
    displays = list_displays()
    print(f"{len(displays)} display(s) detected:")
    for d in displays:
        print(f"  [{d.index}] {d.name!r}  bounds=({d.x},{d.y},{d.w},{d.h})  {d.refresh_hz}Hz")
    if len(displays) == 1:
        print("\nOnly one display — that's topology A's minimum case (laptop-only), "
              "not a detection failure. Plug in an external monitor/projector as an "
              "EXTENDED (not mirrored) display to see a second entry here.")


if __name__ == "__main__":
    _main()
