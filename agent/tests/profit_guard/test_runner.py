"""P6: the guard loop end to end (paper broker, scripted prices, fake clock)."""

from __future__ import annotations

import json
from datetime import date, time
from decimal import Decimal

import pytest

from src.live import halt as halt_mod
from src.live.profit_guard import session
from src.live.profit_guard.alerts import AlertSender
from src.live.profit_guard.config import load_config
from src.live.profit_guard.executor import Executor
from src.live.profit_guard.models import Alert, PositionPlan, Side, State
from src.live.profit_guard.paper_broker import PaperBroker
from src.live.profit_guard.reconcile import reconcile
from src.live.profit_guard.runner import GuardRunner, queue_entry, readiness
from src.live.profit_guard.store import GuardStore

D = Decimal
DAY = date(2026, 11, 18)
NEXT = date(2026, 11, 19)


def at(h, m, s=0, d=DAY):
    return session.ist_ms(d, time(h, m, s))


class Prices:
    def __init__(self):
        self.now: dict[str, Decimal] = {}

    def __call__(self, symbols):
        return {s: self.now[s] for s in symbols if s in self.now}


class Quiet(AlertSender):
    def __init__(self):
        super().__init__(token="", chat_id="")
        self.log = []

    def send(self, alerts):
        self.log += [a.text for a in alerts]
        return 0


def make(tmp_path, cfg=None, fallback=None, holidays=None):
    prices = Prices()
    r = GuardRunner(cfg or load_config(), GuardStore(tmp_path / "g.db"), PaperBroker(), prices, fallback,
                    alerts=Quiet(), holidays=holidays or set(), broker_state=tmp_path / "pb.json",
                    inbox=tmp_path / "inbox.jsonl")
    return r, prices


def test_closed_market_does_nothing(tmp_path):
    r, p = make(tmp_path)
    sat = session.ist_ms(date(2026, 11, 21), time(11, 0))
    assert r.tick(sat)["phase"] == "closed"
    r.store.close()


def test_holiday_is_closed(tmp_path):
    r, p = make(tmp_path, holidays={DAY})
    assert r.tick(at(11, 0))["phase"] == "closed"
    r.store.close()


def test_paper_entry_then_stop_exit(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    out = r.tick(at(10, 0))
    assert any("Paper entry sent" in a for a in out["alerts"])
    r.tick(at(10, 0, 10))  # fills on the next tick
    plans = r.store.open_plans()
    assert len(plans) == 1 and plans[0].stop < D("800") and r.broker.position_qty("SBIN") == 10
    stop = plans[0].stop
    p.now["SBIN"] = stop - 1
    r.tick(at(10, 1))      # stop hit -> exit sent
    r.tick(at(10, 1, 10))  # exit fills
    assert r.broker.position_qty("SBIN") == 0
    assert r.store.all_plans()[0].state is State.EXITED and r.store.all_plans()[0].realized < 0
    r.store.close()


def test_one_plan_per_stock(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    inbox = tmp_path / "inbox.jsonl"
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10}, inbox)
    r.tick(at(10, 0)); r.tick(at(10, 0, 10))
    queue_entry({"strategy": "btst", "symbol": "SBIN", "qty": 5}, inbox)
    out = r.tick(at(10, 1))
    assert any("one plan per stock" in a for a in out["alerts"])
    r.store.close()


def test_btst_entry_gets_gtt_and_survives_square_off(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "btst", "symbol": "SBIN", "qty": 10, "carry": True, "target_pct": 1}, tmp_path / "inbox.jsonl")
    r.tick(at(15, 20)); r.tick(at(15, 20, 10))
    plan = r.store.open_plans()[0]
    assert plan.carry and plan.gtt_id and plan.target is not None
    out = r.tick(at(15, 26))  # 15:25 GTT check: present, so no alert
    assert not any("NO active GTT" in a for a in out["alerts"])
    assert r.store.open_plans()  # not squared off on entry day
    r.store.close()


def test_stale_prices_block_decisions_and_entries(tmp_path):
    r, p = make(tmp_path)
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10}, tmp_path / "inbox.jsonl")
    out = r.tick(at(10, 0))
    assert any("No fresh price" in a for a in out["alerts"])
    assert any("refused" in a for a in out["alerts"])
    assert r.store.open_plans() == []
    r.store.close()


def test_rest_fallback_used(tmp_path):
    r, p = make(tmp_path, fallback=lambda syms: {s: D("800") for s in syms})
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10}, tmp_path / "inbox.jsonl")
    out = r.tick(at(10, 0))
    assert any("Paper entry sent" in a for a in out["alerts"])
    r.store.close()


