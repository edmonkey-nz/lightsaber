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

GET/POST /canvas-thumb/{name} — a low-res JPEG snapshot of that canvas's
editor view, captured client-side right after canvas_save (see index.html)
and pushed up as a plain HTTP POST (raw JPEG bytes, not the websocket —
same reasoning as media upload below: binary payloads don't belong on the
state-broadcast channel). GET serves it back for the sequencer step list's
thumbnails; 404 if that canvas was never saved since this feature shipped.

    {"type":"project_set_resolution", "width":1920, "height":1080}   # per-project, not per-canvas
    {"type":"project_set_notes", "notes":"..."}   # free text, this project's own run sheet
    {"type":"project_set_output", "index":0,
     "key":"flip_x|flip_y|keystone_h|keystone_v|viewport_x|viewport_y|viewport_w|viewport_h|duplicate_of",
     "value":...}
        # index 0 = Output 1, 1 = Output 2, etc — see projects.py's
        # OutputMonitorConfig. Edited/saved only via explicit Project save,
        # same as resolution above — not auto-persisted per keystroke.
    {"type":"project_set_output_count", "count":2}   # resizes the outputs list — see set_output_count
    {"type":"project_set_viewports_enabled", "value":true}
    {"type":"project_set_show_fps", "value":true}
    {"type":"project_set_fps", "value":60}
    {"type":"set_test_pattern", "index":0, "value":true}
        # Calibration aid, NOT persisted (engine-runtime only — see
        # composite.py's set_test_pattern): shows a keystone-aware alignment
        # grid on that output window instead of the live composite.
    {"type":"poly_add", "scene":"..."}           # scene optional
    {"type":"poly_delete", "id":"..."}
    {"type":"poly_corners", "id":"...", "corners":[[x,y]x4]}
    {"type":"poly_set", "id":"...", "key":"scene|opacity|...", "value":...}
    {"type":"poly_reorder", "id":"...", "index":0}
    {"type":"seq_add", "canvas":"...", "duration":8.0, "crossfade":0.0}
    {"type":"seq_remove"/"seq_reorder"/"seq_set", "id":"..."}   # seq_set: canvas/duration/crossfade optional
    {"type":"seq_play"/"seq_pause"/"seq_stop"/"seq_next"/"seq_prev"}
    {"type":"seq_goto", "index":0}

Effector matrix messages (see effectors.py) — sources/routes/tempo live on
the current canvas, saved with it:

    {"type":"set_tempo", "value":120}
    {"type":"set_audio_bands", "low":0.0, "mid":0.0, "high":0.0}   # engine-wide, not per-canvas — see composite.py
    {"type":"effector_source_add"} / effector_source_remove {"id":...}
    {"type":"effector_source_set", "id":"...", "key":"shape|rate_hz|sync|phase|pulse_width|name", "value":...}
    {"type":"effector_route_add"} / effector_route_remove {"id":...}
    {"type":"effector_route_set", "id":"...", "key":"source_id|target_poly|target_param|depth|offset|curve|mode", "value":...}

Media upload widget (Input > Local video/image files) is plain HTTP, not the
websocket — see media_list/media_upload/media_delete below.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.parse

from aiohttp import web, WSMsgType

