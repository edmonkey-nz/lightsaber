"""aiohttp control surface.

Serves the single-page UI and a websocket that (a) broadcasts engine state +
a composite frame ~20 Hz, and (b) receives control messages:

    {"type":"set_active", "value":true}
    {"type":"set_frozen", "value":true}
    {"type":"set_visuals_disabled", "value":true, "fade":2.0}
    {"type":"generate", "keyword":"aurora over a still lake", "aspect":"16:9"}
    {"type":"scene_delete", "name":"..."}
    {"type":"set_max_pps", "value":20000}

Project messages (see projects.py):

    {"type":"project_open"/"project_new", "name":"..."}
    {"type":"project_save", "name":"..."}
    {"type":"project_save_as", "src":"...", "dst":"..."}
    {"type":"project_delete", "name":"..."}

Canvas/sequencer messages (the renderer — see composite.py):

    {"type":"canvas_new"} / canvas_load / canvas_save / canvas_delete  {"name":...}
    {"type":"canvas_set_resolution", "width":1920, "height":1080}
    {"type":"poly_add", "scene":"..."}           # scene optional
    {"type":"poly_delete", "id":"..."}
    {"type":"poly_corners", "id":"...", "corners":[[x,y]x4]}
    {"type":"poly_set", "id":"...", "key":"scene|opacity|...", "value":...}
    {"type":"poly_reorder", "id":"...", "index":0}
    {"type":"seq_add", "canvas":"...", "duration":8.0}
    {"type":"seq_remove"/"seq_reorder"/"seq_set", "id":"..."}
    {"type":"seq_play"/"seq_pause"/"seq_stop"/"seq_next"/"seq_prev"}
    {"type":"seq_goto", "index":0}

Media upload widget (Input > Local video/image files) is plain HTTP, not the
websocket — see media_list/media_upload/media_delete below.
"""

from __future__ import annotations

import asyncio
import json
import os

from aiohttp import web, WSMsgType

from .. import settings

_STATIC = os.path.join(os.path.dirname(__file__), "static")

_MEDIA_KINDS = {
    ".mp4": "video", ".mov": "video", ".webm": "video", ".m4v": "video",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
}


def _safe_media_name(filename: str) -> str:
    # Must allow "()" — _unique_media_path appends "(2)"/"(3)"/... to dedupe
    # collisions, and this same sanitiser runs again on every delete lookup
    # (via the filename the list endpoint handed back), so a collision-
    # suffixed name has to round-trip through here unchanged.
    base = os.path.basename(filename or "")
    safe = "".join(c for c in base if c.isalnum() or c in " _.()-").strip(" .")
    return safe or "upload"


def _unique_media_path(media_dir: str, name: str) -> str:
    # Uploads must never silently clobber an earlier one with the same
    # filename — append " (2)", " (3)", ... the same way a browser's own
    # download manager avoids collisions.
    root, ext = os.path.splitext(name)
    candidate, i = name, 1
    while os.path.exists(os.path.join(media_dir, candidate)):
        i += 1
        candidate = f"{root} ({i}){ext}"
    return candidate


