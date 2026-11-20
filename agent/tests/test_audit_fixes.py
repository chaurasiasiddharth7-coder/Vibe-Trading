"""Regression tests for the 20 Nov deep-audit findings (H1-H3, M1-M4, L1-L2, callback owner id)."""

from __future__ import annotations

import json
import time
from decimal import Decimal as D

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.alphadesk import profiles, users
from src.api.security import require_auth
from src.live.profit_guard.config import load_config
from src.live.profit_guard.models import PositionPlan, Side, State
from src.live.profit_guard.paper_broker import PaperBroker
from src.live.profit_guard.runner import GuardRunner, queue_entry
from src.live.profit_guard.store import GuardStore
from tests.profit_guard.test_runner import Quiet, at, make


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_API_KEY", raising=False)
    monkeypatch.delenv("VT_TRUSTED_PROXIES", raising=False)
    return tmp_path


def _restart(tmp_path, r, prices):
    r.save_broker()
    r.store.close()
    broker = PaperBroker.from_state(json.loads((tmp_path / "pb.json").read_text()))
    return GuardRunner(load_config(), GuardStore(tmp_path / "g.db"), broker, prices, None, alerts=Quiet(),
                       holidays=set(), broker_state=tmp_path / "pb.json", inbox=tmp_path / "inbox.jsonl")


