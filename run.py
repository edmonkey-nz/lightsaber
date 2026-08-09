#!/usr/bin/env python3
"""Lightsaber entry point.

Examples
--------
    # serve the browser control surface
    python run.py --web

    # use Claude as the scene director
    ANTHROPIC_API_KEY=sk-... python run.py --web

Then open http://localhost:8080, build a canvas of shapes, and generate or
assign scenes into them.
"""

from __future__ import annotations

import argparse
import os
import shutil

from lightsaber import settings
from lightsaber.engine import Engine
from lightsaber.web import run as run_web

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    ap = argparse.ArgumentParser(description="Lightsaber — a live canvas/shape visual instrument")
    ap.add_argument("--web", action="store_true", help="serve the browser control surface")
    ap.add_argument("--web-port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--fps", type=int, default=20,
                    help="compositor render rate at startup; a project's own saved "
                         "fps (Project > Performance) overrides this once opened")
    ap.add_argument("--media-root", action="append", default=[], metavar="NAME=PATH",
                    help="register a folder that linked media may be referenced from, "
                         "e.g. --media-root videos=/home/me/Videos. Repeatable. Only "
                         "files under a registered root are ever served (this server "
                         "listens on all interfaces by default). Persists to "
                         "settings.json, so this is a one-off per folder.")
    ap.add_argument("--diag", action="store_true",
                    help="enable perf diagnostics instrumentation at startup (off by "
                         "default; also toggleable live in Settings)")
    ap.add_argument("--model", default=None, help="override director model id")
    return ap.parse_args()


def _register_media_roots(specs) -> None:
    """--media-root NAME=PATH, repeatable. Registering is persistent, so this
    is a convenience for the first run/scripted setup — the same roots are
    editable live from Settings > Media roots."""
    from lightsaber import media_roots
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep:
            print(f"[lightsaber] --media-root needs NAME=PATH, got: {spec!r}")
            continue
        ok, msg = media_roots.set_root(name.strip(), os.path.abspath(
            os.path.expanduser(path.strip())))
        print(f"[lightsaber] media root {name.strip()!r}: "
              + ("registered" if ok else f"rejected — {msg}"))


def _resolve_startup_project(projects) -> str:
    """Pick which project to open. First run (or a pre-project install):
    migrate the old single root canvases/+sequence.json into projects/default/
    if either has real content, else just create an empty default — copies,
    never moves, so a migration mistake is recoverable from the originals."""
    if not projects.names():
        legacy_canvas_dir = os.path.join(HERE, "canvases")
        legacy_sequence_path = os.path.join(HERE, "sequence.json")
        has_canvases = os.path.isdir(legacy_canvas_dir) and any(
            f.endswith(".json") for f in os.listdir(legacy_canvas_dir))
        has_sequence = os.path.exists(legacy_sequence_path)
        projects.create("default")
        if has_canvases or has_sequence:
            print("[lightsaber] migrating pre-project canvases/sequence into "
                  "projects/default/ (originals left in place)")
            if has_canvases:
                dst = projects.canvas_dir("default")
                for f in os.listdir(legacy_canvas_dir):
                    if f.endswith(".json"):
                        shutil.copy2(os.path.join(legacy_canvas_dir, f), os.path.join(dst, f))
            if has_sequence:
                shutil.copy2(legacy_sequence_path, projects.sequence_path("default"))
    name = settings.get("current_project")
    if not name or name not in projects.names():
        name = projects.names()[0]
    return name


def main():
    args = parse_args()
    _register_media_roots(args.media_root)
    engine = Engine(
        library_dir=os.path.join(HERE, "scenes"),
        cache_dir=os.path.join(HERE, "scenes", "generated"),
        projects_dir=os.path.join(HERE, "projects"),
        fps=args.fps, model=args.model,
        enable_diagnostics=args.diag,
    )
    engine.start()
    startup_project = _resolve_startup_project(engine.projects)
    engine.composite.set_project(startup_project)
    print(f"[lightsaber] engine running — project={startup_project} "
          f"director={'claude' if engine.director.online else 'local'}")
    if args.web:
        print(f"[lightsaber] open http://localhost:{args.web_port}")
        try:
            run_web(engine, os.path.join(HERE, "media"), host=args.host, port=args.web_port)
        finally:
            engine.stop()
    else:
        print("[lightsaber] running headless; Ctrl-C to stop")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            engine.stop()


if __name__ == "__main__":
    main()
