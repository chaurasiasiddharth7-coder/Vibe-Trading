"""Desk users: Argon2id passwords, lockout, roles and page access, cookie sessions, owner-only admin."""

import sqlite3
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.alphadesk import profiles, users
from src.api.security import require_auth


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_API_KEY", raising=False)
    from src.api import users_routes

    users_routes._IP_TRIES.clear()
    return tmp_path


def _db_dump() -> str:
    con = sqlite3.connect(users.db_path())
    rows = []
    for table in ("users", "sessions", "auth_audit"):
        rows += [repr(tuple(r)) for r in con.execute(f"SELECT * FROM {table}")]
    return "\n".join(rows)


def test_password_never_stored_and_argon2id(home):
    u, otp = users.create_user("ayushi", "Ayushi", "trader", ["stocks", "portfolio"])
    dump = _db_dump()
    assert otp not in dump
    assert "$argon2id$" in dump
    assert len(otp) >= users.MIN_PASSWORD
    assert u.must_change and u.profile_id == "ayushi"
    assert profiles.get("ayushi") is not None  # a private desk was made


def test_login_change_and_must_change(home):
    _u, otp = users.create_user("ayushi", "Ayushi", "trader")
    user, token = users.login("Ayushi", otp)
    assert users.session_user(token).id == "ayushi"
    ok, why = users.route_allowed(user, "/api/desk/board/paper", "GET")
    assert not ok and "Change your password" in why
    with pytest.raises(users.AuthError):
        users.change_password(user, otp, "short")
    with pytest.raises(users.AuthError):
        users.change_password(user, "wrong" * 4, "a-much-longer-pass-123")
    users.change_password(user, otp, "a-much-longer-pass-123", keep_token=token)
    fresh = users.session_user(token)
    assert fresh is not None and not fresh.must_change
    assert users.route_allowed(fresh, "/api/desk/board/paper", "GET")[0]


def test_lockout_after_five(home):
    _u, otp = users.create_user("ayushi", "Ayushi", "trader")
    for _ in range(4):
        with pytest.raises(users.AuthError, match="Wrong username"):
            users.login("ayushi", "nope-nope-nope")
    with pytest.raises(users.AuthError, match="locked"):
        users.login("ayushi", "nope-nope-nope")
    with pytest.raises(users.AuthError, match="Too many"):
        users.login("ayushi", otp)  # even the right password is refused while locked
    users.unlock("ayushi")
    assert users.login("ayushi", otp)[0].id == "ayushi"


def test_unknown_user_same_message(home):
    with pytest.raises(users.AuthError) as e:
        users.login("ghost", "whatever-whatever")
    assert str(e.value) == users.GENERIC_FAIL


def test_reset_and_disable_end_sessions(home):
    _u, otp = users.create_user("ayushi", "Ayushi", "trader")
    _user, token = users.login("ayushi", otp)
    new = users.reset_password("ayushi")
    assert users.session_user(token) is None and new != otp
    with pytest.raises(users.AuthError):
        users.login("ayushi", otp)
    _user, token = users.login("ayushi", new)
    users.set_disabled("ayushi", True)
    assert users.session_user(token) is None
    with pytest.raises(users.AuthError):
        users.login("ayushi", new)


def test_expired_session(home, monkeypatch):
    _u, otp = users.create_user("ayushi", "Ayushi", "trader")
    _user, token = users.login("ayushi", otp)
    real = time.time
    monkeypatch.setattr(users.time, "time", lambda: real() + users.SESSION_SECONDS + 5)
    assert users.session_user(token) is None


