"""Demo: N simulated adjacent projectors in one laptop window, running the
full per-output correction chain from ARCHITECTURE.md §5 (crop, corner-pin
homography, gamma-corrected edge blend, black-level lift) so you can SEE
edge blending working before any real projector or render host exists.

This is topology-A-shaped (one window, laptop only, per ARCHITECTURE.md
§1's Deployment topologies) but does not itself implement topology A or B —
see README.md in this directory for exactly what's real here vs deferred.

Run:
    python3 -m renderhost.demo_blend
    python3 -m renderhost.demo_blend --outputs 4 --overlap 0.2
    python3 -m renderhost.demo_blend --screenshot /tmp/blend.png --screenshot-frame 60

Keys (see README.md for the full list):
    B  toggle edge blend        L  toggle black-level lift
    G/g  blend gamma +/-        [ / ]  overlap width -/+
    R  reset to defaults        Esc/Q  quit
"""

from __future__ import annotations

import argparse
import ctypes
import sys

import moderngl
import sdl2

# Supports both `python3 -m renderhost.demo_blend` (the documented way —
# __package__ is "renderhost", relative import works) and running this file
# directly, e.g. `cd renderhost && python3 demo_blend.py` (__package__ is
# "", relative imports fail with "attempted relative import with no known
# parent package" — a real error someone hit running exactly that).
if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from renderhost import correction_chain as cc
else:
    from . import correction_chain as cc