def make_app(engine, media_dir: str) -> web.Application:
    os.makedirs(media_dir, exist_ok=True)
    # aiohttp's default client_max_size (1MB) is a websocket/JSON-API
    # ceiling, not a "someone uploads a home video" one — raise it for the
    # simple media-upload widget (Input > Local video/image files).
    app = web.Application(client_max_size=1024 * 1024 * 1024)
    app["engine"] = engine
    app["clients"] = {}   # ws -> {"hq": bool}

    def _no_cache(resp):
        # The whole UI lives in one HTML file and changes across sessions
        # during development; without this, a browser tab left open (or even
        # just reopened) can silently keep serving an old cached copy —
        # looking exactly like "features went missing" even though the server
        # is fully up to date. Force a fresh fetch every load.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    async def index(request):
        return _no_cache(web.FileResponse(os.path.join(_STATIC, "index.html")))

    async def output_page(request):
        # A bare, chrome-less fullscreen canvas — meant to be opened as its
        # own window and dragged onto a projector or second screen. No
        # controls: it's just another websocket client watching the same
        # composite broadcast the control UI does.
        return _no_cache(web.FileResponse(os.path.join(_STATIC, "output.html")))

    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # ?hq=1 (the standalone output window) asks for a much less thinned
        # composite than the small in-page control-UI canvas needs — it's
        # the actual thing being watched, not just a status glance.
        request.app["clients"][ws] = {"hq": request.query.get("hq") == "1"}
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    reply = await _handle(engine, json.loads(msg.data))
                    if reply is not None:
                        await ws.send_str(json.dumps(reply))
        finally:
            request.app["clients"].pop(ws, None)
        return ws

    async def broadcaster(app):
        try:
            while True:
                if app["clients"]:
                    try:
                        state = engine.state()
                        composite_std = engine.composite_preview()
                    except Exception as e:
                        # Never let a bad tick (e.g. a non-JSON-serialisable
                        # value slipping into state()) kill this task. Before
                        # this guard, an exception here escaped the while loop
                        # entirely and ended the broadcaster permanently and
                        # silently — the UI would show "linked" (the websocket
                        # itself is fine) but never receive another update for
                        # the rest of the session, with the error only surfacing
                        # in the terminal on process exit.
                        print(f"[lightsaber] broadcaster: skipped a bad state "
                              f"tick ({e}); continuing")
                        await asyncio.sleep(0.05)
                        continue
                    payload_std = json.dumps({"type": "state", "state": state, "composite": composite_std})
                    payload_hq = None   # built lazily, only if an hq client is actually connected
                    for ws, meta in list(app["clients"].items()):
                        try:
                            if meta.get("hq"):
                                if payload_hq is None:
                                    composite_hq = engine.composite_preview(max_points=8000, stroke_thin=400)
                                    payload_hq = json.dumps({"type": "state", "state": state, "composite": composite_hq})
                                await ws.send_str(payload_hq)
                            else:
                                await ws.send_str(payload_std)
                        except Exception:
                            app["clients"].pop(ws, None)
                await asyncio.sleep(0.05)   # ~20 Hz
        except asyncio.CancelledError:
            pass

    async def on_start(app):
        app["broadcast_task"] = asyncio.create_task(broadcaster(app))

    async def on_cleanup(app):
        app["broadcast_task"].cancel()

    # Media upload widget (Input > Local video/image files) — plain
    # HTTP, not the websocket: file bodies don't belong on the same
    # channel as engine state broadcasts. Not wired into any scene/canvas
    # yet, just upload + list + delete storage.
    async def media_list(request):
        out = []
        for name in sorted(os.listdir(media_dir)):
            path = os.path.join(media_dir, name)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            out.append({
                "name": name, "url": f"/media-files/{name}",
                "kind": _MEDIA_KINDS.get(ext, "other"),
                "size": os.path.getsize(path),
            })
        return web.json_response(out)

    async def media_upload(request):
        reader = await request.multipart()
        saved = []
        while True:
            field = await reader.next()
            if field is None:
                break
            if not field.filename:
                continue
            name = _unique_media_path(media_dir, _safe_media_name(field.filename))
            size = 0
            with open(os.path.join(media_dir, name), "wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    size += len(chunk)
                    f.write(chunk)
            saved.append({"name": name, "url": f"/media-files/{name}", "size": size})
        return web.json_response({"ok": True, "saved": saved})

    async def media_delete(request):
        name = _safe_media_name(request.match_info["filename"])
        path = os.path.join(media_dir, name)
        if os.path.isfile(path):
            os.remove(path)
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "not found"}, status=404)

    app.router.add_get("/", index)
    app.router.add_get("/output", output_page)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/media/list", media_list)
    app.router.add_post("/media/upload", media_upload)
    app.router.add_delete("/media/{filename}", media_delete)
    app.router.add_static("/static/", _STATIC)
    app.router.add_static("/media-files/", media_dir)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_cleanup)
    return app