def test_page_and_role_rules(home):
    v, _ = users.create_user("dad", "Dad", "viewer", ["stocks", "suggestions"])
    v = users.User(**{**v.__dict__, "must_change": False})
    assert users.route_allowed(v, "/api/desk/suggestions", "GET")[0]
    assert not users.route_allowed(v, "/api/desk/fno", "GET")[0]           # page not ticked
    assert not users.route_allowed(v, "/api/desk/board/paper", "POST")[0]  # viewer is read only
    assert not users.route_allowed(v, "/api/desk/live/status", "GET")[0]   # never live
    assert not users.route_allowed(v, "/api/desk/users", "GET")[0]         # never the Users page
    assert not users.route_allowed(v, "/api/settings", "GET")[0]
    assert users.route_allowed(v, "/api/desk/me/prefs", "PUT")[0]          # own layout is always allowed
    o, _ = users.create_user("sid", "Sid", "owner")
    o = users.User(**{**o.__dict__, "must_change": False})
    assert users.route_allowed(o, "/api/desk/users", "GET")[0]
    assert not users.route_allowed(o, "/api/desk/live/unlock", "POST")[0]  # live stays key-only
    assert o.profile_id is None


def test_keep_one_owner(home):
    users.create_user("sid", "Sid", "owner")
    with pytest.raises(users.AuthError):
        users.set_disabled("sid", True)
    with pytest.raises(users.AuthError):
        users.update_user("sid", role="viewer")


def _app():
    from src.api.users_routes import register_users_routes

    app = FastAPI()

    @app.get("/api/desk/who", dependencies=[Depends(require_auth)])
    def who():
        u = users.current_user()
        return {"profile": profiles.current_id(), "user": u.username if u else None}

    @app.get("/api/desk/fno", dependencies=[Depends(require_auth)])
    def fno():
        return {"ok": True}

    register_users_routes(app, require_auth)
    return app


def test_http_flow_cookie_owner_admin(home, monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "owner-secret-key")
    c = TestClient(_app(), base_url="https://testserver")
    # Without a key or a session, a remote caller is refused.
    assert c.get("/api/desk/who").status_code == 401
    # The owner key can manage users.
    r = c.post("/api/desk/users", json={"username": "ayushi", "name": "Ayushi", "role": "trader",
                                         "pages": ["stocks"]}, headers={"Authorization": "Bearer owner-secret-key"})
    assert r.status_code == 200, r.text
    otp = r.json()["data"]["one_time_password"]
    assert "pw_hash" not in r.text
    # Sign in with the password: cookie is HttpOnly, Secure, SameSite=Lax.
    r = c.post("/api/desk/auth/login", json={"username": "ayushi", "password": otp})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie and "path=/api;" in cookie + ";"
    # Must change first.
    assert c.get("/api/desk/who").status_code == 403
    r = c.post("/api/desk/auth/change-password", json={"old": otp, "new": "blue-river-stone-4417"})
    assert r.status_code == 200, r.text
    assert c.get("/api/desk/who").json() == {"profile": "ayushi", "user": "ayushi"}
    assert c.get("/api/desk/fno").status_code == 403          # page not ticked
    assert c.get("/api/desk/users").status_code == 403        # not the owner
    assert c.post("/api/desk/users", json={"username": "x1", "role": "owner"}).status_code == 403
    assert c.get("/api/desk/auth/session").json()["data"]["username"] == "ayushi"
    c.post("/api/desk/auth/logout")
    assert c.get("/api/desk/auth/session").json()["data"] is None
    assert c.get("/api/desk/who").status_code == 401


def test_http_login_errors_and_ip_brake(home, monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "owner-secret-key")
    c = TestClient(_app(), base_url="https://testserver")
    r = c.post("/api/desk/auth/login", json={"username": "nobody", "password": "x" * 12})
    assert r.status_code == 401 and r.json()["detail"] == users.GENERIC_FAIL
    for _ in range(25):
        r = c.post("/api/desk/auth/login", json={"username": "nobody", "password": "x" * 12})
    assert r.status_code == 429


def test_profile_key_still_works(home, monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "owner-secret-key")
    key = profiles.create("old", "Old Key User")
    c = TestClient(_app(), base_url="https://testserver")
    r = c.get("/api/desk/who", headers={"Authorization": f"Bearer {key}"})
    assert r.json() == {"profile": "old", "user": None}
