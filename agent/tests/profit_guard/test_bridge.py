"""Bridge: the overnight pick and practice trades reach the guard once, sized and with their own stop."""

from __future__ import annotations

import json
from decimal import Decimal as D

from src.live.profit_guard import bridge as B
from src.live.profit_guard.config import load_config
from src.live.profit_guard.models import State
from tests.profit_guard.test_runner import at, make

S = B.settings({})
CAP = D("1000000")
PICK = {"date": "2026-11-20", "status": "picked", "pick": {"symbol": "APOLLOHOSP", "entry": 9190.0, "target": 9281.9,
                                                          "stop": 9006.2, "gain_pct": 5.17}}


def test_size_by_risk_and_capped_by_value():
    assert B.size(CAP, 100, 98, S) == 2000  # risk cap: 5000 / 2 = 2500, value cap: 200000 / 100 = 2000
    assert B.size(CAP, 9190, 9006.2, S) == 21  # 5000 / 183.8 = 27; 200000 / 9190 = 21
    assert B.size(CAP, 100, 100, S) == 0


def test_btst_pick_becomes_a_carry_entry_with_its_stop_and_no_plus_one_target():
    reqs = B.btst_requests("2026-11-20", CAP, S, load_day=lambda d: PICK)
    (key, req), = reqs
    assert key == "btst:2026-11-20:APOLLOHOSP"
    assert req["carry"] and req["strategy"] == "btst" and req["stop"] == 9006.2 and req["qty"] == 21
    assert "target" not in req and req["target_pct"] == 5.0  # backstop only: the +1% exit lost money in P7
    assert B.btst_requests("2026-11-20", CAP, S, load_day=lambda d: {**PICK, "status": "skipped"}) == []
    assert B.btst_requests("2026-11-20", CAP, S, load_day=lambda d: None) == []


def test_ledger_mirrors_open_trades_only():
    rows = [
        {"id": "t1", "symbol": "SBIN", "side": "LONG", "status": "OPEN", "qty": 50, "entry": 800, "stop": 780,
         "stop_now": 790, "target": 840, "current_price": 810, "_where": "hosted"},
        {"id": "t2", "symbol": "TCS", "side": "LONG", "status": "CLOSED", "qty": 5, "entry": 4000, "stop": 3900, "target": 4200},
        {"id": "t3", "symbol": "INFY", "side": "SHORT", "status": "OPEN", "qty": 10, "entry": 1500, "stop": 1530,
         "target": 1440, "strategy_id": "lab_x", "strategy_name": "RSI dip"},
    ]
    out = dict(B.ledger_requests(CAP, S, rows=rows))
    assert set(out) == {"ledger:hosted:t1", "ledger:local:t3"}
    assert out["ledger:hosted:t1"]["stop"] == 790 and out["ledger:hosted:t1"]["target"] == 840
    assert out["ledger:local:t3"]["strategy"] == "lab" and out["ledger:local:t3"]["source"] == "RSI dip"


def test_bridge_queues_once_across_restarts_and_skips_held():
    meta: dict[str, str] = {}
    queued: list[dict] = []
    held = {"TCS"}

    def mk():
        return B.Bridge({"capital_inr": 1000000}, meta.get, meta.__setitem__, queued.append, lambda: set(held),
                        btst_source=lambda today, cap, s: B.btst_requests(today, cap, s, load_day=lambda d: PICK),
                        ledger_source=lambda cap, s: [("ledger:local:x", {"strategy": "signal_board", "symbol": "TCS",
                                                                           "side": "LONG", "qty": 1, "carry": True,
                                                                           "stop": 1, "source": "signal board"})])

    b = mk()
    assert b.poll(at(9, 5), "preopen", "2026-11-20") == []  # nothing before the open
    got = b.poll(at(15, 21), "square_off", "2026-11-20")
    assert got == ["overnight pick: LONG 21 APOLLOHOSP"] and len(queued) == 1  # ledger ideas only in normal hours
    assert mk().poll(at(15, 23), "square_off", "2026-11-20") == []  # a restart remembers what was handed over
    b2 = mk()
    b2.poll(at(10, 0), "open", "2026-11-20")
    assert len(queued) == 1 and "already held" in json.loads(meta["bridge_done"])["ledger:local:x"]


def test_runner_buys_the_overnight_pick_with_the_picks_stop(tmp_path, monkeypatch):
    r, p = make(tmp_path)
    monkeypatch.setattr(B, "btst_requests", lambda today, cap, s, load_day=None: [
        ("btst:x:SBIN", {"strategy": "btst", "symbol": "SBIN", "side": "LONG", "qty": 10, "carry": True,
                         "stop": 780.0, "target_pct": 5.0, "source": "overnight pick"})])
    r.bridge.btst_source = B.btst_requests
    r.bridge.raw_cfg["bridge"]["btst"] = True  # off in the shipped yaml since the audit; the mechanism still works
    p.now["SBIN"] = D("800")
    r.tick(at(15, 21))  # bridge queues
    r.tick(at(15, 21, 10))  # runner reads the inbox, sends the paper entry
    r.tick(at(15, 21, 20))  # fill
    plans = r.store.open_plans()
    assert len(plans) == 1
    pl = plans[0]
    assert pl.strategy == "btst" and pl.carry and pl.stop == D("780") and pl.state is State.OPEN
    assert pl.target is not None and pl.target > D("830")  # +5% backstop, not the +1% exit
    assert pl.gtt_id  # overnight GTT backstop placed
    r.store.close()


def test_runner_refuses_a_handover_already_past_its_stop(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("770")
    from src.live.profit_guard.runner import queue_entry

    queue_entry({"strategy": "signal_board", "symbol": "SBIN", "side": "LONG", "qty": 5, "carry": True, "stop": 780},
                tmp_path / "inbox.jsonl")
    out = r.tick(at(10, 0))
    r.tick(at(10, 0, 10))
    assert any("already past its stop" in a for a in out["alerts"]) and r.store.open_plans() == []
    r.store.close()


def test_yaml_has_bridge_and_strategy_overrides():
    raw = load_config().raw
    assert raw["bridge"]["enabled"] and raw["bridge"]["signal_board"]
    assert raw["bridge"]["btst"] is False  # off since the 21 Nov 2026 audit
    sb = load_config().for_strategy("signal_board")
    assert sb.l1["time_stop_bars"] == 0 and sb.l1["trail"] == "none" and sb.l1["scale_out_frac"] == 0
    assert sb.l2["mode"] == "off" and float(sb.l1["breakeven_at_r"]) > 100
