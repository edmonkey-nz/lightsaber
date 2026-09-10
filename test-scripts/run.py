#!/usr/bin/env python3
"""Render regression runner for lightsaber.

    python test-scripts/run.py                 # everything, headed (real GPU)
    python test-scripts/run.py --suite render  # one suite
    python test-scripts/run.py --json out.json # machine-readable results
    python test-scripts/run.py --headless      # CI-ish; perf comparisons skipped

Builds its own throwaway copy of the app on a free port, so it never touches
the projects or media you are actually working on. Exits non-zero if any check
fails or the page logs a JS error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Report, Server, Session, build_sandbox   # noqa: E402

SUITES = ("render", "ui", "perf")


async def main_async(args):
    import importlib
    rep = Report()
    root = build_sandbox()
    print(f"sandbox: {root}")
    started = time.time()
    try:
        with Server(root) as server:
            print(f"server:  {server.url}\n")
            async with Session(server.url, headless=args.headless) as s:
                if args.headless:
                    print("!! headless: the GPU is SwiftShader here, so perf numbers are\n"
                          "!! software-rendered and comparisons are skipped.\n")
                for name in args.suite:
                    print(f"[{name}]")
                    mod = importlib.import_module(f"suite_{name}")
                    try:
                        await mod.run(s, rep)
                    except Exception as exc:      # a crashed suite must not hide the rest
                        rep.check_true(f"{name}/suite-completed", False, detail=repr(exc),
                                       hint="the suite raised before finishing; earlier checks "
                                            "in this list are still valid")
                    print()
                for err in s.errors:
                    rep.check_true("page/no-js-errors", False, detail=err,
                                   hint="a JS error kills the rest of the script in that page, so "
                                        "later checks may fail for this reason alone")
    finally:
        if args.keep:
            print(f"kept sandbox at {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)

    checked = sum(1 for r in rep.records if r["status"] != "note")
    failed = len(rep.failures)
    print("=" * 62)
    print(f"{checked - failed}/{checked} checks passed in {time.time() - started:.0f}s")
    if failed:
        print("\nFAILED:")
        for r in rep.failures:
            print(f"  - {r['name']}: got {r['actual']}, expected {r['expected']}")
            if r.get("hint"):
                print(f"    {r['hint']}")
    if args.json:
        with open(args.json, "w") as f:
            f.write(rep.as_json())
        print(f"\njson written to {args.json}")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", action="append", choices=SUITES,
                    help="run only this suite (repeatable); default is all")
    ap.add_argument("--headless", action="store_true",
                    help="no visible window. Perf comparisons are skipped: headless is software.")
    ap.add_argument("--json", metavar="PATH", help="also write results as JSON")
    ap.add_argument("--keep", action="store_true", help="leave the sandbox behind for inspection")
    args = ap.parse_args()
    args.suite = args.suite or list(SUITES)
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("playwright is needed and is deliberately not in requirements.txt:\n"
              "  .venv/bin/python -m pip install playwright\n"
              "  .venv/bin/python -m playwright install chromium", file=sys.stderr)
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