async def _handle(engine, m: dict):
    t = m.get("type")
    loop = asyncio.get_event_loop()
    if t == "set_active":
        engine.composite.set_active(bool(m.get("value")))
    elif t == "set_frozen":
        engine.composite.set_frozen(bool(m.get("value")))
    elif t == "set_diagnostics":
        engine.composite.set_diagnostics(bool(m.get("value")))
    elif t == "set_visuals_disabled":
        fade = float(m.get("fade", 2.0))
        (engine.composite.disable_visuals if m.get("value") else engine.composite.enable_visuals)(fade=fade)
    elif t == "set_max_pps":
        # Scene-generation complexity budget (how much geometry Claude is
        # told to keep a scene under) — not a hardware rate, see director.py.
        engine.director.set_max_pps(int(m.get("value", 20000)))
    elif t == "generate":
        # run the (possibly networked) director off the event loop, then ack
        # with the saved library name so the UI can drop it straight onto
        # the canvas (see index.html's generate_result handler)
        name = await loop.run_in_executor(None, engine.generate_scene,
                                          m["keyword"], m.get("name"), m.get("size", "small"),
                                          m.get("aspect", "1:1"))
        return {"type": "generate_result", "ok": True, "name": name,
                "source": engine.director.last_source,
                "error": engine.director.last_error}
    elif t == "set_model":
        engine.director.set_model(m.get("value", "haiku"))
    elif t == "set_effort":
        engine.director.set_effort(m.get("value", "med"))
    elif t == "scene_delete":
        engine.scenes.delete(m["name"])
    # Projects (see projects.py) — the base-level container, each owning its
    # own canvas library + sequencer document. `current_project` is
    # persisted so the same project reopens on the next launch.
    elif t == "project_open":
        engine.composite.set_project(m["name"])
        settings.set("current_project", m["name"])
    elif t == "project_new":
        engine.composite.new_project(m["name"])
        settings.set("current_project", m["name"])
    elif t == "project_save":
        engine.composite.save_project(m["name"])
    elif t == "project_save_as":
        engine.composite.save_project_as(m["src"], m["dst"])
        settings.set("current_project", m["dst"])
    elif t == "project_delete":
        engine.composite.delete_project(m["name"])
    # Canvas / sequencer (the renderer — see composite.py). All mutations go
    # through engine.composite, which queues them onto its own render thread.
    elif t == "canvas_new":
        engine.composite.new_canvas()
    elif t == "canvas_load":
        engine.composite.load_canvas(m["name"])
    elif t == "canvas_save":
        engine.composite.save_canvas(m["name"])
    elif t == "canvas_delete":
        engine.composite.delete_canvas(m["name"])
    elif t == "canvas_set_resolution":
        engine.composite.set_canvas_resolution(m["width"], m["height"])
    elif t == "poly_add":
        engine.composite.add_polygon(m.get("scene"))
    elif t == "poly_delete":
        engine.composite.delete_polygon(m["id"])
    elif t == "poly_corners":
        engine.composite.set_polygon_corners(m["id"], m["corners"])
    elif t == "poly_set":
        engine.composite.set_polygon(m["id"], m["key"], m.get("value"))
    elif t == "poly_reorder":
        engine.composite.reorder_polygon(m["id"], m["index"])
    elif t == "seq_add":
        engine.composite.seq_add(m["canvas"], m.get("duration", 8.0))
    elif t == "seq_remove":
        engine.composite.seq_remove(m["id"])
    elif t == "seq_reorder":
        engine.composite.seq_reorder(m["id"], m["index"])
    elif t == "seq_set":
        engine.composite.seq_set(m["id"], canvas=m.get("canvas"), duration=m.get("duration"))
    elif t == "seq_loop":
        engine.composite.seq_set_loop(bool(m.get("value")))
    elif t == "seq_play":
        engine.composite.seq_play()
    elif t == "seq_pause":
        engine.composite.seq_pause()
    elif t == "seq_stop":
        engine.composite.seq_stop()
    elif t == "seq_next":
        engine.composite.seq_next()
    elif t == "seq_prev":
        engine.composite.seq_prev()
    elif t == "seq_goto":
        engine.composite.seq_goto(m["index"])
    elif t == "set_api_key":
        engine.director.set_api_key(m.get("key", ""))
        return {"type": "api_result", "action": "save", "ok": engine.director.online,
                "detail": "key saved" if engine.director.online else "key saved (package missing?)"}
    elif t == "test_api_key":
        # a real network call — keep it off the event loop
        result = await loop.run_in_executor(None, engine.director.test)
        return {"type": "api_result", "action": "test", **result}
    return None


def run(engine, media_dir, host="0.0.0.0", port=8080):
    web.run_app(make_app(engine, media_dir), host=host, port=port, print=None)
