"""The web control panel — ARCHITECTURE.md §8's intended end-state
(browser UI on a control surface, websocket to the render host), scoped
down to what §9's build order actually has ready to control right now:
live overlap/gamma/lift tuning for one already-loaded profile group, plus
saving the tuned values back to the file. NOT the alignment UI (§9 item
4, still corner-drag-free — corners stay hand-edited JSON) and NOT
multi-group live switching (still one `--group` per process, per
README.md's deferred list).

Architecture note, since this is the first script in this directory that
has to reconcile a blocking GL render loop with a networked control
surface: the SDL/GL loop stays on the main thread (as in every other demo
here), aiohttp's websocket server runs on a background thread with its own
asyncio event loop, and the two talk through a thread-safe queue drained at
the top of each render tick — the EXACT discipline lightsaber's own
composite.py already uses for the same reason (composite.py's own docstring:
"queued mutations, applied at the top of _loop... so the render thread
never races the websocket thread"). Don't touch GL state from the
websocket thread; queue a callable instead, same as every `set_*` method
here does.

Run:
    python3 -m renderhost.profile from-real /tmp/rig.json --overlap 100
    python3 -m renderhost.serve /tmp/rig.json --group main
    # then open http://localhost:8090
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading

import sdl2
from aiohttp import web, WSMsgType

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from renderhost import profile as profilemod
    from renderhost.demo_profile import ProfileDemo
else:
    from . import profile as profilemod
    from .demo_profile import ProfileDemo

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class RenderHostServer(ProfileDemo):
    """ProfileDemo (the GL render loop, unmodified) plus a thread-safe
    control queue a websocket handler can push into safely from a different
    thread."""

    def __init__(self, profile_path: str, group_id: str, fullscreen: bool):
        self.profile_path = profile_path
        spec = profilemod.load_profile(profile_path)
        super().__init__(spec, group_id, fullscreen)
        self.spec = spec
        self._lock = threading.Lock()
        self._queue = []

    def _enqueue(self, fn):
        with self._lock:
            self._queue.append(fn)

    def drain_queue(self):
        with self._lock:
            q, self._queue = self._queue, []
        for fn in q:
            try:
                fn()
            except Exception as e:
                print(f"[serve] queued action error: {e}")

    def _relayout(self):
        # Overlap changes the crop split between neighbours but never the
        # group's own total canvas size, so this is safe to do live without
        # touching the FBO/texture. A future group-switch feature (not
        # this round) would need to handle a real resize.
        self.outputs, self.canvas_w, self.canvas_h, self.origin_x, self.origin_y = \
            profilemod.layout_group(self.group)

    def set_overlap(self, px: int):
        def apply():
            self.group.blend.overlap = max(0, int(px))
            self._relayout()
        self._enqueue(apply)

    def set_gamma(self, g: float):
        self._enqueue(lambda: setattr(self, "blend_gamma", max(0.2, float(g))))

    def set_lift_value(self, v: float):
        self._enqueue(lambda: setattr(self, "lift", max(0.0, float(v))))

    def toggle_blend(self):
        self._enqueue(lambda: setattr(self, "blend_enabled", not self.blend_enabled))

    def toggle_lift(self):
        self._enqueue(lambda: setattr(self, "lift_enabled", not self.lift_enabled))

    def save(self):
        def apply():
            self.group.blend.gamma = self.blend_gamma
            self.group.blend.lift = self.lift
            profilemod.save_profile(self.profile_path, self.spec)
            print(f"[serve] saved {self.profile_path}")
        self._enqueue(apply)

    def _output_config(self, name: str):
        return next((o for o in self.group.outputs if o.name == name), None)

    def _output_spec(self, name: str):
        return next((o for o in self.outputs if o.name == name), None)

    def set_corners(self, output_name: str, corners_px: list):
        def apply():
            oc = self._output_config(output_name)
            spec = self._output_spec(output_name)
            if oc is None or spec is None:
                return
            # Same pixel-space convention profile.py's on-disk format
            # already uses (sized to the output's own viewport) — stored on
            # the OutputConfig as-is (what save() persists), converted to
            # [0,1] UV for the live OutputSpec actually being rendered
            # (correction_chain.homography()'s own convention — see its
            # docstring for why the two differ).
            oc.corners = [[float(x), float(y)] for x, y in corners_px]
            spec.corners = oc.corners_uv()
        self._enqueue(apply)

    def reset_corners(self, output_name: str):
        def apply():
            oc = self._output_config(output_name)
            spec = self._output_spec(output_name)
            if oc is None or spec is None:
                return
            oc.corners = []   # [] is profile.py's own "identity, no keystone" convention
            spec.corners = oc.corners_uv()
        self._enqueue(apply)

    def take_screenshot(self, path: str):
        self._enqueue(lambda: self.screenshot(path))

    def state_dict(self) -> dict:
        return {
            "profile": self.profile_path,
            "groups": [g.id for g in self.spec.groups],
            "group": self.group.id,
            "canvas_w": self.canvas_w, "canvas_h": self.canvas_h,
            "outputs": [{"name": o.name, "viewport": list(o.window_rect),
                         "corners": self._output_config(o.name).corners}
                        for o in self.outputs],
            "blend_enabled": self.blend_enabled,
            "lift_enabled": self.lift_enabled,
            "gamma": round(self.blend_gamma, 3),
            "lift": round(self.lift, 4),
            "overlap": self.group.blend.overlap,
        }


def make_app(rhs: RenderHostServer) -> web.Application:
    app = web.Application()
    app["clients"] = set()

    async def index(request):
        return web.FileResponse(os.path.join(_WEB_DIR, "index.html"))

    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        app["clients"].add(ws)
        try:
            await ws.send_str(json.dumps({"type": "state", "state": rhs.state_dict()}))
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                _handle(rhs, m)
        finally:
            app["clients"].discard(ws)
        return ws

    async def broadcaster(app):
        import asyncio
        try:
            while True:
                if app["clients"]:
                    payload = json.dumps({"type": "state", "state": rhs.state_dict()})
                    for ws in list(app["clients"]):
                        try:
                            await ws.send_str(payload)
                        except Exception:
                            app["clients"].discard(ws)
                await asyncio.sleep(0.2)   # blend params don't animate; no need for 20Hz here
        except Exception:
            pass

    async def on_start(app):
        import asyncio
        app["broadcast_task"] = asyncio.create_task(broadcaster(app))

    async def on_cleanup(app):
        app["broadcast_task"].cancel()

    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/web/", _WEB_DIR)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_cleanup)
    return app


def _handle(rhs: RenderHostServer, m: dict):
    t = m.get("type")
    if t == "set_overlap":
        rhs.set_overlap(m.get("value", 0))
    elif t == "set_gamma":
        rhs.set_gamma(m.get("value", 2.2))
    elif t == "set_lift":
        rhs.set_lift_value(m.get("value", 0.0))
    elif t == "toggle_blend":
        rhs.toggle_blend()
    elif t == "toggle_lift":
        rhs.toggle_lift()
    elif t == "save":
        rhs.save()
    elif t == "set_corners":
        rhs.set_corners(m.get("output", ""), m.get("corners", []))
    elif t == "reset_corners":
        rhs.reset_corners(m.get("output", ""))
    elif t == "screenshot":
        rhs.take_screenshot(m.get("path", "/tmp/serve-screenshot.png"))


def start_web_server(rhs: RenderHostServer, port: int, host: str) -> threading.Thread:
    import asyncio

    async def _serve():
        # The lower-level runner/site API, not web.run_app() — run_app()
        # installs a SIGINT handler, which only works on the main thread
        # (Python's signal module is main-thread-only) and raised
        # "set_wakeup_fd only works in main thread of the main interpreter"
        # here, since aiohttp has to live in a background thread — the main
        # thread is reserved for the SDL/GL loop, same reasoning as the
        # queue-based control handoff this whole module is built around.
        app = make_app(rhs)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        while True:
            await asyncio.sleep(3600)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def start_heartbeat_client(lightsaber_url: str) -> threading.Thread:
    """Phones home to the lightsaber control server's `/ws` (tagged
    `?client=renderhost`) purely so its header's "Render Host" dot can
    reflect whether a render host process is actually up and reachable —
    see server.py's ws_handler. This is NOT the content pipeline: renderhost
    doesn't consume lightsaber's composite broadcast at all yet (still a
    separate profile-tuning demo — see ARCHITECTURE.md §12), so this
    connection carries nothing but the fact of being open. Runs in its own
    thread/event loop, same discipline as start_web_server above, and
    reconnects forever on drop (the lightsaber server restarting, or this
    process starting before it, shouldn't require a manual retry)."""
    import asyncio

    from aiohttp import ClientSession, WSMsgType

    sep = "&" if "?" in lightsaber_url else "?"
    full_url = f"{lightsaber_url}{sep}client=renderhost"

    async def _run():
        while True:
            try:
                async with ClientSession() as session:
                    async with session.ws_connect(full_url, heartbeat=20) as ws:
                        print(f"[serve] connected to lightsaber ({lightsaber_url})")
                        async for msg in ws:
                            if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSING):
                                break
            except Exception as e:
                print(f"[serve] lightsaber heartbeat: {e} — retrying in 5s")
            await asyncio.sleep(5)

    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile")
    ap.add_argument("--group", default=None)
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--port", type=int, default=8090,
                     help="deliberately different from lightsaber's own default 8080, "
                          "so both can run side by side without a port clash")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--lightsaber-url", default=None,
                     help="e.g. ws://192.168.50.1:8080/ws — if set, phones home so the "
                          "lightsaber control panel's header can show this render host as "
                          "connected (a heartbeat only, see start_heartbeat_client)")
    args = ap.parse_args(argv)

    spec_groups = profilemod.load_profile(args.profile).groups
    if not spec_groups:
        raise SystemExit(f"{args.profile} has no groups")
    group_id = args.group or spec_groups[0].id

    rhs = RenderHostServer(args.profile, group_id, args.fullscreen)
    start_web_server(rhs, args.port, args.host)
    print(f"[serve] control panel: http://localhost:{args.port}")
    if args.lightsaber_url:
        start_heartbeat_client(args.lightsaber_url)

    try:
        running = True
        event = sdl2.SDL_Event()
        while running:
            rhs.drain_queue()
            while sdl2.SDL_PollEvent(ctypes.byref(event)):
                if event.type == sdl2.SDL_QUIT:
                    running = False
                elif event.type == sdl2.SDL_KEYDOWN:
                    running = rhs.handle_key(event.key.keysym.sym)
            rhs.render_scene()
            rhs.render_outputs()
            sdl2.SDL_GL_SwapWindow(rhs.window)
            sdl2.SDL_Delay(16)
        return 0
    finally:
        rhs.close()


if __name__ == "__main__":
    sys.exit(main())
