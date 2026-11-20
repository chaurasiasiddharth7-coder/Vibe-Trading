"""Sign-in with a password, and the owner's Users page.

Routes live under ``/api/desk/`` so the hosted dashboard's existing proxy rule already forwards them.

    POST /api/desk/auth/login            {username, password}   -> session cookie (no auth needed)
    GET  /api/desk/auth/session                                  -> who is signed in (no auth needed)
    POST /api/desk/auth/logout
    POST /api/desk/auth/logout-all
    POST /api/desk/auth/change-password  {old, new}
    GET  /api/desk/users                                         owner only
    POST /api/desk/users                 {username, name, role, pages} -> one-time password, shown once
    PATCH /api/desk/users/{id}           {name?, role?, pages?}
    POST /api/desk/users/{id}/reset      -> one-time password, shown once
    POST /api/desk/users/{id}/disable | /enable | /unlock
    GET  /api/desk/users/audit
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response

AuthDep = Callable[..., Awaitable[Any] | Any]

# A light per-address brake on top of the per-account lockout: 20 tries a minute.
_IP_TRIES: dict[str, deque[float]] = {}
_IP_LOCK = threading.Lock()
IP_MAX_PER_MIN = 20  # per address + username
IP_TOTAL_PER_MIN = 100  # per address, all usernames: slows a password spray without letting one guesser lock others out


# The session cookie covers every route a password user may reach (/api/desk, /api/analysis, /api/kronos).
COOKIE_PATH = "/api"

# Our own proxy hops: loopback, the Docker bridge and other private networks, plus any CIDRs in
# VT_TRUSTED_PROXIES (comma-separated). The API port is bound to 127.0.0.1, so only Caddy can reach it.
_DEFAULT_TRUSTED = ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")


def _trusted_nets() -> list[Any]:
    import ipaddress
    import os

    out = []
    for raw in (*_DEFAULT_TRUSTED, *os.environ.get("VT_TRUSTED_PROXIES", "").split(",")):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return out


def _is_trusted(addr: str, nets: list[Any]) -> bool:
    import ipaddress

    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in n for n in nets)


def _ip(request: Request) -> str:
    """The caller's address: walk X-Forwarded-For from the right, skipping our own proxy hops, and take the
    first address we didn't add ourselves. Values further left were typed by the client and are never used."""
    peer = request.client.host if request.client else ""
    nets = _trusted_nets()
    if not _is_trusted(peer, nets):
        return peer  # not behind our own proxy: the header could say anything
    fwd = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(fwd):
        if not _is_trusted(hop, nets):
            return hop
    return fwd[0] if fwd else peer


def _ip_brake(ip: str, limit: int = IP_MAX_PER_MIN) -> None:
    now = time.monotonic()
    with _IP_LOCK:
        if len(_IP_TRIES) > 5000:  # never grows without limit
            for k in [k for k, q in _IP_TRIES.items() if not q or now - q[-1] > 60]:
                _IP_TRIES.pop(k, None)
            if len(_IP_TRIES) > 5000:
                _IP_TRIES.clear()
        q = _IP_TRIES.setdefault(ip, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail="Too many sign-in tries from here. Wait a minute.")
        q.append(now)


