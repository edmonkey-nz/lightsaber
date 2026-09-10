"""Pixel checks for every compositing feature that has a render path.

Each check samples a real pixel off the editor canvas and compares it to a
value worked out by hand, so a failure says which stage broke rather than
"something looks different".
"""

from harness import FULL_QUAD, nearest_name

HOLE = [[-0.4, 0.4], [0.4, 0.4], [0.4, -0.4], [-0.4, -0.4]]
BOX = [[-0.5, 0.5], [0.5, 0.5], [0.5, -0.5], [-0.5, -0.5]]
WIDE = [[-0.6, 0.6], [0.6, 0.6], [0.6, -0.6], [-0.6, -0.6]]


async def run(s, rep):
    await _punch(s, rep)
    await _key(s, rep)
    await _fx(s, rep)
    await _feather(s, rep)
    await _kaleidoscope(s, rep)
    await _output_window(s, rep)


async def _punch(s, rep):
    """A punch-through cutout erases the layers in its band; the ones deeper
    than the band show through the hole."""
    await s.reset_canvas()
    await s.add_shape({"source_type": "media", "media": "red.png", "z_index": 8}, FULL_QUAD)
    await s.add_shape({"source_type": "media", "media": "green.png", "z_index": 6}, FULL_QUAD)
    await s.add_shape({"source_type": "media", "media": "blue.png", "z_index": 4}, FULL_QUAD)
    cut = await s.add_shape({"source_type": "knockout", "z_index": 3}, HOLE)
    await s.start()
    rep.check("render/cutout/blackout", nearest_name(await s.rgb(0)), "black",
              hint="a plain cutout paints opaque black; see drawKnockoutFill in both renderers")
    await s.set(cut, "knockout_punch", True)
    for depth, want in ((1, "green"), (3, "red"), (5, "black")):
        await s.set(cut, "knockout_depth", depth)
        rep.check(f"render/cutout/punch-depth-{depth}", nearest_name(await s.rgb(0)), want,
                  hint="depth counts z-LEVELS behind the cutout: at z3 depth 3 erases z4-z6. "
                       "See punchBand/surfaceFor in both renderers.")


async def _key(s, rep):
    await s.reset_canvas()
    await s.add_shape({"source_type": "media", "media": "blue.png", "z_index": 9}, FULL_QUAD)
    shot = await s.add_shape({"source_type": "media", "media": "greenred.png", "z_index": 4}, FULL_QUAD)
    await s.start()
    rep.check("render/key/off", nearest_name(await s.rgb(-0.5)), "chroma-green",
              hint="with the key off the shape should be untouched")
    await s.set(shot, "key_mode", "colour")
    rep.check("render/key/chroma-removes-key-colour", nearest_name(await s.rgb(-0.5)), "blue",
              hint="the key colour should drop out and reveal the backdrop. If the whole shape "
                   "went black, the matte branch is being computed on the spill-suppressed "
                   "colour instead of SourceGraphic (see configureKeyFilter).")
    rep.check("render/key/chroma-keeps-foreground", nearest_name(await s.rgb(0.5)), "red",
              hint="non-key colours must survive with their colour intact. Black here means the "
                   "colour branch had its alpha driven to 0 — the pipeline is premultiplied.")
    await s.set(shot, "key_mode", "luma")
    await s.set(shot, "key_tolerance", 0.3)
    rep.check("render/key/luma-drops-dark", nearest_name(await s.rgb(0.5)), "blue",
              hint="luma keys everything BELOW the threshold; pure red is ~0.21 luminance")
    await s.set(shot, "key_mode", "off")
    await s.set(shot, "key_tolerance", 0.65)


