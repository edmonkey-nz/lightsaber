"""The output path: server tick rate, output-window paint pacing, the filtered
warp mesh, and the output-preview tiles.

Everything here is a behaviour that was measured wrong on the dev laptop in
2026-11 and fixed (see ARCHITECTURE.md §12's 2026-11 findings). Each failure
here would bring a measured cost straight back without breaking any picture,
which is why they need a check of their own: the render suite would never
notice.
"""

import os

from harness import FULL_QUAD, VIDEO_FIXTURE

# Deliberately NOT a parallelogram, so drawMediaWarped takes the 72-triangle
# mesh path rather than the single-drawImage fast path.
SKEW = [[-0.7, 0.6], [0.6, 0.7], [0.7, -0.6], [-0.6, -0.7]]

# Samples a horizontal line of editor pixels across the shape's middle and
# returns the blue channel's spread. A mesh with per-triangle blending shows
# as a grid: brighter bands along the triangle seams.
_ROW_JS = """([y, x0, x1, n]) => {
  paintCanvasEditor();
  const c = document.getElementById('cv-edit'), g = c.getContext('2d'), r = getEditorRect();
  const vals = [];
  for (let i = 0; i <= n; i++){
    const nx = x0 + (x1 - x0) * i / n;
    const [vx, vy] = normToPx(nx, y, r);
    const [dx, dy] = _viewToDevice(vx, vy, r);
    vals.push(g.getImageData(Math.round(dx), Math.round(dy), 1, 1).data[2]);
  }
  return {min: Math.min(...vals), max: Math.max(...vals),
          mean: Math.round(vals.reduce((a, b) => a + b, 0) / vals.length)};
}"""


async def run(s, rep):
    await _server_rate(s, rep)
    await _preview_tiles(s, rep)
    await _filtered_mesh(s, rep)
    await _paint_pacing(s, rep)
    await _gpu_indicator(s, rep)


async def _gpu_indicator(s, rep):
    """Output windows report their GPU (client_stats) and the editor header
    shows it — ported from promptwaver so it's visible which GPU a show is
    actually running on."""
    page = s.page
    out = await s.open_output(1)
    await page.wait_for_timeout(2500)          # at least one 2s report
    clients = await page.evaluate("_lastState.output_clients || []")
    mine = [c for c in clients if c.get("role") == "output 1"]
    rep.check_true("output/gpu/output-reports-gpu", bool(mine and mine[0].get("gpu")),
                   detail=str(mine[:1]),
                   hint="output.html's reportClientStats -> server.py's client_stats branch -> "
                        "Engine.state()'s output_clients")
    name = await page.evaluate("document.getElementById('gpu-name').textContent")
    rep.check_true("output/gpu/header-shows-name", bool(name) and name != "…",
                   detail=repr(name), hint="paintGpu should run from paintState")
    await out.close()
    await page.wait_for_timeout(1500)
    clients = await page.evaluate("_lastState.output_clients || []")
    rep.check_true("output/gpu/closed-window-forgotten",
                   not any(c.get("role") == "output 1" for c in clients),
                   detail=str(clients),
                   hint="ws_handler's finally must pop engine.client_stats for the socket")


async def _server_rate(s, rep):
    page = s.page
    await page.evaluate("send({type:'project_set_fps', value:30})")
    await s.start()
    await page.wait_for_timeout(1000)
    # Rate from the tick COUNTER, not perf_diag.fps: that is an average over
    # the last 300 intervals, which still holds the previous rate's ticks
    # for ~10s after a change.
    t0 = await page.evaluate("[_lastState.perf_diag.ticks, performance.now()]")
    await page.wait_for_timeout(3000)
    t1 = await page.evaluate("[_lastState.perf_diag.ticks, performance.now()]")
    d = await page.evaluate("_lastState.perf_diag")
    d["fps"] = round((t1[0] - t0[0]) * 1000 / (t1[1] - t0[1]), 1)
    rep.check("output/server/target-follows-project", d["target_fps"], 30,
              hint="LoopStats.set_fps must be called when the project's fps changes "
                   "(CompositeRenderer._loop); otherwise 'dropped' is judged against the "
                   "wrong budget")
    rep.check_true("output/server/tick-rate-follows-project", d["fps"] >= 27,
                   detail=f"{d['fps']} fps at a 30fps target",
                   hint="_loop must read self.fps every tick. It used to read it once before "
                        "the loop, and a 30fps project ran at the --fps default of 20.")


async def _preview_tiles(s, rep):
    page = s.page
    count = "document.querySelectorAll('#output-preview-grid iframe').length"
    await page.evaluate("setActiveTab('tab-canvas')")
    await page.wait_for_timeout(600)
    rep.check("output/preview/none-while-hidden", await page.evaluate(count), 0,
              hint="each tile is a full output renderer with its own video decoders; "
                   "renderOutputPreview must not build them while the tab is hidden")
    await page.evaluate("setActiveTab('tab-output-preview')")
    await page.wait_for_timeout(800)
    outputs = await page.evaluate("(_lastState.outputs || []).length || 2")
    rep.check("output/preview/built-when-shown", await page.evaluate(count), outputs,
              hint="setActiveTab should call refreshOutputPreview when this tab opens")
    await page.evaluate("setActiveTab('tab-canvas')")
    await page.wait_for_timeout(800)
    rep.check("output/preview/torn-down-when-left", await page.evaluate(count), 0,
              hint="leaving the tab must remove the iframes, or their decoders keep running")


