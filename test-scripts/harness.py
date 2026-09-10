"""Shared plumbing for the render regression suites.

Everything here exists to make a suite a list of assertions rather than a pile
of setup. Three things it takes care of, each of which has cost real debugging
time when done by hand (see CLAUDE.md's "Verifying a change"):

* **Isolation.** `run.py` resolves `scenes/`, `projects/`, `media/` and
  `settings.json` relative to the repo root, so a test server started in the
  repo would read and write the user's real projects. `build_sandbox` copies
  the package into a temp dir with empty data folders instead, on a free port.
* **A clean canvas.** The server is long-lived and holds canvas state in
  memory, so shapes accumulate between runs and silently corrupt pixel
  measurements. `Session.reset_canvas` clears them and verifies it worked.
* **A real GPU.** Headless Chromium is SwiftShader even with the GPU flags, so
  any timing taken headless is software and off by roughly ten times. The
  browser runs headed by default and the renderer is reported, so a number can
  never be quoted without knowing which it was.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Solid fixtures with known values, so a check can assert an exact colour
# rather than "looks about right".
CHROMA_GREEN = (0, 177, 64)      # what the keyer defaults to
FIXTURES = {
    "blue.png": lambda x, y: (0, 0, 255),
    "green.png": lambda x, y: (0, 255, 0),
    "red.png": lambda x, y: (255, 0, 0),
    # left half chroma-green, right half red: a synthetic green-screen shot
    "greenred.png": lambda x, y: CHROMA_GREEN if x < 32 else (255, 0, 0),
    # horizontal ramp — pixelate must visibly quantise this
    "grad.png": lambda x, y: ((x * 255) // 63,) * 3,
    # vertical ramp — a kaleidoscope's symmetry is only provable against a
    # source that is asymmetric to begin with
    "vgrad.png": lambda x, y: ((y * 255) // 63,) * 3,
}


def _png(path, fn, size=64):
    rows = b"".join(b"\x00" + b"".join(bytes(fn(x, y)) for x in range(size))
                    for y in range(size))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows))
                + chunk(b"IEND", b""))


def build_sandbox(dest: str | None = None) -> str:
    """A throwaway copy of the app with empty data folders and known fixtures."""
    dest = dest or tempfile.mkdtemp(prefix="lightsaber-test-")
    for sub in ("projects", "scenes", "media"):
        os.makedirs(os.path.join(dest, sub), exist_ok=True)
    shutil.copytree(os.path.join(REPO, "lightsaber"), os.path.join(dest, "lightsaber"),
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("run.py", "about.md"):
        shutil.copy2(os.path.join(REPO, f), os.path.join(dest, f))
    with open(os.path.join(dest, "settings.json"), "w") as f:
        f.write("{}")
    for name, fn in FIXTURES.items():
        _png(os.path.join(dest, "media", name), fn)
    return dest


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The sandbox app, on its own port, torn down on exit."""

    def __init__(self, root: str, port: int | None = None):
        self.root, self.port = root, port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "run.py", "--web", "--web-port", str(self.port),
             "--host", "127.0.0.1"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("server exited during startup:\n" + (self.proc.stdout.read() or ""))
            try:
                urllib.request.urlopen(self.url, timeout=1).read(1)
                return self
            except Exception:
                time.sleep(0.4)
        raise RuntimeError(f"server did not answer on {self.url} within 30s")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)


