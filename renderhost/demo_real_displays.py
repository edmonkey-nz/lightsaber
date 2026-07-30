"""Same correction chain as demo_blend.py, but laid out from REAL detected
display geometry (ARCHITECTURE.md §9 build-order step 2) instead of an
invented single-window simulation — see displays.py.

Key difference from demo_blend.py, and why it's a separate script rather
than a flag on that one: real monitors/projectors occupy distinct,
non-overlapping regions of desktop coordinate space (X/xrandr has already
composed them side-by-side per §4 before this process starts) — only their
physical LIGHT overlaps, once actually aimed at a shared surface. So this
script draws each output normally (no additive blend_func across
viewports); demo_blend.py's overlapping-viewport + additive-blend trick was
specifically a single-laptop-screen SIMULATION of that physical summing,
which doesn't apply here. See correction_chain.py's
layout_from_real_displays() docstring for the full reasoning.

Run:
    python3 -m renderhost.demo_real_displays              # one window per detected geometry, windowed+borderless
    python3 -m renderhost.demo_real_displays --fullscreen  # venue-accurate: real fullscreen across detected displays
    python3 -m renderhost.demo_real_displays --screenshot /tmp/real.png

On a single-display machine (the common laptop-only dev case) this covers
exactly that one display — a legitimate topology-A minimum, not a fallback.
Plug in an external monitor as an EXTENDED (not mirrored) display and rerun
to see real multi-output layout; this script does not simulate a second
display the way demo_blend.py does.
"""

from __future__ import annotations

import argparse
import ctypes
import sys

import moderngl
import sdl2

# See demo_blend.py's identical guard for why: supports both
# `python3 -m renderhost.demo_real_displays` and running this file directly.
if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from renderhost import correction_chain as cc
    from renderhost import displays as displaymod
else:
    from . import correction_chain as cc
    from . import displays as displaymod


class RealDisplayDemo:
    def __init__(self, overlap: float, fullscreen: bool):
        self.overlap = overlap
        self.detected = displaymod.list_displays()
        print(f"[demo_real_displays] {len(self.detected)} display(s) detected:")
        for d in self.detected:
            print(f"    [{d.index}] {d.name!r}  bounds=({d.x},{d.y},{d.w},{d.h})  {d.refresh_hz}Hz")

        self.outputs, self.canvas_w, self.canvas_h, self.origin_x, self.origin_y = \
            cc.layout_from_real_displays(self.detected, overlap)

        self.blend_enabled = True
        self.lift_enabled = True
        self.blend_gamma = 2.2
        self.black_leak = 0.05
        self.lift = 0.05

        self._init_sdl_and_gl(fullscreen)
        self._init_gl_objects()
        self._print_state()

    def _init_sdl_and_gl(self, fullscreen: bool):
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {sdl2.SDL_GetError()}")
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MAJOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MINOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_PROFILE_MASK, sdl2.SDL_GL_CONTEXT_PROFILE_CORE)
        flags = sdl2.SDL_WINDOW_OPENGL | (sdl2.SDL_WINDOW_BORDERLESS if not fullscreen else 0)
        self.window = sdl2.SDL_CreateWindow(
            b"lightsaber render host", self.origin_x, self.origin_y,
            self.canvas_w, self.canvas_h, flags)
        if not self.window:
            raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
        if fullscreen:
            # SDL_WINDOW_FULLSCREEN_DESKTOP takes over whichever display the
            # window's current position already lands on, matching real
            # per-projector geometry rather than one arbitrary display's mode.
            sdl2.SDL_SetWindowFullscreen(self.window, sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP)
        self.glctx = sdl2.SDL_GL_CreateContext(self.window)
        if not self.glctx:
            raise RuntimeError(f"SDL_GL_CreateContext failed: {sdl2.SDL_GetError()}")
        self.ctx = moderngl.create_context()

    def _init_gl_objects(self):
        ctx = self.ctx
        quad = ctx.buffer(_quad_verts())
        self.scene_prog = ctx.program(
            vertex_shader=cc.FULLSCREEN_VERTEX_SHADER, fragment_shader=cc.SCENE_FRAGMENT_SHADER)
        self.scene_vao = ctx.vertex_array(self.scene_prog, [(quad, "2f", "in_pos")])
        self.output_prog = ctx.program(
            vertex_shader=cc.FULLSCREEN_VERTEX_SHADER, fragment_shader=cc.OUTPUT_FRAGMENT_SHADER)
        self.output_vao = ctx.vertex_array(self.output_prog, [(quad, "2f", "in_pos")])
        self.scene_tex = ctx.texture((self.canvas_w, self.canvas_h), 3)
        self.scene_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.scene_fbo = ctx.framebuffer(color_attachments=[self.scene_tex])

    def render_scene(self):
        self.scene_fbo.use()
        self.ctx.viewport = (0, 0, self.canvas_w, self.canvas_h)
        self.scene_prog["u_canvas_aspect"].value = self.canvas_w / self.canvas_h
        self.scene_vao.render(moderngl.TRIANGLE_STRIP)

    def render_outputs(self):
        self.ctx.screen.use()
        self.ctx.clear(0.0, 0.0, 0.0)
        self.scene_tex.use(location=0)
        prog = self.output_prog
        prog["u_scene"].value = 0
        prog["u_blend_enabled"].value = self.blend_enabled
        prog["u_blend_gamma"].value = self.blend_gamma
        prog["u_lift_enabled"].value = self.lift_enabled
        prog["u_black_leak"].value = self.black_leak
        prog["u_lift"].value = self.lift

        # No additive blend_func here — see module docstring. Each output
        # occupies its own, non-overlapping screen region; any actual
        # light-summing happens physically, on a real projected surface.
        self.ctx.disable(moderngl.BLEND)

        for out in self.outputs:
            x, y, w, h = out.window_rect
            self.ctx.viewport = (x, self.canvas_h - y - h, w, h)
            prog["u_crop"].value = out.crop_rect
            prog["u_homography"].write(_homography_to_mat3(out.homography_uniforms()))
            prog["u_left_overlap"].value = out.left_overlap
            prog["u_right_overlap"].value = out.right_overlap
            self.output_vao.render(moderngl.TRIANGLE_STRIP)

    def screenshot(self, path: str):
        data = self.ctx.screen.read(components=3)
        from PIL import Image
        img = Image.frombytes("RGB", (self.canvas_w, self.canvas_h), data)
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        img.save(path)
        print(f"[demo_real_displays] wrote {path}")

    def handle_key(self, sym: int) -> bool:
        if sym in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
            return False
        elif sym == sdl2.SDLK_b:
            self.blend_enabled = not self.blend_enabled
        elif sym == sdl2.SDLK_l:
            self.lift_enabled = not self.lift_enabled
        elif sym == sdl2.SDLK_g:
            self.blend_gamma = max(0.2, round(self.blend_gamma - 0.2, 2))
        elif sym == sdl2.SDLK_h:
            self.blend_gamma = round(self.blend_gamma + 0.2, 2)
        else:
            return True
        self._print_state()
        return True

    def _print_state(self):
        print(f"[demo_real_displays] outputs={len(self.outputs)}  blend={'on' if self.blend_enabled else 'OFF'}  "
              f"gamma={self.blend_gamma:.2f}  lift={'on' if self.lift_enabled else 'OFF'} ({self.lift:.3f})  "
              f"overlap={self.overlap:.2f}  canvas={self.canvas_w}x{self.canvas_h}")

    def close(self):
        sdl2.SDL_GL_DeleteContext(self.glctx)
        sdl2.SDL_DestroyWindow(self.window)
        sdl2.SDL_Quit()