async def _filtered_mesh(s, rep):
    """A colour filter on a warped mesh goes through the scratch buffer and is
    applied once. The value and the flatness are both checked: the value
    proves the filter still applies, the flatness that the seams don't show."""
    await s.reset_canvas()
    shot = await s.add_shape({"source_type": "media", "media": "blue.png", "z_index": 5}, FULL_QUAD)
    await s.page.evaluate("([id,c])=>send({type:'poly_corners',id,corners:c})", [shot, SKEW])
    await s.start()
    await s.set(shot, "brightness", 0.5)
    row = await s.page.evaluate(_ROW_JS, [0.0, -0.45, 0.45, 60])
    rep.check("output/mesh/filter-applies", row["mean"], 128, tolerance=10,
              hint="brightness 0.5 on pure blue should read ~128. If it reads 255 the filter "
                   "was dropped on the scratch path (drawMediaWarped's viaScratch branch).")
    rep.check_true("output/mesh/filtered-is-flat", row["max"] - row["min"] <= 6,
                   detail=f"blue {row['min']}..{row['max']}",
                   hint="a spread here is the triangle grid showing through")
    await s.set(shot, "opacity", 0.5)
    row = await s.page.evaluate(_ROW_JS, [0.0, -0.45, 0.45, 60])
    rep.check("output/mesh/filter-and-opacity", row["mean"], 64, tolerance=10,
              hint="brightness 0.5 at opacity 0.5 over black: ~64")
    rep.check_true("output/mesh/translucent-is-flat", row["max"] - row["min"] <= 6,
                   detail=f"blue {row['min']}..{row['max']}",
                   hint="the anti-seam skirts double-blending: see ARCHITECTURE.md's "
                        "per-primitive opacity note")
    out = await s.open_output(1)
    got = await out.evaluate("""() => {
      const g = cv.getContext('2d'), w = cv.width, h = cv.height;
      return g.getImageData(Math.round(w/2), Math.round(h/2), 1, 1).data[2]; }""")
    await out.close()
    rep.check("output/mesh/output-window-matches", got, 64, tolerance=10,
              hint="output.html's drawMediaWarped is a hand-synced copy; if the editor passes "
                   "and this fails, the change landed in index.html only")


async def _paint_pacing(s, rep):
    """The output window repaints live content on its own clock. To prove it,
    the window is made to see only one server message in four: video should
    still paint at the project rate, and a still image only when a message
    arrives."""
    root = getattr(s.server, "root", None)
    if not root or not os.path.exists(os.path.join(root, "media", VIDEO_FIXTURE)):
        rep.note("output/pacing", "SKIPPED", "no ffmpeg, so no video fixture")
        return
    starve = """(secs) => new Promise(res => {
      let msgs = 0, kept = 0, paints = 0;
      const om = ws.onmessage, op = window.paintComposite;
      ws.onmessage = e => { if (msgs++ % 4 === 0){ kept++; om(e); } };
      window.paintComposite = function(){ paints++; return op.apply(this, arguments); };
      setTimeout(() => { ws.onmessage = om; window.paintComposite = op;
        res({kept: kept / secs, paints: paints / secs}); }, secs * 1000);
    })"""
    await s.reset_canvas()
    shot = await s.add_shape({"source_type": "media", "media": VIDEO_FIXTURE, "z_index": 5}, FULL_QUAD)
    await s.start()
    out = await s.open_output(1)
    ready = await out.evaluate("[..._mediaEls.values()].some(m => m.kind === 'video' && m.ready)")
    if not ready:
        await out.close()
        rep.check_true("output/pacing/video-loads", False,
                       hint="the WebM fixture never became ready in the output window")
        return
    r = await out.evaluate(starve, 4)
    rep.check_true("output/pacing/video-paints-at-project-rate", r["paints"] >= 24,
                   detail=f"{r['paints']:.1f} paints/s from {r['kept']:.1f} messages/s",
                   hint="with video on screen the window must repaint the newest state every "
                        "period (drawPending / _wantsContinuousPaint), not only on a message")
    await s.set(shot, "media", "blue.png")
    await out.wait_for_timeout(1500)
    r = await out.evaluate(starve, 3)
    rep.check_true("output/pacing/still-image-paints-on-message", r["paints"] <= r["kept"] + 2,
                   detail=f"{r['paints']:.1f} paints/s from {r['kept']:.1f} messages/s",
                   hint="static content has nothing new to show between messages; repainting "
                        "it every period is wasted GPU work")
    await out.close()
