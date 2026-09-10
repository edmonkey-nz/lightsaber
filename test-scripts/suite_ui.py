"""Control-surface checks: does each pane exist, show the right rows for the
selected shape, and does moving a control actually reach the server.

The last part matters more than it sounds — a control can look right and be
wired to a key the server drops, which renders as "the slider does nothing".
"""

from harness import FULL_QUAD

EXPECTED_PANES = ["shapesettings", "playback", "mask", "colourize",
                  "transform", "key", "fx", "effectors", "settings"]


async def run(s, rep):
    page = s.page
    await s.reset_canvas()
    shot = await s.add_shape({"source_type": "media", "media": "greenred.png"}, FULL_QUAD)
    await page.evaluate("([id])=>{selectedPolyId=id; renderPolyInspector();}", [shot])
    await s.start()

    order = await page.evaluate(
        "[...document.querySelectorAll('.drawer-handle-btn')].map(b=>b.dataset.drawer)")
    rep.check("ui/panes/rail-order", order, EXPECTED_PANES,
              hint="every pane needs a handle button AND an entry in DRAWER_NAMES, or "
                   "setDrawerOpen throws on a missing element")
    for name in EXPECTED_PANES:
        ok = await page.evaluate(f"!!document.getElementById('content-{name}')")
        if not ok:
            rep.check_true(f"ui/panes/content-{name}", False,
                           hint=f"#content-{name} is missing but #handle-{name} exists")
    await _key_pane(s, rep, shot)
    await _fx_pane(s, rep, shot)
    await _transform_pane(s, rep, shot)
    await _routes(s, rep)


async def _key_pane(s, rep, shot):
    page = s.page
    await page.click("#handle-key")
    await page.wait_for_timeout(600)
    rep.check_true("ui/key/shown-for-raster", await page.is_visible("#poly-key-block"),
                   hint="a media shape should offer keying")
    rep.check_true("ui/key/colour-row-hidden-when-off",
                   not await page.is_visible("#poly-key-colour-field"))
    await page.select_option("#poly-key-mode", "colour")
    await page.wait_for_timeout(600)
    for row in ("colour", "tol", "soft", "spill", "feather", "matte"):
        rep.check_true(f"ui/key/row-{row}", await page.is_visible(f"#poly-key-{row}-field"))
    await page.select_option("#poly-key-mode", "luma")
    await page.wait_for_timeout(600)
    rep.check_true("ui/key/luma-hides-colour-and-spill",
                   not await page.is_visible("#poly-key-colour-field")
                   and not await page.is_visible("#poly-key-spill-field"),
                   hint="a luma key has no hue to pick or suppress")
    await page.select_option("#poly-key-mode", "colour")
    await page.wait_for_timeout(500)
    # the eyedropper must suspend the key while picking, or it samples the hole
    await page.click("#poly-key-pick")
    await page.wait_for_timeout(600)
    rep.check_true("ui/key/eyedropper-arms",
                   await page.evaluate("_keyPickFor === selectedPolyId"))
    box = await page.evaluate("""() => { const r=getEditorRect();
      const [vx,vy]=normToPx(-0.5,0,r); const [dx,dy]=_viewToDevice(vx,vy,r);
      const c=document.getElementById('cv-edit'), b=c.getBoundingClientRect();
      return {x: b.left + dx*b.width/c.width, y: b.top + dy*b.height/c.height}; }""")
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(800)
    picked = await page.evaluate("polyById(selectedPolyId).key_color")
    rep.check("ui/key/eyedropper-samples-untreated", picked.lower(), "#00b140",
              hint="the key is suspended for the picked shape so you sample raw footage; if this "
                   "returns the backdrop colour the suspension in drawContentPoly is not firing")
    await s.set(shot, "key_mode", "off")


