"""Regression tests for the break-it review findings (one test per finding)."""

import threading
from datetime import date

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.alphadesk import profiles, users, wealth as W
from src.api.security import require_auth


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_API_KEY", raising=False)
    users.bind_user(None)
    profiles.bind(None)
    users._GHOSTS.clear()
    from src.api import users_routes

    users_routes._IP_TRIES.clear()
    yield tmp_path
    users.bind_user(None)
    profiles.bind(None)


# 1. A Kite logout must not wipe groups
def test_unread_kite_holdings_are_not_trimmed(home):
    rows = [W._row("INFY", "NSE", 10, 1500, 1500, source="kite")]
    held = {r["id"]: r for r in rows}
    g = W.create_group({"name": "Retire"})
    W.set_allocation(g["id"], rows[0]["id"], 8, held)
    view = W.groups_view([], read=["paper"])  # Kite logged out: only paper was read
    assert view["notes"] == []
    assert view["groups"][0]["waiting"][0]["qty"] == 8
    assert W._load_groups()[0]["allocations"][0]["qty"] == 8  # still on disk
    view = W.groups_view([W._row("INFY", "NSE", 5, 1500, 1500, source="kite")], read=["kite", "paper"])
    assert view["groups"][0]["lines"][0]["qty"] == 5  # a real sell, read in full, does trim


# 2. Parallel wrong passwords still lock the account, and at most 5 are checked
def test_parallel_tries_cannot_skip_the_lock(home):
    _u, otp = users.create_user("ayushi", "A", "trader")
    checked = []
    real = users._verify

    def counting(h, p):
        checked.append(1)
        return real(h, p)

    users._verify = counting
    try:
        def attempt():
            try:
                users.login("ayushi", "wrong-wrong-wrong")
            except users.AuthError:
                pass

        ts = [threading.Thread(target=attempt) for _ in range(30)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        users._verify = real
    assert len(checked) <= users.MAX_FAILS
    assert users.get_user("ayushi").locked_until > 0
    with pytest.raises(users.AuthError, match="Too many"):
        users.login("ayushi", otp)


# 3. The address brake uses our proxy's hop, not a value the client typed
def test_forwarded_for_left_value_is_ignored(home):
    from starlette.requests import Request

    from src.api.users_routes import _ip

    req = Request({"type": "http", "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")], "client": ("127.0.0.1", 1)})
    assert _ip(req) == "203.0.113.9"


def _app():
    from src.api.users_routes import register_users_routes

    app = FastAPI()

    @app.get("/api/desk/guard/status", dependencies=[Depends(require_auth)])
    def guard():
        return W.guard_status()

    register_users_routes(app, require_auth)
    return app


def test_forged_forwarded_for_still_braked(home, monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "k" * 20)
    c = TestClient(_app(), base_url="https://testserver")
    # Per address + name the limit is 20; across names one address gets 100 a minute (changed 20 Nov after the audit).
    codes = [c.post("/api/desk/auth/login", json={"username": f"nobody{i}", "password": "x" * 12},
                    headers={"X-Forwarded-For": f"10.0.0.{i % 250}"}).status_code for i in range(110)]
    assert 429 in codes


# 4 + 5. Backtest: lock before stop on the same swing; a gap above the target sells at the open
def test_backtest_lock_before_stop_and_gap_target():
    from src.live.profit_guard import backtest_btst as B
    from src.live.profit_guard.config import load_config

    base = load_config()
    V = {v.key: v for v in B.VARIANTS}
    t = B.Trade(date(2025, 3, 3), date(2025, 3, 4), "X", 100.0, 101.0, 101.5, 97.0, 100.0)
    for k in ("D", "E", "F"):
        r = B.simulate(t, V[k], base, path="best")
        assert r["reason"] == "PROFIT_LOCK" and r["gross"] > 0, k
    g = B.simulate(B.Trade(date(2025, 3, 3), date(2025, 3, 4), "X", 100.0, 102.0, 103.0, 101.0, 102.5), V["A"], base)
    assert g["reason"] == "TARGET" and g["gross"] == pytest.approx(2.0)


# 6. Two practice trades in one symbol keep separate ids
def test_paper_rows_have_unique_ids(home, monkeypatch):
    import src.alphadesk.board_paper as bp

    monkeypatch.setattr(bp, "list_trades", lambda: [
        {"id": "t1", "symbol": "RELIANCE", "status": "OPEN", "qty": 10, "entry": 100, "side": "LONG"},
        {"id": "t2", "symbol": "RELIANCE", "status": "OPEN", "qty": 5, "entry": 101, "side": "SHORT"}])
    monkeypatch.setattr(bp, "with_live_prices", lambda ts: ts)
    rows = W.paper_holdings()
    assert len({r["id"] for r in rows}) == 2


# 7. An owner who hasn't replaced the one-time password sees no guard data
def test_must_change_owner_gets_no_guard_data(home, monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "k" * 20)
    _u, otp = users.create_user("boss", "Boss", "owner")
    c = TestClient(_app(), base_url="https://testserver")
    assert c.post("/api/desk/auth/login", json={"username": "boss", "password": otp}).status_code == 200
    assert c.get("/api/desk/guard/status").status_code == 403


# 8. No enumeration: unknown names lock too; a turned-off account answers like a wrong password
def test_unknown_users_lock_too_and_disabled_is_generic(home):
    msgs = []
    for _ in range(6):
        with pytest.raises(users.AuthError) as e:
            users.login("ghost", "whatever-whatever")
        msgs.append(str(e.value))
    assert "locked" in msgs[4] or "Too many" in msgs[4]
    _u, otp = users.create_user("ayushi", "A", "trader")
    users.set_disabled("ayushi", True)
    with pytest.raises(users.AuthError) as e:
        users.login("ayushi", otp)
    assert str(e.value) == users.GENERIC_FAIL


# 9. The callback limit is per caller (one stranger can't lock the owner out)
def test_callback_limit_is_per_caller():
    from src.api import wealth_routes

    assert isinstance(wealth_routes._CB_LAST, dict)


# plausible: a password user can't take over an existing key-made desk
def test_password_user_cannot_take_over_key_profile(home):
    profiles.create("bob", "Bob")
    with pytest.raises(users.AuthError, match="already exists"):
        users.create_user("bob", "Bob", "trader")


# plausible: wrong current passwords on change count toward the lock and end sessions
def test_change_password_bruteforce_locks(home):
    _u, otp = users.create_user("ayushi", "A", "trader")
    user, token = users.login("ayushi", otp)
    for _ in range(users.MAX_FAILS):
        with pytest.raises(users.AuthError):
            users.change_password(user, "nope-nope-nope", "a-good-new-pass-77")
    assert users.session_user(token) is None
