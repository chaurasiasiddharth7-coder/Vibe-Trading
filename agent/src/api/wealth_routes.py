"""Routes for the Kite-style pages and the server's Kite login page. All read-only; nothing here trades.

    GET  /api/desk/guard/status            Profit Guard strip (owner only; others get a short reason)
    GET  /api/desk/holdings                holdings in one shape (Kite for the owner, paper for everyone)
    GET  /api/desk/groups                  groups with live value and the ungrouped rest
    POST /api/desk/groups                  {name, colour?, goal_amount?, goal_date?, asset_types?}
    PATCH/DELETE /api/desk/groups/{id}
    PUT  /api/desk/groups/{id}/allocations {holding_id, qty}   qty 0 removes
    GET  /api/desk/fno                     F&O positions with Profit Guard columns
    GET  /api/desk/fno/chain?underlying=NIFTY&expiry=YYYY-MM-DD
    GET  /api/desk/wealth                  Gold / Equity ETF / Debt ETF / MF tabs, SIPs
    GET  /api/desk/suggestions             ranked idea cards
    GET  /zerodha/callback                 where Kite sends you after login (see below)

The callback does NOT use up the login by default: it shows the address so you can paste it into the Mac's
login window, exactly as today. Set ``KITE_SERVER_LOGIN=1`` in the server's .env (once the guard runs on the
server) and it saves the day's token on the server instead.
"""

from __future__ import annotations

import html
import os
import threading
import time
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

AuthDep = Callable[..., Awaitable[Any] | Any]
_CB_LOCK = threading.Lock()
_CB_LAST: dict[str, list[float]] = {}  # per caller, so one stranger can't block the owner's login