async def _fx(s, rep):
    """Exact values, all hand-derived from the source colour (0,177,64)."""
    await s.reset_canvas()
    shot = await s.add_shape({"source_type": "media", "media": "greenred.png", "z_index": 4}, FULL_QUAD)
    await s.start()
    cases = [
        ("fx_invert", 1.0, [255, 78, 191], "invert is 255-x per channel"),
        ("fx_posterize", 2, [0, 255, 0], "2 levels snaps each channel to 0 or 255"),
        ("fx_solarize", 1.0, [0, 156, 128], "a triangle curve 1-|2t-1| per channel"),
        ("fx_duotone", 1.0, [135, 120, 75],
         "luminance 0.514 mapped onto #0a1a2f -> #ffd166"),
    ]
    for key, value, want, why in cases:
        await s.set(shot, key, value)
        rep.check(f"render/fx/{key[3:]}", await s.rgb(-0.5), want, tolerance=3,
                  hint=f"{why}. If nothing changed at all, check the feFunc uses tableValues "
                       f"(NOT values, which is feColorMatrix's attribute).")
        await s.set(shot, key, 0)
    # pixelate is a rescale, not a filter — it must quantise a smooth ramp
    await s.reset_canvas()
    grad = await s.add_shape({"source_type": "media", "media": "grad.png", "z_index": 4}, FULL_QUAD)
    await s.start()
    runs = await s.page.evaluate("""() => {
      paintCanvasEditor();
      const c=document.getElementById('cv-edit'), g=c.getContext('2d'), r=getEditorRect();
      const [vx,vy]=normToPx(0,0,r); const [dx,dy]=_viewToDevice(vx,vy,r);
      const v=[]; for(let i=0;i<24;i++) v.push(g.getImageData(Math.round(dx)+i,Math.round(dy),1,1).data[0]);
      return new Set(v).size; }""")
    await s.set(grad, "fx_pixelate", 48)
    blocky = await s.page.evaluate("""() => {
      paintCanvasEditor();
      const c=document.getElementById('cv-edit'), g=c.getContext('2d'), r=getEditorRect();
      const [vx,vy]=normToPx(0,0,r); const [dx,dy]=_viewToDevice(vx,vy,r);
      const v=[]; for(let i=0;i<24;i++) v.push(g.getImageData(Math.round(dx)+i,Math.round(dy),1,1).data[0]);
      return new Set(v).size; }""")
    rep.check_true("render/fx/pixelate-quantises", blocky < runs,
                   detail=f"{runs} distinct values -> {blocky}",
                   hint="pixelate is measured in PROJECT-CANVAS px, so it looks smaller in the "
                        "editor preview than on the output. See fxPixelDevicePx.")
    await s.set(grad, "fx_pixelate", 0)


async def _feather(s, rep):
    """The ramp must widen monotonically with the setting."""
    await s.reset_canvas()
    await s.add_shape({"source_type": "media", "media": "blue.png", "z_index": 9}, FULL_QUAD)
    masked = await s.add_shape(
        {"source_type": "media", "media": "green.png", "z_index": 6, "clip_shape": "square"}, BOX)
    await s.start()
    widths = []
    for value in (0, 4, 12, 20):
        await s.set(masked, "feather", value)
        widths.append(await s.ramp_width(0.5, channel=1))
    rising = all(widths[i] <= widths[i + 1] for i in range(len(widths) - 1)) and widths[-1] > widths[0]
    rep.check_true("render/feather/ramp-widens", rising, detail=f"px at 0/4/12/20: {widths}",
                   hint="feather is in project-canvas px, converted per surface. A flat list "
                        "means featherDevicePx returned 0 — check _projectRes and the blur string.")


