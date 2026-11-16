"""Does the control surface survive the server going away?

Every Python change needs a server restart, so this is the most-exercised
failure path in the whole project. The editor used to hold one WebSocket for
the life of the page: after a restart `send` quietly became a no-op and nothing
repainted, which reads as "the app broke" — and specifically as "the output
windows have stopped", since they are the visible end of a control surface that
has gone silent.

Runs last, because it takes the server down and back up underneath the others.
"""


async def run(s, rep):
    server = getattr(s, "server", None)
    if server is None:
        rep.check_true("resilience/has-server-handle", False,
                       hint="run.py should set session.server so this suite can cycle it")
        return
    page = s.page
    # Taking the server down makes the browser log a failed reconnect per
    # attempt, per socket. Those are the point of the exercise, not a defect —
    # anything NOT matching these is still reported.
    s.expect_error("ERR_CONNECTION_REFUSED")

    async def live():
        return await page.evaluate("""() => ({
          dot: document.getElementById('engine-dot').classList.contains('on'),
          rs: ws ? ws.readyState : -1,
          polys: (_canvasDoc && _canvasDoc.polygons) ? _canvasDoc.polygons.length : null })""")

    st = await live()
    rep.check_true("resilience/connected-at-start", st["dot"] and st["rs"] == 1,
                   detail=f"dot={st['dot']} readyState={st['rs']}")

    server.__exit__(None, None, None)
    await page.wait_for_timeout(2500)
    st = await live()
    rep.check_true("resilience/notices-server-gone", st["dot"] is False and st["rs"] == 3,
                   detail=f"dot={st['dot']} readyState={st['rs']}",
                   hint="the engine dot is the only signal the user gets, so it must go out")

    server.__enter__()          # same port, so the page can find it again
    await page.wait_for_timeout(8000)
    st = await live()
    rep.check_true("resilience/reconnects-without-reload", st["dot"] and st["rs"] == 1,
                   detail=f"dot={st['dot']} readyState={st['rs']}",
                   hint="connectWS should retry on close with a backoff. If this fails the user "
                        "has to reload the editor after every server restart, and will most "
                        "likely report it as the OUTPUT windows being broken.")

    # connected is not the same as working: prove a message still lands
    before = (await live())["polys"]
    await page.evaluate("send({type:'poly_add'})")
    await page.wait_for_timeout(1500)
    after = (await live())["polys"]
    rep.check("resilience/usable-after-reconnect", after, (before or 0) + 1,
              hint="`send` reads the CURRENT ws, so it must not be captured in a closure over "
                   "the old socket")