# H1 ------------------------------------------------------------------------------------------------------
def test_h1_restart_never_replays_the_inbox(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    r.tick(at(10, 0)); r.tick(at(10, 0, 10))
    p.now["SBIN"] = r.store.open_plans()[0].stop - 1
    r.tick(at(10, 1)); r.tick(at(10, 1, 10)); r.tick(at(10, 1, 20))
    assert r.store.open_plans() == []
    r2 = _restart(tmp_path, r, p)
    r2.start(at(10, 5))
    p.now["SBIN"] = D("800")
    out = r2.tick(at(10, 5, 10))
    r2.tick(at(10, 5, 20))
    assert not [a for a in out["alerts"] if "Paper entry sent" in a] and r2.broker.position_qty("SBIN") == 0
    r2.store.close()


def test_h1_preopen_entries_wait_for_the_open(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    r.tick(at(9, 5))  # pre-open: left in the file, not refused and lost
    assert r.store.get_meta("inbox_offset") in (None, "0")
    r.tick(at(9, 16)); r.tick(at(9, 16, 10))
    assert len(r.store.open_plans()) == 1
    r.store.close()


# H2 ------------------------------------------------------------------------------------------------------
def test_h2_crash_between_rules_and_exit_still_exits_after_restart(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    r.tick(at(10, 0)); r.tick(at(10, 0, 10))
    plan = r.store.open_plans()[0]
    r.save_broker()
    p.now["SBIN"] = plan.stop - 1

    def boom(*a, **k):
        raise SystemExit("power cut")

    r.executor.execute = boom
    with pytest.raises(SystemExit):
        r.tick(at(10, 1))
    assert r.store.get_plan(plan.id).state is not State.EXITED  # nothing marked exited without an exit on record
    r.store.close()
    broker = PaperBroker.from_state(json.loads((tmp_path / "pb.json").read_text()))
    r2 = GuardRunner(load_config(), GuardStore(tmp_path / "g.db"), broker, p, None, alerts=Quiet(), holidays=set(),
                     broker_state=tmp_path / "pb.json", inbox=tmp_path / "inbox.jsonl")
    r2.start(at(10, 2))
    for i in range(3):
        r2.tick(at(10, 2, 10 * (i + 1)))
    assert r2.broker.position_qty("SBIN") == 0
    plans = r2.store.all_plans()
    assert len(plans) == 1 and plans[0].state is State.EXITED  # no orphan with a looser stop
    r2.store.close()


def test_h2_exit_plan_is_saved_with_its_intent(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    r.tick(at(10, 0)); r.tick(at(10, 0, 10))
    plan = r.store.open_plans()[0]
    p.now["SBIN"] = plan.stop - 1
    r.tick(at(10, 1))
    exits = [i for i in r.store.intents() if i["kind"] == "exit"]
    assert exits and r.store.get_plan(plan.id).state is State.EXITED
    r.store.close()


# M3 ------------------------------------------------------------------------------------------------------
def test_m3_exited_plans_are_not_shown_as_open():
    from src.alphadesk import wealth
    from src.live.profit_guard.store import default_paths

    db, _ = default_paths()
    st = GuardStore(db)
    pl = PositionPlan(id="x", strategy="swing", symbol="SBIN", side=Side.LONG, qty=1, entry=D(1), initial_stop=D("0.5"),
                      stop=D("0.5"), target=None, one_r=D("0.5"), opened_ms=at(10, 0))
    st.save_plan(pl.with_(state=State.EXITED, qty_open=0), at(11, 0))
    st.save_plan(pl.with_(id="y", symbol="TCS"), at(11, 0))
    st.close()
    plans, _port, _err = wealth._guard_plans()
    assert [q["id"] for q in plans] == ["y"]


# H3 / M1 / M2 / L1 / L2 ------------------------------------------------------------------------------------
def _client(monkeypatch):
    monkeypatch.setenv("API_AUTH_KEY", "k" * 24)
    from src.api.users_routes import register_users_routes

    app = FastAPI()
    register_users_routes(app, require_auth)

    @app.get("/api/analysis/{sym}", dependencies=[Depends(require_auth)])
    def analysis(sym: str):
        return {"ok": True, "who": users.current_user().username if users.current_user() else None}

    return TestClient(app, base_url="https://testserver")


def test_h3_cookie_covers_analysis_and_kronos(monkeypatch):
    c = _client(monkeypatch)
    _u, otp = users.create_user("tina", "Tina", "trader", ["stocks"])
    r = c.post("/api/desk/auth/login", json={"username": "tina", "password": otp})
    assert "path=/api;" in r.headers["set-cookie"].lower().replace(" ", "") + ";"
    c.post("/api/desk/auth/change-password", json={"old": otp, "new": "correct-horse-battery-9"})
    r = c.get("/api/analysis/RELIANCE")
    assert r.status_code == 200 and r.json()["who"] == "tina"


def _req(peer: str, xff: str | None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers, "method": "POST", "path": "/"})


def test_m1_real_client_ip_behind_docker_gateway_and_forged_left_values(monkeypatch):
    from src.api.users_routes import _ip

    assert _ip(_req("172.18.0.1", "198.51.100.7")) == "198.51.100.7"  # Caddy on the docker bridge
    assert _ip(_req("172.18.0.1", "6.6.6.6, 198.51.100.7")) == "198.51.100.7"  # left value typed by the client
    assert _ip(_req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"  # not our proxy: header ignored
    monkeypatch.setenv("VT_TRUSTED_PROXIES", "76.76.21.0/24")  # e.g. an edge proxy you add
    assert _ip(_req("127.0.0.1", "198.51.100.7, 76.76.21.9")) == "198.51.100.7"


def test_m1_one_address_cant_block_other_users(monkeypatch):
    c = _client(monkeypatch)
    users.create_user("victim", "V", "trader")
    for _ in range(25):
        c.post("/api/desk/auth/login", json={"username": "someone", "password": "x" * 12})
    r = c.post("/api/desk/auth/login", json={"username": "victim", "password": "wrong-but-not-braked"})
    assert r.status_code == 401  # a normal wrong-password answer, not 429


def test_m2_weak_new_password_never_locks_with_the_right_old_one():
    _u, otp = users.create_user("ayushi", "A", "trader")
    user, _tok = users.login("ayushi", otp)
    for _ in range(8):
        with pytest.raises(users.AuthError, match="at least|different|username"):
            users.change_password(user, otp, "short")
    fresh = users.get_user(user.id)
    assert fresh.failed == 0 and fresh.locked_until == 0


def test_l1_unknown_names_lock_exactly_like_real_ones():
    users.create_user("realuser", "R", "trader")
    msgs = {"realuser": [], "ghostuser": []}
    for name in msgs:
        for _ in range(9):
            try:
                users.login(name, "wrong-password-123")
            except users.AuthError as exc:
                msgs[name].append(str(exc))
    assert msgs["realuser"] == msgs["ghostuser"]


def test_l2_key_profile_cant_take_a_password_users_name():
    users.create_user("bob", "Bob", "owner")
    with pytest.raises(ValueError, match="password user"):
        profiles.create("bob", "Bob key")


# M4 ------------------------------------------------------------------------------------------------------
def test_m4_raw_kite_client_unreachable():
    from src.live.profit_guard.feeds.kite_client import KiteReadOnlyError, ReadOnlyKite

    class Fake:
        def __init__(self, api_key):
            self.api_key = api_key

        def set_access_token(self, t):
            pass

        def place_order(self, **k):
            return "ORDER-1"

        def ltp(self, keys):
            return {"x": 1}

    rk = ReadOnlyKite(api_key="k", access_token="t", factory=Fake)
    for attempt in (lambda: rk._k, lambda: getattr(rk, "_k"), lambda: rk._k.place_order(), lambda: rk.place_order):
        with pytest.raises(KiteReadOnlyError):
            attempt()
    assert rk.ltp(["x"]) == {"x": 1}


# callback ----------------------------------------------------------------------------------------------------
def test_server_kite_login_refuses_without_owner_id(monkeypatch):
    monkeypatch.setenv("KITE_SERVER_LOGIN", "1")
    monkeypatch.delenv("KITE_OWNER_USER_ID", raising=False)
    from src.api.wealth_routes import register_wealth_routes

    app = FastAPI()
    register_wealth_routes(app, require_auth)
    c = TestClient(app)
    r = c.get("/zerodha/callback?request_token=abc123&status=success")
    assert r.status_code == 200 and "isn't set up" in r.text and "Nothing was saved" in r.text


# plausible -> confirmed: order ids never reused after a crash restores an older broker state
def test_order_ids_not_reused_after_restore(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    r.save_broker()  # old snapshot: next id is low
    old_state = (tmp_path / "pb.json").read_text()
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    r.tick(at(10, 0)); r.tick(at(10, 0, 10))
    p.now["SBIN"] = r.store.open_plans()[0].stop - 1
    r.tick(at(10, 1))
    used = {i["broker_order_id"] for i in r.store.intents() if i.get("broker_order_id")}
    assert used
    r.store.close()
    (tmp_path / "pb.json").write_text(old_state)  # the crash lost the newer broker state
    r2 = GuardRunner(load_config(), GuardStore(tmp_path / "g.db"), PaperBroker.from_state(json.loads(old_state)), p, None,
                     alerts=Quiet(), holidays=set(), broker_state=tmp_path / "pb.json", inbox=tmp_path / "inbox.jsonl")
    r2.start(at(10, 2))
    o = r2.broker.submit_limit("TCS", "buy", 1, D("100"), tag="probe", now_ms=at(10, 2, 1))
    assert o["order_id"] not in used
    r2.store.close()


def test_ai_budget_tools_are_owner_only_for_password_users():
    t, _ = users.create_user("tina2", "T", "trader", ["stocks"])
    o, _ = users.create_user("boss2", "B", "owner")
    t.must_change = False
    o.must_change = False
    for path in ("/api/desk/morning", "/api/desk/arena", "/api/desk/pm/plan"):
        assert users.route_allowed(t, path, "POST")[0] is False
        assert users.route_allowed(o, path, "POST")[0] is True
    assert users.route_allowed(t, "/api/desk/board/scan", "POST")[0] is True  # the Stocks page scan stays open