class Report:
    """Collects results in a shape both a person and an agent can act on.

    A bare FAIL is not actionable, so every check carries what was expected,
    what happened, and a hint naming where to look. `--json` emits the same
    records for a tool to read.
    """

    def __init__(self, quiet: bool = False):
        self.records: list[dict] = []
        self.quiet = quiet

    def check(self, name, actual, expected, hint="", tolerance=None):
        if tolerance is not None and isinstance(actual, (int, float)):
            ok = abs(actual - expected) <= tolerance
        elif tolerance is not None and isinstance(actual, (list, tuple)):
            ok = len(actual) == len(expected) and all(
                abs(a - b) <= tolerance for a, b in zip(actual, expected))
        else:
            ok = actual == expected
        self._add(name, ok, actual, expected, hint)
        return ok

    def check_true(self, name, ok, detail="", hint=""):
        self._add(name, bool(ok), detail or ("true" if ok else "false"), "true", hint)
        return bool(ok)

    def note(self, name, value, detail=""):
        """A measurement with no pass/fail — perf numbers, the renderer name."""
        self.records.append({"name": name, "status": "note", "actual": value, "detail": detail})
        if not self.quiet:
            print(f"  ---- {name:44s} {value}{('  ' + detail) if detail else ''}")

    def _add(self, name, ok, actual, expected, hint):
        rec = {"name": name, "status": "pass" if ok else "fail",
               "actual": actual, "expected": expected, "hint": hint}
        self.records.append(rec)
        if not self.quiet:
            print(f"  {'PASS' if ok else 'FAIL'} {name:44s} {actual}")
            if not ok:
                print(f"       expected: {expected}")
                if hint:
                    print(f"       hint:     {hint}")

    @property
    def failures(self):
        return [r for r in self.records if r["status"] == "fail"]

    def as_json(self):
        return json.dumps({"records": self.records,
                           "failed": len(self.failures),
                           "checked": sum(1 for r in self.records if r["status"] != "note")}, indent=2)


# --- browser session -------------------------------------------------------
# playwright is imported lazily: it is a test-only dependency and deliberately
# not in requirements.txt, so the helpers above stay usable without it.

FULL_QUAD = [[-1, 1], [1, 1], [1, -1], [-1, -1]]

# Reads a device pixel off the editor canvas at a point given in CANVAS
# coordinates ([-1,1], +y up), pushing it through the same normToPx +
# _viewToDevice the editor's own chrome uses.
_PIXEL_JS = """([nx, ny]) => {
  paintCanvasEditor();
  const c = document.getElementById('cv-edit'), g = c.getContext('2d');
  const r = getEditorRect();
  const [vx, vy] = normToPx(nx, ny, r);
  const [dx, dy] = _viewToDevice(vx, vy, r);
  const d = g.getImageData(Math.round(dx), Math.round(dy), 1, 1).data;
  return [d[0], d[1], d[2], d[3]];
}"""

# A 1px getImageData stalls until the GPU has finished. Without it this times
# command SUBMISSION rather than execution and every filter looks free.
_BENCH_JS = """(n) => {
  const g = document.getElementById('cv-edit').getContext('2d');
  const t = [];
  for (let i = 0; i < n; i++){
    const a = performance.now();
    paintCanvasEditor();
    g.getImageData(0, 0, 1, 1);
    t.push(performance.now() - a);
  }
  t.sort((x, y) => x - y);
  return {median: +t[n >> 1].toFixed(2), p90: +t[Math.floor(n * 0.9)].toFixed(2)};
}"""

_RENDERER_JS = """() => {
  const c = document.createElement('canvas');
  const g = c.getContext('webgl');
  if (!g) return 'no webgl';
  const d = g.getExtension('WEBGL_debug_renderer_info');
  const r = d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER);
  return /SwiftShader|llvmpipe/i.test(r) ? 'SOFTWARE (' + r.slice(0, 30) + ')' : r;
}"""