async def _kaleidoscope(s, rep):
    """Even counts mirror (and so close seamlessly); odd counts rotate."""
    await s.reset_canvas()
    await s.add_shape({"source_type": "media", "media": "red.png", "z_index": 9}, FULL_QUAD)
    shot = await s.add_shape({"source_type": "media", "media": "vgrad.png", "z_index": 4}, WIDE)
    await s.start()

    async def at_angle(deg, radius=0.5):
        return await s.page.evaluate("""([deg, rad]) => {
          paintCanvasEditor();
          const c=document.getElementById('cv-edit'), g=c.getContext('2d'), r=getEditorRect();
          const [cx,cy]=_viewToDevice(...normToPx(0,0,r), r);
          const [ex]=_viewToDevice(...normToPx(0.6,0,r), r);
          const R=(ex-cx)*rad, a=deg*Math.PI/180;
          return g.getImageData(Math.round(cx+R*Math.cos(a)), Math.round(cy+R*Math.sin(a)),1,1).data[0];
        }""", [deg, radius])

    top, bottom = await at_angle(-90), await at_angle(90)
    rep.check_true("render/kaleido/source-is-asymmetric", abs(top - bottom) > 40,
                   detail=f"top {top} vs bottom {bottom}",
                   hint="the vgrad fixture must ramp top-to-bottom or the symmetry checks below "
                        "would pass trivially")
    await s.set(shot, "mirror_kaleido", 2)
    top, bottom = await at_angle(-90), await at_angle(90)
    rep.check_true("render/kaleido/even-mirrors", abs(top - bottom) <= 3,
                   detail=f"top {top} vs bottom {bottom}",
                   hint="2 segments should mirror top/bottom about the shape centre")
    await s.set(shot, "mirror_kaleido", 3)
    vals = [await at_angle(d) for d in (30, 150, 270)]
    rep.check_true("render/kaleido/odd-rotates", max(vals) - min(vals) <= 5, detail=str(vals),
                   hint="an odd count cannot close a mirrored ring, so it repeats by rotation; "
                        "three points 120 deg apart must match")
    await s.set(shot, "mirror_kaleido", 6)
    # even wedges show source(t), odd ones source(step - t): these six angles
    # all resolve to source(15 deg)
    vals = [await at_angle(d) for d in (15, 105, 135, 225, 255, 345)]
    rep.check_true("render/kaleido/even-sixfold", max(vals) - min(vals) <= 6, detail=str(vals),
                   hint="mirror-pair angles must agree; sampling six EVENLY spaced angles would "
                        "correctly alternate instead, so do not 'fix' this by respacing them")
    adjacent = [await at_angle(15), await at_angle(75)]
    rep.check_true("render/kaleido/is-mirrored-not-rotated", abs(adjacent[0] - adjacent[1]) > 20,
                   detail=str(adjacent),
                   hint="adjacent wedges must differ, or the even path is rotating rather than "
                        "alternating a reflection")
    outside = await s.rgb(0.9, 0.9)
    rep.check("render/kaleido/stays-inside-quad", nearest_name(outside), "red",
              hint="a rotated wedge must be clipped to the shape's own quad, or it paints into "
                   "bbox corners the shape does not cover")
    await s.set(shot, "mirror_kaleido", 0)


async def _output_window(s, rep):
    """The projector window is a hand-synced copy of the editor's draw path, so
    at least one feature is checked there too — a change made in only one of
    the two files is the classic failure here."""
    await s.reset_canvas()
    await s.add_shape({"source_type": "media", "media": "blue.png", "z_index": 9}, FULL_QUAD)
    shot = await s.add_shape({"source_type": "media", "media": "greenred.png", "z_index": 4}, FULL_QUAD)
    await s.set(shot, "key_mode", "colour")
    await s.start()
    out = await s.open_output(0)
    got = await out.evaluate("""() => {
      const g = cv.getContext('2d'), w = cv.width, h = cv.height;
      const sc = Math.min(w/1920, h/1080), halfW = 1920*sc/2;
      const d = g.getImageData(Math.round(w/2 - 0.5*halfW), Math.round(h/2), 1, 1).data;
      return [d[0], d[1], d[2]]; }""")
    await out.close()
    rep.check("render/output-window/key-applies", nearest_name(got), "blue",
              hint="output.html duplicates the editor's draw functions by hand. If this fails "
                   "while render/key passes, the change landed in index.html only.")