async def _fx_pane(s, rep, shot):
    page = s.page
    await page.click("#handle-fx")
    await page.wait_for_timeout(600)
    rep.check_true("ui/fx/shown", await page.is_visible("#poly-fx-block"))
    for slider, span, field, value, label in [
            ("poly-fx-pixelate", "v-polyfxpixelate", "fx_pixelate", "12", "12 px"),
            ("poly-fx-posterize", "v-polyfxposterize", "fx_posterize", "5", "5 levels"),
            ("poly-fx-duotone", "v-polyfxduotone", "fx_duotone", "0.6", "0.60")]:
        await page.fill(f"#{slider}", value)
        await page.dispatch_event(f"#{slider}", "input")
        await page.wait_for_timeout(450)
        rep.check(f"ui/fx/{field}-label", await page.inner_text(f"#{span}"), label)
        rep.check(f"ui/fx/{field}-reaches-server",
                  await page.evaluate(f"(_lastComposite.find(l=>l.id===selectedPolyId)||{{}}).{field}"),
                  float(value) if "." in value else int(value),
                  hint=f"add {field} to composite.py's set_polygon AND the layer payload")
    await page.fill("#poly-fx-posterize", "1")
    await page.dispatch_event("#poly-fx-posterize", "input")
    await page.wait_for_timeout(450)
    rep.check("ui/fx/posterize-1-snaps-off", await page.inner_text("#v-polyfxposterize"), "off",
              hint="one level is not a posterize; the first step must read as off")
    await page.click("#poly-fx-reset")
    await page.wait_for_timeout(700)
    zeroed = await page.evaluate("""()=>{const p=polyById(selectedPolyId);
      return ['fx_pixelate','fx_posterize','fx_solarize','fx_invert','fx_duotone'].map(k=>p[k]);}""")
    rep.check("ui/fx/reset", zeroed, [0, 0, 0, 0, 0])


async def _transform_pane(s, rep, shot):
    page = s.page
    await page.click("#handle-transform")
    await page.wait_for_timeout(600)
    rep.check_true("ui/transform/kaleido-below-mirrors", await page.evaluate(
        "!!(document.getElementById('mf-mirror-predistort').compareDocumentPosition("
        "document.getElementById('mf-kaleido')) & Node.DOCUMENT_POSITION_FOLLOWING)"),
        hint="the kaleidoscope belongs under the mirror folds it extends")
    for value, label, stored in (("1", "off", 0), ("3", "3 rot", 3), ("6", "6 mir", 6)):
        await page.fill("#mf-kaleido", value)
        await page.dispatch_event("#mf-kaleido", "input")
        await page.wait_for_timeout(450)
        rep.check(f"ui/transform/kaleido-{value}-label", await page.inner_text("#v-mfkaleido"), label,
                  hint="the label says mir/rot because an odd count cannot mirror seamlessly")
        rep.check(f"ui/transform/kaleido-{value}-server",
                  await page.evaluate(
                      "(_lastComposite.find(l=>l.id===selectedPolyId)||{}).mirror_kaleido"),
                  stored)
    await page.fill("#mf-kaleido", "0")
    await page.dispatch_event("#mf-kaleido", "input")
    await page.wait_for_timeout(400)


async def _routes(s, rep):
    """A new effector route must arrive pointed at something — empty selects
    read as broken rather than as 'pick one'."""
    page = s.page
    await page.click("#handle-effectors")
    await page.wait_for_timeout(500)
    await page.evaluate("""()=>{const fx=(_lastState.canvas&&_lastState.canvas.effectors)||{sources:[],routes:[]};
      fx.routes.forEach(r=>send({type:'effector_route_remove',id:r.id}));
      fx.sources.forEach(x=>send({type:'effector_source_remove',id:x.id}));}""")
    await page.wait_for_timeout(1000)
    await page.evaluate("send({type:'effector_source_add'})")
    await page.wait_for_timeout(1000)
    await page.evaluate("send({type:'effector_route_add'})")
    await page.wait_for_timeout(1200)
    got = await page.evaluate("""()=>{const fx=_lastState.canvas.effectors;
      const r=fx.routes[fx.routes.length-1];
      return {src: !!r.source_id, tgt: !!r.target_poly}; }""")
    rep.check_true("ui/effectors/new-route-has-source", got["src"],
                   hint="effector_route_add should prefer a source the user built over the audio "
                        "bands, which do nothing until audio input is running")
    rep.check_true("ui/effectors/new-route-has-target", got["tgt"],
                   hint="and land on the first shape on the canvas")
    overflow = await page.evaluate("""() => {
      const card = document.querySelector('.fx-card');
      if (!card) return null;
      const p = card.parentElement;
      return Math.round(card.getBoundingClientRect().right - p.getBoundingClientRect().right); }""")
    if overflow is not None:
        rep.check_true("ui/effectors/route-card-fits", overflow <= 0, detail=f"{overflow}px past parent",
                       hint="flex children default to min-width:auto, so the three selects refuse "
                            "to shrink and the row pushes past the panel edge")