def test_restart_keeps_positions_and_rebuilds_inflight_exit(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    queue_entry({"strategy": "swing", "symbol": "SBIN", "qty": 10, "atr": 8}, tmp_path / "inbox.jsonl")
    r.tick(at(10, 0)); r.tick(at(10, 0, 10))
    stop = r.store.open_plans()[0].stop
    p.now["SBIN"] = stop - D("40")  # crash right after the exit is sent (price far below: no fill this tick)
    r.tick(at(10, 1))
    assert len(r.executor.pending) == 1
    r.store.close()
    # --- restart
    broker = PaperBroker.from_state(json.loads((tmp_path / "pb.json").read_text()))
    r2 = GuardRunner(load_config(), GuardStore(tmp_path / "g.db"), broker, p, None, alerts=Quiet(),
                     broker_state=tmp_path / "pb.json", inbox=tmp_path / "inbox.jsonl")
    r2._inbox_offset = 10**9
    r2.start(at(10, 2))
    assert len(r2.executor.pending) == 1  # re-tracked, not re-sent
    for i in range(4):
        r2.tick(at(10, 2, 10 * (i + 1)))
    assert broker.position_qty("SBIN") == 0  # closed once, never oversold
    assert all(f["side"] == "sell" for f in broker.fills if f.get("tag", "").startswith("exit"))
    assert sum(f["qty"] for f in broker.fills if f["side"] == "sell") == 10
    r2.store.close()


def test_missing_gtt_alerts_at_close(tmp_path):
    r, p = make(tmp_path)
    p.now["SBIN"] = D("800")
    r.broker.seed_position("SBIN", 10, "800")
    r.broker.on_tick("SBIN", D("800"), at(15, 0))
    plan = PositionPlan(id="b1", strategy="btst", symbol="SBIN", side=Side.LONG, qty=10, entry=D("800"),
                        initial_stop=D("784"), stop=D("784"), target=D("808"), one_r=D("16"),
                        opened_ms=at(15, 20), carry=True)
    r.store.save_plan(plan, at(15, 20))
    out = r.tick(at(15, 26))
    assert any("NO active GTT" in a for a in out["alerts"])
    r.store.close()


def test_reconcile_cases(tmp_path):
    cfg = load_config()
    with GuardStore(tmp_path / "r.db") as store:
        b = PaperBroker()
        ex = Executor(store, b, cfg)
        b.seed_position("ORPH", 5, "100"); b.on_tick("ORPH", D("100"), 1)
        gone = PositionPlan(id="g", strategy="swing", symbol="GONE", side=Side.LONG, qty=5, entry=D("10"),
                            initial_stop=D("9"), stop=D("9"), target=None, one_r=D("1"), opened_ms=1)
        store.save_plan(gone, 1)
        alerts = reconcile(store, b, ex, cfg, at(10, 0))
        texts = " | ".join(a.text for a in alerts)
        assert "Orphan ORPH" in texts and "EXTERNAL" in texts
        assert store.get_plan("g").state is State.EXITED
        mism = PositionPlan(id="m", strategy="swing", symbol="MISM", side=Side.LONG, qty=10, entry=D("10"),
                            initial_stop=D("9"), stop=D("9"), target=None, one_r=D("1"), opened_ms=1)
        store.save_plan(mism, 2)
        b.seed_position("MISM", 7, "10"); b.on_tick("MISM", D("10"), 2)
        alerts = reconcile(store, b, ex, cfg, at(10, 1))
        assert any("HALT" in a.text for a in alerts) and halt_mod.halt_flag_set("zerodha")


def test_telegram_rate_limit_and_disabled():
    sent = []
    clock = [0.0]
    s = AlertSender("tok", "chat", per_minute=3, post=lambda u, p: sent.append(p), clock=lambda: clock[0])
    assert s.send([Alert("warn", f"a{i}") for i in range(5)]) == 3 and s.dropped == 2
    clock[0] = 61
    assert s.send([Alert("warn", "later")]) == 1
    off = AlertSender("", "", post=lambda u, p: sent.append("x"))
    assert off.send([Alert("critical", "nobody")]) == 0


def test_readiness_flags_placeholders():
    w = readiness(load_config())
    assert any("capital_inr" in x for x in w) and any("weekly_expiry_weekday" in x for x in w)
    ok = load_config(overrides={"capital_inr": 500000, "layer3": {"weekly_expiry_weekday": 1}})
    assert readiness(ok) == []