class Session:
    """One browser against one sandbox server, plus the canvas verbs a suite needs."""

    def __init__(self, url, headless=False):
        self.url, self.headless = url, headless
        self.errors: list[str] = []

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless)
        self.ctx = await self.browser.new_context(viewport={"width": 1600, "height": 1000})
        self.page = await self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console",
                     lambda m: self.errors.append(f"console.error: {m.text}")
                     if m.type == "error" else None)
        await self.page.goto(self.url)
        await self.page.wait_for_timeout(2500)
        return self

    async def __aexit__(self, *exc):
        await self.browser.close()
        await self._pw.stop()

    async def renderer(self):
        return await self.page.evaluate(_RENDERER_JS)

    async def reset_canvas(self):
        """Delete every shape and confirm it took. The server keeps canvas state
        in memory for its whole life, so leftovers from an earlier suite would
        sit under the sample points and quietly change the answer."""
        await self.page.evaluate(
            "()=>(_canvasDoc.polygons||[]).forEach(p=>send({type:'poly_delete',id:p.id}))")
        for _ in range(40):
            await self.page.wait_for_timeout(150)
            if await self.page.evaluate("(_canvasDoc.polygons||[]).length") == 0:
                return
        raise RuntimeError("canvas would not clear — is the server wedged?")

    async def add_shape(self, props=None, corners=None):
        page = self.page
        before = await page.evaluate("(_canvasDoc.polygons||[]).map(p=>p.id)")
        await page.evaluate("send({type:'poly_add'})")
        new = []
        for _ in range(60):
            await page.wait_for_timeout(100)
            ids = await page.evaluate("(_canvasDoc.polygons||[]).map(p=>p.id)")
            new = [i for i in ids if i not in before]
            if new:
                break
        if not new:
            raise RuntimeError("poly_add never landed")
        pid = new[0]
        for k, v in (props or {}).items():
            await self.set(pid, k, v, settle=0)
        await page.wait_for_timeout(200)
        if corners:
            await page.evaluate("([id,c])=>send({type:'poly_corners',id,corners:c})", [pid, corners])
            await page.wait_for_timeout(250)
        return pid

    async def set(self, pid, key, value, settle=450):
        await self.page.evaluate("([id,k,v])=>send({type:'poly_set',id,key:k,value:v})",
                                 [pid, key, value])
        if settle:
            await self.page.wait_for_timeout(settle)

    async def start(self):
        await self.page.evaluate("send({type:'set_active', value:true})")
        await self.page.wait_for_timeout(1000)

    async def pixel(self, nx, ny=0):
        await self.page.wait_for_timeout(120)
        return await self.page.evaluate(_PIXEL_JS, [nx, ny])

    async def rgb(self, nx, ny=0):
        return (await self.pixel(nx, ny))[:3]

    async def ramp_width(self, nx, channel=1, span=16):
        """Width in px of the transition either side of a vertical edge at `nx`.
        Direction-agnostic: a blackout edge runs dark->bright where a punch edge
        runs bright->dark, so this counts samples between the two plateaus."""
        return await self.page.evaluate("""([nx, ch, span]) => {
          paintCanvasEditor();
          const c=document.getElementById('cv-edit'), g=c.getContext('2d'), r=getEditorRect();
          const [vx,vy]=normToPx(nx,0,r); const [dx,dy]=_viewToDevice(vx,vy,r);
          const v=[];
          for(let i=-span;i<=span;i++)
            v.push(g.getImageData(Math.round(dx)+i, Math.round(dy),1,1).data[ch]);
          const lo=Math.min(...v), hi=Math.max(...v), sp=hi-lo;
          if (sp < 20) return -1;
          return v.filter(x => x > lo+0.1*sp && x < lo+0.9*sp).length;
        }""", [nx, channel, span])

    async def bench(self, iterations=30):
        return await self.page.evaluate(_BENCH_JS, iterations)

    async def open_output(self, index=0):
        page = await self.ctx.new_page()
        page.on("pageerror", lambda e: self.errors.append(f"output pageerror: {e}"))
        await page.goto(f"{self.url}/output?out={index}")
        await page.wait_for_timeout(3000)
        return page


def nearest_name(rgb, palette=None):
    """Names a sampled colour, so a failure reads 'green, wanted blue' rather
    than two triples the reader has to decode."""
    palette = palette or {
        (0, 0, 255): "blue", (0, 255, 0): "green", (255, 0, 0): "red",
        (0, 0, 0): "black", (255, 255, 255): "white", CHROMA_GREEN: "chroma-green",
    }
    best = min(palette, key=lambda k: sum((a - b) ** 2 for a, b in zip(k, rgb)))
    return palette[best] if sum((a - b) ** 2 for a, b in zip(best, rgb)) < 3000 else f"rgb{tuple(rgb)}"