from .. import settings
from .. import media_roots
from .. import media_meta

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
        # ?client=renderhost identifies a renderhost/serve.py process phoning
        # home (see its --lightsaber-url flag) purely so the header's "Render
        # Host" dot can reflect whether one is actually up and reachable —
        # not yet a content pipeline (renderhost doesn't consume the
        # composite broadcast at all yet, see ARCHITECTURE.md §12).
        request.app["clients"][ws] = {
            "hq": request.query.get("hq") == "1",
            "renderhost": request.query.get("client") == "renderhost",
        }
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
        # Paced by engine.composite.fps (--fps), NOT a fixed rate — this
        # used to be a hardcoded 20Hz (`asyncio.sleep(0.05)`) regardless of
        # what --fps was set to, which silently capped every client's
        # observed frame rate at ~20 no matter how fast the compositor's own
        # render loop (composite.py's _loop, which DOES already respect
        # --fps) was actually producing frames. --fps 60 looked like it did
        # nothing because the broadcaster was still only picking up and
        # sending a new frame 20 times a second.
        try:
            while True:
                period = 1.0 / max(1, engine.composite.fps)
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
                        # in the terminal on process exit. Fixed short backoff
                        # here (not `period`) on purpose — an error retry loop
                        # shouldn't spin at whatever --fps happens to be.
                        print(f"[lightsaber] broadcaster: skipped a bad state "
                              f"tick ({e}); continuing")
                        await asyncio.sleep(0.05)
                        continue
                    renderhost_connected = any(
                        meta.get("renderhost") for meta in app["clients"].values())
                    payload_std = json.dumps({"type": "state", "state": state, "composite": composite_std,
                                              "renderhost_connected": renderhost_connected})
                    payload_hq = None   # built lazily, only if an hq client is actually connected
                    for ws, meta in list(app["clients"].items()):
                        try:
                            if meta.get("hq"):
                                if payload_hq is None:
                                    composite_hq = engine.composite_preview(max_points=8000, stroke_thin=400)
                                    payload_hq = json.dumps({"type": "state", "state": state, "composite": composite_hq,
                                                             "renderhost_connected": renderhost_connected})
                                await ws.send_str(payload_hq)
                            else:
                                await ws.send_str(payload_std)
                        except Exception:
                            app["clients"].pop(ws, None)
                await asyncio.sleep(period)
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

    # Canvas thumbnails (see the module docstring above) — plain HTTP, same
    # channel as media upload, not the websocket. Looked up via
    # engine.canvases (live reference, reflects whichever project is
    # currently open) rather than a static mount, since the on-disk
    # directory changes on project switch.
    async def canvas_thumb_get(request):
        path = engine.canvases.thumb_path_for(request.match_info["name"])
        if not os.path.isfile(path):
            return web.Response(status=404)
        return web.FileResponse(path)

    async def canvas_thumb_post(request):
        data = await request.read()
        if not data or len(data) > 2_000_000:
            return web.json_response({"ok": False, "error": "bad thumbnail"}, status=400)
        engine.canvases.save_thumbnail(request.match_info["name"], data)
        return web.json_response({"ok": True})

    # Linked media (see media_roots.py) — files referenced where they live
    # instead of copied into media_dir. EVERY path here goes through
    # media_roots.resolve(), which is the allowlist/containment boundary;
    # this server listens on 0.0.0.0 by default, so a route that served
    # arbitrary absolute paths would be a remote file read for anyone on the
    # same network. Nothing outside a configured root is reachable.
    async def media_link_file(request):
        link = f"{request.match_info['root']}::{request.match_info['tail']}"
        path = media_roots.resolve(link)
        if not path or not os.path.isfile(path):
            return web.Response(status=404)
        # FileResponse handles Range requests, which is what makes seeking
        # (and a browser's own video buffering) work on a large file.
        return web.FileResponse(path)

    async def media_roots_list(request):
        return web.json_response({"roots": [
            {"name": n, "path": p} for n, p in sorted(media_roots.roots().items())]})

    async def media_roots_add(request):
        body = await request.json()
        ok, msg = media_roots.set_root(body.get("name", ""), body.get("path", ""))
        return web.json_response({"ok": ok, "error": None if ok else msg},
                                 status=200 if ok else 400)

    async def media_roots_remove(request):
        ok = media_roots.remove_root(request.match_info["name"])
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    async def media_browse(request):
        result = media_roots.browse(request.query.get("root", ""),
                                    request.query.get("path", ""))
        return web.json_response(result,
                                 status=400 if result.get("error") else 200)

    app.router.add_get("/", index)
    app.router.add_get("/output", output_page)
    # Unified asset inventory for the Media tab — the app's own uploaded
    # library AND every linked file this install knows about, in one list.
    #
    # "Knows about" is deliberately the union of three sources rather than a
    # directory scan: linked files live wherever the user keeps them, so the
    # only records of them are the shapes that reference one and any trim
    # already stored against one. A link whose last shape was just deleted
    # still appears (via media_meta) so its trim isn't silently orphaned.
    async def media_assets(request):
        assets = {}

        def entry(key, kind, name):
            if key not in assets:
                meta = media_meta.get(key)
                assets[key] = {"key": key, "kind": kind, "name": name,
                               "duration": meta["duration"], "in": meta["in"],
                               "out": meta["out"], "used_by": []}
            return assets[key]

        for fname in sorted(os.listdir(media_dir)):
            path = os.path.join(media_dir, fname)
            if not os.path.isfile(path):
                continue
            e = entry("lib:" + fname, "lib", fname)
            e.update(url=f"/media-files/{fname}", path=path,
                     size=os.path.getsize(path), exists=True)

        # Usage across THIS project's whole canvas library, not just the open
        # canvas — "where is this clip used" is only useful if it covers the
        # canvases you're not currently looking at.
        canvases = engine.canvases
        for cname in canvases.names():
            try:
                spec = canvases.load_spec(cname)
            except Exception:
                continue
            for i, poly in enumerate(spec.polygons):
                if poly.source_type != "media":
                    continue
                if poly.media_link:
                    key, kind, name = ("link:" + poly.media_link, "link",
                                       poly.media_link.split("::")[-1].split("/")[-1])
                elif poly.media:
                    key, kind, name = "lib:" + poly.media, "lib", poly.media
                else:
                    continue
                e = entry(key, kind, name)
                e["used_by"].append({"canvas": cname,
                                     "shape": poly.label or f"shape {i + 1}"})

        for key in media_meta.all_meta():
            if key.startswith("link:"):
                entry(key, "link", key.split("::")[-1].split("/")[-1])
            elif key.startswith("lib:"):
                entry(key, "lib", key[4:])

        # Resolve every linked asset now: a root may have been removed or a
        # drive unplugged since the link was stored.
        for key, e in assets.items():
            if e["kind"] != "link":
                continue
            link = key[5:]
            path = media_roots.resolve(link)
            exists = bool(path) and os.path.isfile(path)
            e["link"] = link
            e["path"] = path
            e["exists"] = exists
            e["size"] = os.path.getsize(path) if (exists and path) else None
            root, _, rel = link.partition("::")
            tail = "/".join(urllib.parse.quote(seg) for seg in rel.split("/"))
            e["url"] = f"/media-link/{urllib.parse.quote(root)}/{tail}"

        for e in assets.values():
            e.setdefault("exists", False)
            e.setdefault("size", None)
            e.setdefault("path", None)
            e.setdefault("url", None)

        return web.json_response({"assets": sorted(
            assets.values(), key=lambda a: (not a["exists"], a["name"].lower()))})

    # Relink: point every shape that uses one asset at a different file.
    # This is the "the drive got remounted somewhere else" repair, so it
    # deliberately rewrites SAVED canvases on disk rather than only the one
    # currently open — a relink that fixed 1 of 12 shapes would be worse than
    # none. The open canvas is patched in memory too, so the editor doesn't
    # keep showing the stale link until reload. The UI confirms first.
    async def media_relink(request):
        body = await request.json()
        from_key = str(body.get("from_key") or "")
        to_link = str(body.get("to_link") or "")
        if not media_roots.resolve(to_link):
            return web.json_response({"ok": False, "error": "target isn't inside a media root"},
                                     status=400)

        def repoint(poly) -> bool:
            key = ("link:" + poly.media_link) if poly.media_link else (
                "lib:" + poly.media if poly.media else None)
            if key != from_key:
                return False
            poly.media_link = to_link
            poly.media = None
            return True

        canvases, changed, touched = engine.canvases, 0, []
        for cname in canvases.names():
            try:
                spec = canvases.load_spec(cname)
            except Exception:
                continue
            hits = sum(1 for p in spec.polygons if repoint(p))
            if hits:
                canvases.save(cname, spec)
                changed += hits
                touched.append(cname)
        # ...and the in-memory copy, which the disk pass above didn't touch.
        live = sum(1 for p in canvases.current.polygons if repoint(p))
        # Carry the trim over so a relinked file keeps the in/out you set.
        meta = media_meta.get(from_key)
        if meta["in"] or meta["out"] is not None:
            media_meta.set_trim("link:" + to_link, meta["in"], meta["out"])
        return web.json_response({"ok": True, "shapes": changed + live,
                                  "canvases": touched})

    # Asset-level media metadata (duration + default trim). Duration is
    # measured by whichever client first loads the file — the server never
    # opens it, so there's no ffprobe dependency.
    async def media_meta_list(request):
        return web.json_response({"meta": media_meta.all_meta()})

    async def media_meta_duration(request):
        body = await request.json()
        media_meta.set_duration(body.get("key", ""), body.get("duration"))
        return web.json_response({"ok": True})

    async def media_meta_trim(request):
        body = await request.json()
        entry = media_meta.set_trim(body.get("key", ""), body.get("in"), body.get("out"))
        return web.json_response({"ok": True, "meta": entry})

    app.router.add_get("/media/assets", media_assets)
    app.router.add_post("/media/relink", media_relink)
    app.router.add_get("/media/meta", media_meta_list)
    app.router.add_post("/media/meta/duration", media_meta_duration)
    app.router.add_post("/media/meta/trim", media_meta_trim)
    app.router.add_get("/media/roots", media_roots_list)
    app.router.add_post("/media/roots", media_roots_add)
    app.router.add_delete("/media/roots/{name}", media_roots_remove)
    app.router.add_get("/media/browse", media_browse)
    app.router.add_get("/media-link/{root}/{tail:.*}", media_link_file)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/media/list", media_list)
    app.router.add_post("/media/upload", media_upload)
    app.router.add_delete("/media/{filename}", media_delete)
    app.router.add_get("/canvas-thumb/{name}", canvas_thumb_get)
    app.router.add_post("/canvas-thumb/{name}", canvas_thumb_post)
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
    elif t == "project_set_resolution":
        engine.composite.set_project_resolution(m["width"], m["height"])
    elif t == "project_set_notes":
        engine.composite.set_project_notes(m.get("notes", ""))
    elif t == "project_set_output":
        engine.composite.set_output_config(m["index"], m["key"], m.get("value"))
    elif t == "project_set_output_count":
        engine.composite.set_output_count(m["count"])
    elif t == "project_set_viewports_enabled":
        engine.composite.set_viewports_enabled(bool(m.get("value")))
    elif t == "project_set_show_fps":
        engine.composite.set_show_fps(bool(m.get("value")))
    elif t == "project_set_fps":
        engine.composite.set_project_fps(m.get("value", 20))
    elif t == "set_test_pattern":
        engine.composite.set_test_pattern(m["index"], bool(m.get("value")))
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
    # Effector matrix (see effectors.py) — sources/routes/tempo live on the
    # current canvas (CanvasSpec.effectors), same queued-mutation discipline
    # as everything else in composite.py.
    elif t == "set_tempo":
        engine.composite.set_tempo(m.get("value", 120))
    elif t == "set_audio_bands":
        engine.composite.set_audio_bands(m.get("low", 0), m.get("mid", 0), m.get("high", 0))
    elif t == "effector_source_add":
        engine.composite.effector_source_add()
    elif t == "effector_source_remove":
        engine.composite.effector_source_remove(m["id"])
    elif t == "effector_source_set":
        engine.composite.effector_source_set(m["id"], m["key"], m.get("value"))
    elif t == "effector_route_add":
        engine.composite.effector_route_add()
    elif t == "effector_route_remove":
        engine.composite.effector_route_remove(m["id"])
    elif t == "effector_route_set":
        engine.composite.effector_route_set(m["id"], m["key"], m.get("value"))
    elif t == "seq_add":
        engine.composite.seq_add(m["canvas"], m.get("duration", 8.0), m.get("crossfade", 0.0))
    elif t == "seq_remove":
        engine.composite.seq_remove(m["id"])
    elif t == "seq_reorder":
        engine.composite.seq_reorder(m["id"], m["index"])
    elif t == "seq_set":
        engine.composite.seq_set(m["id"], canvas=m.get("canvas"), duration=m.get("duration"),
                                  crossfade=m.get("crossfade"))
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