def _secure_cookie(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (request.url.hostname or "").lower()
    return proto == "https" or host not in {"localhost", "127.0.0.1", "testserver"}


def _set_cookie(response: Response, request: Request, token: str) -> None:
    from src.alphadesk import users

    response.set_cookie(
        users.COOKIE, token, max_age=users.SESSION_SECONDS, httponly=True,
        secure=_secure_cookie(request), samesite="lax", path=COOKIE_PATH,
    )


def _clear_cookie(response: Response, request: Request) -> None:
    from src.alphadesk import users

    for path in (COOKIE_PATH, "/api/desk"):  # "/api/desk" was the path before 20 Nov; clear any old cookie too
        response.delete_cookie(users.COOKIE, path=path, httponly=True, secure=_secure_cookie(request), samesite="lax")


def _caller_is_owner() -> bool:
    """Owner = a password user with the owner role, or the owner key / loopback (no user, owner desk)."""
    from src.alphadesk import profiles, users

    user = users.current_user()
    if user is not None:
        return user.role == "owner" and not user.must_change
    return profiles.current_id() is None


def _actor() -> str:
    from src.alphadesk import users

    user = users.current_user()
    return user.username if user else "owner-key"


def register_users_routes(app: FastAPI, require_auth: AuthDep | None = None) -> None:
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError("register_users_routes: pass require_auth explicitly")
        require_auth = host.require_auth

    from src.alphadesk import users

    def owner_only() -> None:
        if not _caller_is_owner():
            raise HTTPException(status_code=403, detail="Only the owner can manage users.")

    def fail(exc: users.AuthError, code: int = 400) -> HTTPException:
        return HTTPException(status_code=code, detail=str(exc))

    @app.post("/api/desk/auth/login")
    def login(body: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
        from src.api.security import _reject_cross_site_browser_request

        _reject_cross_site_browser_request(request)
        ip = _ip(request)
        # Keyed by address AND username: several people behind one address (an office, a Vercel edge) can't
        # lock each other out; guessing one name is still capped here and by the 5-try account lock.
        _ip_brake(f"{ip}|{str(body.get('username', ''))[:64].strip().lower()}")
        _ip_brake(f"{ip}|*", IP_TOTAL_PER_MIN)
        try:
            user, token = users.login(str(body.get("username", ""))[:64], str(body.get("password", ""))[:300], ip=ip)
        except users.AuthError as exc:
            raise fail(exc, 401) from exc
        _set_cookie(response, request, token)
        return {"ok": True, "data": user.public()}

    @app.get("/api/desk/auth/session")
    def session(request: Request) -> dict[str, Any]:
        user = users.session_user(request.cookies.get(users.COOKIE))
        return {"ok": True, "data": user.public() if user else None}

    @app.post("/api/desk/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, Any]:
        from src.api.security import _reject_cross_site_browser_request

        _reject_cross_site_browser_request(request)
        users.logout(request.cookies.get(users.COOKIE))
        _clear_cookie(response, request)
        return {"ok": True}

    @app.post("/api/desk/auth/logout-all", dependencies=[Depends(require_auth)])
    def logout_all(request: Request, response: Response) -> dict[str, Any]:
        user = users.current_user()
        if user is None:
            raise HTTPException(status_code=400, detail="Sign in with your password to use this.")
        n = users.end_sessions(user.id)
        users.audit("logout_all", user, ip=_ip(request), detail=f"{n} sessions")
        _clear_cookie(response, request)
        return {"ok": True, "data": {"ended": n}}

    @app.post("/api/desk/auth/change-password", dependencies=[Depends(require_auth)])
    def change_password(body: dict[str, Any], request: Request) -> dict[str, Any]:
        user = users.current_user()
        if user is None:
            raise HTTPException(status_code=400, detail="Sign in with your password to change it.")
        try:
            users.change_password(user, str(body.get("old", "")), str(body.get("new", "")),
                                  keep_token=request.cookies.get(users.COOKIE), ip=_ip(request))
        except users.AuthError as exc:
            raise fail(exc) from exc
        fresh = users.get_user(user.id)
        return {"ok": True, "data": fresh.public() if fresh else None}

    @app.get("/api/desk/users", dependencies=[Depends(require_auth), Depends(owner_only)])
    def list_users() -> dict[str, Any]:
        return {"ok": True, "data": {"users": [u.public() for u in users.list_users()], "counts": users.counts(),
                                     "pages": list(users.PAGES), "roles": list(users.ROLES)}}

    @app.post("/api/desk/users", dependencies=[Depends(require_auth), Depends(owner_only)])
    def add_user(body: dict[str, Any]) -> dict[str, Any]:
        try:
            user, otp = users.create_user(str(body.get("username", "")), str(body.get("name", "")),
                                          str(body.get("role", "trader")), body.get("pages"), actor=_actor())
        except users.AuthError as exc:
            raise fail(exc) from exc
        return {"ok": True, "data": {"user": user.public(), "one_time_password": otp}}

    @app.get("/api/desk/users/audit", dependencies=[Depends(require_auth), Depends(owner_only)])
    def audit(limit: int = 200) -> dict[str, Any]:
        return {"ok": True, "data": users.audit_log(limit)}

    @app.patch("/api/desk/users/{user_id}", dependencies=[Depends(require_auth), Depends(owner_only)])
    def edit_user(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            user = users.update_user(user_id, name=body.get("name"), role=body.get("role"), pages=body.get("pages"),
                                     actor=_actor())
        except users.AuthError as exc:
            raise fail(exc) from exc
        return {"ok": True, "data": user.public()}

    @app.post("/api/desk/users/{user_id}/reset", dependencies=[Depends(require_auth), Depends(owner_only)])
    def reset(user_id: str) -> dict[str, Any]:
        try:
            otp = users.reset_password(user_id, actor=_actor())
        except users.AuthError as exc:
            raise fail(exc) from exc
        return {"ok": True, "data": {"one_time_password": otp}}

    @app.post("/api/desk/users/{user_id}/{action}", dependencies=[Depends(require_auth), Depends(owner_only)])
    def toggle(user_id: str, action: str) -> dict[str, Any]:
        me = users.current_user()
        try:
            if action == "disable":
                if me is not None and me.id == user_id:
                    raise users.AuthError("You can't turn off your own account.")
                user = users.set_disabled(user_id, True, actor=_actor())
            elif action == "enable":
                user = users.set_disabled(user_id, False, actor=_actor())
            elif action == "unlock":
                users.unlock(user_id, actor=_actor())
                user = users.get_user(user_id)
            else:
                raise HTTPException(status_code=404, detail="Unknown action")
        except users.AuthError as exc:
            raise fail(exc) from exc
        return {"ok": True, "data": user.public() if user else None}