def _quad_verts():
    import array
    return array.array("f", [-1, -1, 1, -1, -1, 1, 1, 1]).tobytes()


def _homography_to_mat3(H: dict) -> bytes:
    import array
    m = [H["a"], H["d"], H["g"], H["b"], H["e"], H["h"], H["c"], H["f"], 1.0]
    return array.array("f", m).tobytes()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--overlap", type=float, default=0.0,
                     help="FBO crop overlap into each neighbour, as a fraction of output width — "
                          "0 by default since real, non-overlapping displays have nothing to blend "
                          "unless you're deliberately aiming real projectors to overlap. Verified on "
                          "real 2-monitor hardware: >0 here on ordinary abutting monitors produces a "
                          "visible DARK band at the seam (the ramp fades each edge expecting a second "
                          "physical light source to fill it back in — two monitors have none). Only "
                          "raise this if the outputs are real overlapping projector throws.")
    ap.add_argument("--fullscreen", action="store_true", help="real fullscreen instead of a borderless dev window")
    ap.add_argument("--screenshot", help="write a PNG and exit instead of an interactive loop")
    args = ap.parse_args(argv)

    demo = RealDisplayDemo(args.overlap, args.fullscreen)
    try:
        if args.screenshot:
            demo.render_scene()
            demo.render_outputs()
            demo.screenshot(args.screenshot)
            return 0

        running = True
        event = sdl2.SDL_Event()
        while running:
            while sdl2.SDL_PollEvent(ctypes.byref(event)):
                if event.type == sdl2.SDL_QUIT:
                    running = False
                elif event.type == sdl2.SDL_KEYDOWN:
                    running = demo.handle_key(event.key.keysym.sym)
            demo.render_scene()
            demo.render_outputs()
            sdl2.SDL_GL_SwapWindow(demo.window)
            sdl2.SDL_Delay(16)
        return 0
    finally:
        demo.close()


if __name__ == "__main__":
    sys.exit(main())
