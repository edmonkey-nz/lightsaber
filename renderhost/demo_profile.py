"""Renders ONE group from a profile.py config file — the actual point of
having a config layer: this script contains no layout logic of its own,
unlike demo_blend.py (invents layout from CLI args) and
demo_real_displays.py (derives layout from live-detected displays every
run). Everything here comes from the loaded profile: which outputs, where,
what overlap/gamma/lift, what corners.

Multi-GROUP rendering (each group potentially on different real displays,
possibly needing its own GL context) is deferred — see README.md. This
renders one group at a time, selected with --group.

Run:
    python3 -m renderhost.profile from-real /tmp/rig.json --overlap 200 --gamma 2.2
    python3 -m renderhost.profile validate /tmp/rig.json
    python3 -m renderhost.demo_profile /tmp/rig.json --group main
    python3 -m renderhost.demo_profile /tmp/rig.json --group main --screenshot /tmp/out.png
"""

from __future__ import annotations

import argparse
import ctypes
import sys

import moderngl
import sdl2

if __package__ in (None, ""):
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from renderhost import correction_chain as cc
    from renderhost import profile as profilemod
else:
    from . import correction_chain as cc
    from . import profile as profilemod


class ProfileDemo:
    def __init__(self, spec: "profilemod.ProfileSpec", group_id: str, fullscreen: bool):
        group = next((g for g in spec.groups if g.id == group_id), None)
        if group is None:
            available = [g.id for g in spec.groups]
            raise SystemExit(f"no group {group_id!r} in this profile — available: {available}")
        self.group = group
        self.outputs, self.canvas_w, self.canvas_h, self.origin_x, self.origin_y = profilemod.layout_group(group)
        if not self.outputs:
            raise SystemExit(f"group {group_id!r} has no outputs")

        self.blend_enabled = True
        self.lift_enabled = True
        self.blend_gamma = group.blend.gamma
        self.black_leak = max(group.blend.lift, 0.02)   # a leak needs SOME value to demonstrate lift against — see demo_blend.py
        self.lift = group.blend.lift

        self._init_sdl_and_gl(fullscreen)
        self._init_gl_objects()
        self._print_state()

    def _init_sdl_and_gl(self, fullscreen: bool):
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(f"SDL_Init failed: {sdl2.SDL_GetError()}")
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MAJOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_MINOR_VERSION, 3)
        sdl2.SDL_GL_SetAttribute(sdl2.SDL_GL_CONTEXT_PROFILE_MASK, sdl2.SDL_GL_CONTEXT_PROFILE_CORE)
        flags = sdl2.SDL_WINDOW_OPENGL | (0 if fullscreen else sdl2.SDL_WINDOW_BORDERLESS)
        title = f"lightsaber render host — profile group {self.group.id!r}".encode()
        # Positioned at the group's real absolute desktop coordinates (same
        # reasoning as demo_real_displays.py) — the whole point of a profile
        # is that a saved rig lands on the correct real screens, not
        # wherever the window manager happens to place an unpositioned window.
        self.window = sdl2.SDL_CreateWindow(
            title, self.origin_x, self.origin_y,
            self.canvas_w, self.canvas_h, flags)
        if not self.window:
            raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
        if fullscreen:
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
        # Same reasoning as demo_real_displays.py: real, non-overlapping
        # output regions, so no additive window-space blending.
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
        print(f"[demo_profile] wrote {path}")

    def handle_key(self, sym: int) -> bool:
        if sym in (sdl2.SDLK_ESCAPE, sdl2.SDLK_q):
            return False
        elif sym == sdl2.SDLK_b:
            self.blend_enabled = not self.blend_enabled
        elif sym == sdl2.SDLK_l:
            self.lift_enabled = not self.lift_enabled
        else:
            return True
        self._print_state()
        return True

    def _print_state(self):
        print(f"[demo_profile] group={self.group.id!r}  outputs={len(self.outputs)}  "
              f"blend={'on' if self.blend_enabled else 'OFF'}  gamma={self.blend_gamma:.2f}  "
              f"lift={'on' if self.lift_enabled else 'OFF'} ({self.lift:.3f})  "
              f"canvas={self.canvas_w}x{self.canvas_h}")

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
    ap.add_argument("profile", help="path to a profile JSON (see renderhost.profile)")
    ap.add_argument("--group", default=None, help="group id to render (default: the first group in the file)")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--screenshot", help="write a PNG and exit instead of an interactive loop")
    args = ap.parse_args(argv)

    spec = profilemod.load_profile(args.profile)
    if not spec.groups:
        raise SystemExit(f"{args.profile} has no groups")
    group_id = args.group or spec.groups[0].id

    demo = ProfileDemo(spec, group_id, args.fullscreen)
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