class BlendDemo:
    def __init__(self, n_outputs: int, output_w: int, output_h: int, overlap: float):
        self.n_outputs = n_outputs
        self.output_w = output_w
        self.output_h = output_h
        self.overlap = overlap
        self.outputs, self.canvas_w = cc.compute_overlap_layout(n_outputs, output_w, output_h, overlap)
        self.window_w = max(o.window_rect[0] + o.window_rect[2] for o in self.outputs)
        self.window_h = output_h

        # Live-tunable correction-chain state (§5.4) — these are exactly the
        # per-group `blend` config values in ARCHITECTURE.md §6
        # (`{"overlap": ..., "gamma": ..., "lift": ...}`), just adjusted with
        # keys here instead of hand-edited JSON, since the point of this demo
        # is to feel the effect of each one directly.
        self.blend_enabled = True
        self.lift_enabled = True
        self.blend_gamma = 2.2
        self.black_leak = 0.05
        self.lift = 0.05

        self._init_sdl_and_gl()
        self._init_gl_objects()
        self._print_help()
        self._print_state()

    # -- setup -----------------------------------------------------------
    def _init_sdl_and_gl(self):
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {sdl2.SDL_GetError()}")
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MAJOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MINOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_PROFILE_MASK, sdl2.SDL_GL_CONTEXT_PROFILE_CORE)
        title = f"lightsaber render host — edge blend demo ({self.n_outputs} virtual outputs)".encode()
        self.window = sdl2.SDL_CreateWindow(
            title, sdl2.SDL_WINDOWPOS_CENTERED, sdl2.SDL_WINDOWPOS_CENTERED,
            self.window_w, self.window_h, sdl2.SDL_WINDOW_OPENGL)
        if not self.window:
            raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
        self.glctx = sdl2.SDL_GL_CreateContext(self.window)
        if not self.glctx:
            raise RuntimeError(f"SDL_GL_CreateContext failed: {sdl2.SDL_GetError()}")
        self.ctx = moderngl.create_context()

    def _init_gl_objects(self):
        ctx = self.ctx
        quad = ctx.buffer(_quad_verts())
        vao_fmt = "2f"

        self.scene_prog = ctx.program(
            vertex_shader=cc.FULLSCREEN_VERTEX_SHADER, fragment_shader=cc.SCENE_FRAGMENT_SHADER)
        self.scene_vao = ctx.vertex_array(self.scene_prog, [(quad, vao_fmt, "in_pos")])

        self.output_prog = ctx.program(
            vertex_shader=cc.FULLSCREEN_VERTEX_SHADER, fragment_shader=cc.OUTPUT_FRAGMENT_SHADER)
        self.output_vao = ctx.vertex_array(self.output_prog, [(quad, vao_fmt, "in_pos")])

        # The FBO (§5: "the FBO is mandatory, not an optimisation") — the
        # scene renders once here, every output then SAMPLES it through its
        # own correction chain, instead of each output re-rendering content.
        self.scene_tex = ctx.texture((self.canvas_w, self.output_h), 3)
        self.scene_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.scene_fbo = ctx.framebuffer(color_attachments=[self.scene_tex])

    # -- per-frame ---------------------------------------------------------
    def render_scene(self):
        self.scene_fbo.use()
        self.ctx.viewport = (0, 0, self.canvas_w, self.output_h)
        self.scene_prog["u_canvas_aspect"].value = self.canvas_w / self.output_h
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

        # Additive: two outputs drawing into the same window pixels in an
        # overlap band is exactly how two real projectors' light sums on a
        # shared patch of screen — see correction_chain.py's OUTPUT_FRAGMENT_SHADER.
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.ONE, moderngl.ONE

        for out in self.outputs:
            x, y, w, h = out.window_rect
            # GL viewport origin is bottom-left; window_rect is top-left-based
            # (matches ARCHITECTURE.md §6's own viewport convention).
            self.ctx.viewport = (x, self.window_h - y - h, w, h)
            prog["u_crop"].value = out.crop_rect
            H = out.homography_uniforms()
            prog["u_homography"].write(_homography_to_mat3(H))
            prog["u_left_overlap"].value = out.left_overlap
            prog["u_right_overlap"].value = out.right_overlap
            self.output_vao.render(moderngl.TRIANGLE_STRIP)

        self.ctx.disable(moderngl.BLEND)

    def screenshot(self, path: str):
        data = self.ctx.screen.read(components=3)
        from PIL import Image
        img = Image.frombytes("RGB", (self.window_w, self.window_h), data)
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        img.save(path)
        print(f"[demo_blend] wrote {path}")

    # -- control -----------------------------------------------------------
    def rebuild_layout(self):
        self.outputs, self.canvas_w = cc.compute_overlap_layout(
            self.n_outputs, self.output_w, self.output_h, self.overlap)
        self.scene_tex.release()
        self.scene_fbo.release()
        self.scene_tex = self.ctx.texture((self.canvas_w, self.output_h), 3)
        self.scene_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.scene_fbo = self.ctx.framebuffer(color_attachments=[self.scene_tex])

    def handle_key(self, sym: int) -> bool:
        """Returns False to quit."""
        if sym in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
            return False
        elif sym == sdl2.SDLK_b:
            self.blend_enabled = not self.blend_enabled
        elif sym == sdl2.SDLK_l:
            self.lift_enabled = not self.lift_enabled
        elif sym == sdl2.SDLK_g:
            self.blend_gamma = max(0.2, round(self.blend_gamma - 0.2, 2))
        elif sym == sdl2.SDLK_h:   # shift-G is awkward via SDL keysyms; h = gamma+
            self.blend_gamma = round(self.blend_gamma + 0.2, 2)
        elif sym == sdl2.SDLK_LEFTBRACKET:
            self.overlap = max(0.0, round(self.overlap - 0.02, 2))
            self.rebuild_layout()
        elif sym == sdl2.SDLK_RIGHTBRACKET:
            self.overlap = min(0.45, round(self.overlap + 0.02, 2))
            self.rebuild_layout()
        elif sym == sdl2.SDLK_r:
            self.blend_enabled = True
            self.lift_enabled = True
            self.blend_gamma = 2.2
            self.overlap = 0.15
            self.rebuild_layout()
        else:
            return True
        self._print_state()
        return True

    def _print_help(self):
        print(__doc__)

    def _print_state(self):
        print(f"[demo_blend] blend={'on' if self.blend_enabled else 'OFF'}  "
              f"gamma={self.blend_gamma:.2f}  "
              f"lift={'on' if self.lift_enabled else 'OFF'} ({self.lift:.3f})  "
              f"overlap={self.overlap:.2f} ({self.outputs[1].left_overlap * self.output_w:.0f}px)  "
              f"canvas={self.canvas_w}x{self.output_h}")

    def close(self):
        sdl2.SDL_GL_DeleteContext(self.glctx)
        sdl2.SDL_DestroyWindow(self.window)
        sdl2.SDL_Quit()


def _quad_verts():
    import array
    # Two triangles covering NDC [-1,1]^2, as a triangle strip.
    return array.array("f", [-1, -1, 1, -1, -1, 1, 1, 1]).tobytes()


def _homography_to_mat3(H: dict) -> bytes:
    import array
    # column-major, matching GLSL mat3's memory layout: mat3(col0, col1, col2)
    m = [H["a"], H["d"], H["g"],
         H["b"], H["e"], H["h"],
         H["c"], H["f"], 1.0]
    return array.array("f", m).tobytes()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", type=int, default=3, help="number of simulated virtual projectors")
    ap.add_argument("--width", type=int, default=640, help="each output's width, px")
    ap.add_argument("--height", type=int, default=480, help="each output's height, px")
    ap.add_argument("--overlap", type=float, default=0.15, help="interior overlap as a fraction of output width")
    ap.add_argument("--screenshot", help="write a PNG here and exit instead of opening an interactive loop")
    ap.add_argument("--screenshot-frame", type=int, default=1, help="advance this many frames before capturing")
    args = ap.parse_args(argv)

    demo = BlendDemo(args.outputs, args.width, args.height, args.overlap)
    try:
        if args.screenshot:
            for _ in range(args.screenshot_frame):
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