def _page(title: str, body: str) -> HTMLResponse:
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#111}}
code,textarea{{font:13px ui-monospace,monospace}}textarea{{width:100%;height:96px}}button{{min-height:44px;padding:0 16px;font-size:15px}}
.ok{{color:#047857}}.bad{{color:#b91c1c}}</style></head><body>{body}</body></html>"""
    return HTMLResponse(doc, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def register_wealth_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError("register_wealth_routes: pass require_auth explicitly")
        require_auth = host.require_auth

    from src.alphadesk import wealth as W

    def bad(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/desk/guard/status", dependencies=[Depends(require_auth)])
    def guard_status() -> dict[str, Any]:
        return {"ok": True, "data": W.guard_status()}

    @app.get("/api/desk/holdings", dependencies=[Depends(require_auth)])
    def holdings() -> dict[str, Any]:
        return {"ok": True, "data": W.holdings()}

    @app.get("/api/desk/groups", dependencies=[Depends(require_auth)])
    def groups() -> dict[str, Any]:
        h = W.holdings()
        return {"ok": True, "data": {**W.groups_view(h["rows"], h["read"]), "holdings": h["rows"], "kite": h["kite"],
                                     "note": h["note"], "as_of": h["as_of"]}}

    @app.post("/api/desk/groups", dependencies=[Depends(require_auth)])
    def add_group(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "data": W.create_group(body)}
        except W.GroupError as exc:
            raise bad(exc) from exc

    @app.patch("/api/desk/groups/{group_id}", dependencies=[Depends(require_auth)])
    def edit_group(group_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "data": W.update_group(group_id, body)}
        except W.GroupError as exc:
            raise bad(exc) from exc

    @app.delete("/api/desk/groups/{group_id}", dependencies=[Depends(require_auth)])
    def remove_group(group_id: str) -> dict[str, Any]:
        try:
            W.delete_group(group_id)
        except W.GroupError as exc:
            raise bad(exc) from exc
        return {"ok": True}

    @app.put("/api/desk/groups/{group_id}/allocations", dependencies=[Depends(require_auth)])
    def allocate(group_id: str, body: dict[str, Any]) -> dict[str, Any]:
        held = {r["id"]: r for r in W.holdings()["rows"]}
        try:
            qty = float(body.get("qty", 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Quantity must be a number.") from exc
        try:
            return {"ok": True, "data": W.set_allocation(group_id, str(body.get("holding_id", "")), qty, held)}
        except W.GroupError as exc:
            raise bad(exc) from exc

    @app.get("/api/desk/fno", dependencies=[Depends(require_auth)])
    def fno() -> dict[str, Any]:
        return {"ok": True, "data": W.fno_view()}

    @app.get("/api/desk/fno/chain", dependencies=[Depends(require_auth)])
    def chain(underlying: str = Query("NIFTY", max_length=20), expiry: str | None = Query(None, max_length=10)) -> dict[str, Any]:
        try:
            return {"ok": True, "data": W.option_chain(underlying, expiry)}
        except ValueError as exc:
            raise bad(exc) from exc

    @app.get("/api/desk/wealth", dependencies=[Depends(require_auth)])
    def wealth() -> dict[str, Any]:
        return {"ok": True, "data": W.wealth_view()}

    @app.get("/api/desk/suggestions", dependencies=[Depends(require_auth)])
    def suggestions() -> dict[str, Any]:
        return {"ok": True, "data": W.suggestions()}

    @app.get("/zerodha/callback", include_in_schema=False)
    def zerodha_callback(request: Request) -> HTMLResponse:
        """Kite redirects here after you log in. No auth: Zerodha's redirect carries no key, and the token
        exchange needs the API secret, which only this server holds."""
        from src.live.profit_guard.feeds import kite_auth

        url = str(request.url)
        try:
            token = kite_auth.parse_request_token(url)
        except ValueError:
            return _page("Kite login", "<h1 class='bad'>That login didn't work</h1><p>Start the Kite login again.</p>")
        kite_auth._load_env()
        if os.environ.get("KITE_SERVER_LOGIN", "").strip() != "1":
            safe = html.escape(url)
            return _page("Kite login: one more step", f"""<h1>Kite login: one more step</h1>
<p>Copy this address and paste it into the Kite login window on your Mac, then press Enter there.</p>
<textarea id="u" readonly>{safe}</textarea>
<p><button onclick="navigator.clipboard.writeText(document.getElementById('u').value).then(()=>this.textContent='Copied')">Copy address</button></p>
<p>The code inside works once and only for the next few minutes. Don't share it.</p>""")
        if not os.environ.get("KITE_OWNER_USER_ID", "").strip():
            # Without the owner's Zerodha id, anyone could log in with their own account and swap the server's session.
            return _page("Kite login", "<h1 class='bad'>Server login isn't set up</h1>"
                                       "<p>Add KITE_OWNER_USER_ID (your Zerodha client id) to the server's .env first. "
                                       "Nothing was saved.</p>")
        from src.api.users_routes import _ip

        who = _ip(request)
        with _CB_LOCK:
            now = time.monotonic()
            if len(_CB_LAST) > 5000:
                _CB_LAST.clear()
            tries = [t for t in _CB_LAST.get(who, []) if now - t < 60]
            if len(tries) >= 5:
                return _page("Kite login", "<h1 class='bad'>Too many tries</h1><p>Wait a minute and log in again.</p>")
            _CB_LAST[who] = tries + [now]
        try:
            from src.live.profit_guard.feeds.kite_client import ReadOnlyKite

            session_data = ReadOnlyKite().exchange_request_token(token)
            owner_id = os.environ.get("KITE_OWNER_USER_ID", "").strip().upper()
            if str(session_data.get("user_id") or "").upper() != owner_id:
                return _page("Kite login", "<h1 class='bad'>Wrong Zerodha account</h1>"
                                           "<p>This server only accepts the owner's Zerodha login. Nothing was saved.</p>")
            rec = kite_auth.save_token(session_data, int(time.time() * 1000))
        except Exception as exc:  # the message never contains the secret
            return _page("Kite login", f"<h1 class='bad'>Couldn't finish the login</h1><p>{html.escape(str(exc)[:200])}</p>"
                                       "<p>The code may have expired. Log in to Kite again.</p>")
        until = kite_auth.session.to_ist(rec["expires_at_ms"]).strftime("%d %b %H:%M")
        return _page("Kite login saved", f"<h1 class='ok'>Logged in</h1><p>Saved on the server. Prices are live until "
                                         f"{html.escape(until)} IST. You can close this page.</p>")
