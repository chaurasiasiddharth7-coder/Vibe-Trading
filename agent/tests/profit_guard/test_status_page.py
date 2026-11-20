"""The at-a-glance status page: reads the store read-only, escapes everything, summarises the day."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal as D

from src.live.profit_guard import status_page as SP
from src.live.profit_guard.models import ExitReason, PositionPlan, Side, State
from src.live.profit_guard.store import GuardStore
from tests.profit_guard.test_runner import at


def _plan(pid, sym, **kw):
    return PositionPlan(id=pid, strategy="swing", symbol=sym, side=Side.LONG, qty=10, entry=D("100"), initial_stop=D("98"),
                        stop=D("98"), target=D("104"), one_r=D("2"), opened_ms=at(9, 30), **kw)


def test_page_shows_open_closed_and_pnl(tmp_path, monkeypatch):
    db = tmp_path / "g.db"
    st = GuardStore(db)
    st.save_plan(_plan("a", "SBIN"), at(9, 30))
    st.save_plan(_plan("b", "TCS<script>").with_(state=State.EXITED, qty_open=0, realized=D("40"), exit_ms=at(11, 0),
                                                 exit_price=D("104"), exit_reason=ExitReason.TARGET), at(11, 0))
    (tmp_path / "logs").mkdir()
    day = datetime.now().strftime("%Y-%m-%d")
    (tmp_path / "logs" / f"guard-{day}.log").write_text("x guard_daemon start\nx guard running, pid 1\n{\"phase\": \"open\"}\n")
    (tmp_path / "logs" / f"selftest-{day}.txt").write_text("....\n119 passed in 4.1s\n")
    monkeypatch.setattr(SP, "kite", lambda: {"fresh": True, "until": "21 Nov 07:30"})
    g = SP.guard(db, now_ms=at(12, 0))
    assert [p["symbol"] for p in g["open"]] == ["SBIN"] and g["realized_today"] == "40"
    s = {"at": "now", "kite": SP.kite(), "guard": g, "daemon": SP.daemon_log(day, tmp_path), "selftest": SP.selftest(day, tmp_path)}
    page = SP.render(s)
    assert "Running" in page and "+₹40" in page and "119 passed" in page and "<script>" not in page
    assert "refresh" in page and SP.summary(s).startswith("Paper guard running · 1 closed today (+₹40) · 1 open")
    st.close()


def test_states_from_the_daemon_log(tmp_path):
    (tmp_path / "logs").mkdir()
    day = "2026-11-20"
    p = tmp_path / "logs" / f"guard-{day}.log"
    p.write_text("a guard_daemon start\n")
    assert SP.daemon_log(day, tmp_path)["state"] == "waiting for Kite login"
    p.write_text("a guard_daemon start\nb guard running, pid 2\nc guard EXITED UNEXPECTEDLY (code 1, crash 1)\n")
    d = SP.daemon_log(day, tmp_path)
    assert d["state"] == "crashed, restarting" and d["crashes"] == 1
    p.write_text("a guard_daemon start\nb SELF-TEST FAILED: guard not started\n")
    assert "self-test failed" in SP.daemon_log(day, tmp_path)["state"]


def test_missing_store_is_not_an_error(tmp_path):
    g = SP.guard(tmp_path / "nope.db")
    assert g["open"] == [] and "hasn't run" in g["note"]
