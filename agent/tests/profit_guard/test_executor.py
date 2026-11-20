from __future__ import annotations

from decimal import Decimal

import pytest

from src.live import halt as halt_mod
from src.live.profit_guard.executor import Executor, Throttle
from src.live.profit_guard.models import (
    Alert,
    ExitAll,
    ExitPosition,
    ExitReason,
    MoveStop,
)
from src.live.profit_guard.paper_broker import PaperBroker
from src.live.profit_guard.store import GuardStore
from tests.profit_guard.conftest import at, long_plan, short_opt

D = Decimal


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.t += d


@pytest.fixture
def env(tmp_path, cfg):
    clock = FakeClock()
    store = GuardStore(tmp_path / "g.db")
    broker = PaperBroker()
    ex = Executor(store, broker, cfg, broker_key="zerodha", throttle=Throttle(8, clock, clock.sleep))
    yield store, broker, ex, clock
    store.close()


def seed(broker, symbol, qty, price):
    broker.seed_position(symbol, qty, price)
    broker.on_tick(symbol, price, 0)


def test_exit_all_cancels_before_exits(env):
    store, broker, ex, _ = env
    plans = {}
    for i, sym in enumerate(["S1", "S2", "S3"]):
        seed(broker, sym, 10, "100")
        plans[f"P{i}"] = long_plan(id=f"P{i}", symbol=sym, qty=10)
    broker.submit_limit("S1", "buy", 1, D("90"))
    broker.submit_limit("S2", "buy", 1, D("90"))
    broker.calls.clear()
    rep = ex.execute([ExitAll("account", ExitReason.PROFIT_LOCK)], plans, at(11, 0))
    kinds = [k for k, _ in broker.calls]
    assert kinds == ["cancel", "cancel", "submit", "submit", "submit"]
    assert len(rep.sent) == 3 and not halt_mod.halt_flag_set("zerodha")


def test_mass_flatten_is_throttled(env):
    store, broker, ex, clock = env
    plans = {}
    for i in range(20):
        sym = f"S{i}"
        seed(broker, sym, 5, "100")
        plans[sym] = long_plan(id=sym, symbol=sym, qty=5)
    ex.execute([ExitAll("account", ExitReason.LOSS_CAP)], plans, at(11, 0))
    h = ex.throttle.history
    assert len(h) == 20
    for i, t0 in enumerate(h):
        assert sum(1 for t in h[i:] if t - t0 < 1.0) <= 8
    assert halt_mod.halt_flag_set("zerodha")  # loss cap trips HALT


def test_unfilled_exit_is_repriced_and_alerts(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 500, "500")
    plan = long_plan()
    ex.execute([ExitPosition("A", 500, ExitReason.STOP, D("495"))], {"A": plan}, at(11, 0))
    broker.on_tick("STOCKA", "480", at(11, 0, 1))  # price collapses below our limit: no fill
    assert broker.position_qty("STOCKA") == 500
    rep = ex.poll_exits(at(11, 0, 6))  # > 5s: cancel + re-price off fresh LTP
    assert len(rep.sent) == 1 and rep.sent[0]["attempt"] == 2
    broker.on_tick("STOCKA", "479", at(11, 0, 7))
    ex.poll_exits(at(11, 0, 8))
    assert broker.position_qty("STOCKA") == 0
    assert store.history()[0]["reason"] == "STOP"
    assert ex.pending == {}


def test_stuck_exit_alerts_after_30s(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 500, "500")
    ex.execute([ExitPosition("A", 500, ExitReason.STOP, D("495"))], {"A": long_plan()}, at(11, 0))
    alerts = []
    for s in range(1, 40):
        broker.on_tick("STOCKA", str(480 - 5 * s), at(11, 0, s))  # falls faster than any re-priced limit
        alerts += [a for a in ex.poll_exits(at(11, 0, s)).alerts if a.level == "critical"]
    assert len(alerts) == 1 and "still not filled" in alerts[0].text


def test_failed_send_is_retried(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 500, "500")
    broker.fail_next_submits = 1
    rep = ex.execute([ExitPosition("A", 500, ExitReason.STOP, D("495"))], {"A": long_plan()}, at(11, 0))
    assert rep.sent == [] and any("will retry" in a.text for a in rep.alerts)
    intents = store.intents()
    assert intents[0]["status"] == "failed"  # write-ahead: recorded before the broker call
    rep = ex.poll_exits(at(11, 0, 1))
    assert len(rep.sent) == 1


def test_partial_exit_larger_than_holding_refused(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 100, "500")
    rep = ex.execute([ExitPosition("A", 250, ExitReason.PARTIAL, D("505"))], {"A": long_plan()}, at(11, 0))
    assert rep.sent == [] and rep.refused
    assert any(a.level == "critical" for a in rep.alerts)
    assert [k for k, _ in broker.calls] == []


def test_exit_when_nothing_held_refused(env):
    store, broker, ex, _ = env
    rep = ex.execute([ExitPosition("A", 500, ExitReason.STOP, D("495"))], {"A": long_plan()}, at(11, 0))
    assert rep.sent == [] and rep.refused


def test_halt_total_freeze_blocks_everything_but_loss_cap(tmp_path):
    from src.live.profit_guard.config import load_config

    cfg = load_config(overrides={"executor": {"exits_when_halted": False}})
    clock = FakeClock()
    with GuardStore(tmp_path / "f.db") as store:
        broker = PaperBroker()
        ex = Executor(store, broker, cfg, throttle=Throttle(8, clock, clock.sleep))
        seed(broker, "STOCKA", 500, "500")
        halt_mod.trip_halt(by="test", reason="manual", broker="zerodha")
        rep = ex.execute([MoveStop("A", D("501"), "trail"), ExitPosition("A", 500, ExitReason.STOP, D("495"))],
                         {"A": long_plan()}, at(11, 0))
        assert len(rep.refused) == 2 and rep.sent == []
        rep = ex.execute([ExitAll("account", ExitReason.LOSS_CAP)], {"A": long_plan()}, at(11, 1))
        assert len(rep.sent) == 1


def test_halt_default_still_lets_protective_exits_out(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 500, "500")
    halt_mod.trip_halt(by="test", reason="manual", broker="zerodha")
    rep = ex.execute([MoveStop("A", D("501"), "trail"), ExitPosition("A", 500, ExitReason.STOP, D("495"))],
                     {"A": long_plan()}, at(11, 0))
    assert len(rep.refused) == 1  # the stop move (not an exit) is refused
    assert len(rep.sent) == 1  # the protective stop exit goes out


def test_fill_price_booked_and_exit_all_closes_plans(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 500, "500")
    plan = long_plan()
    store.save_plan(plan, at(10, 0))
    ex.execute([ExitAll("account", ExitReason.PROFIT_LOCK)], {"A": plan}, at(11, 0))
    broker.on_tick("STOCKA", "500", at(11, 0, 1))
    ex.poll_exits(at(11, 0, 2))
    p = store.get_plan("A")
    fill = D(str(broker.fills[-1]["price"]))
    assert p.state.value == "EXITED" and p.exit_reason is ExitReason.PROFIT_LOCK and p.qty_open == 0
    cost = D(str(broker.fills[-1]["cost"]))
    assert p.realized == (fill - D("500")) * 500 - cost  # booked net of charges


def test_cancelled_booked_exit_is_unbooked_not_double_counted(env):
    from src.live.profit_guard.rules import update_position
    from tests.profit_guard.conftest import tk

    store, broker, ex, _ = env
    cfg = ex.cfg
    seed(broker, "STOCKA", 500, "500")
    plan = long_plan()
    broker.on_tick("STOCKA", "494", at(11, 0))
    plan, acts = update_position(plan, tk("STOCKA", "494", at(11, 0)), cfg, at(11, 0))  # stop hit: rules book it
    store.save_plan(plan, at(11, 0))
    ex.execute(acts, {"A": plan}, at(11, 0))
    # same moment the portfolio lock flattens everything: the rules' exit order is cancelled and replaced
    ex.execute([ExitAll("account", ExitReason.PROFIT_LOCK)], {"A": store.get_plan("A")}, at(11, 0, 1))
    broker.on_tick("STOCKA", "494", at(11, 0, 2))
    ex.poll_exits(at(11, 0, 3))
    p = store.get_plan("A")
    fills = [f for f in broker.fills if f["symbol"] == "STOCKA"]
    assert len(fills) == 1 and broker.position_qty("STOCKA") == 0
    assert p.realized == (D(str(fills[0]["price"])) - D("500")) * 500 - D(str(fills[0]["cost"]))  # once, at the real fill, net of charges


def test_paper_fill_never_on_placing_tick():
    b = PaperBroker()
    b.on_tick("X", "100", 1)
    o = b.submit_limit("X", "buy", 1, D("101"))
    assert b.order(o["order_id"])["status"] == "OPEN"
    b.on_tick("X", "100", 2)
    assert b.order(o["order_id"])["status"] == "FILLED"


def test_option_market_rejected_but_flatten_uses_limit(env):
    store, broker, ex, _ = env
    assert broker.submit_market("NIFTY24500CE", "buy", 75)["status"] == "REJECTED"
    seed(broker, "NIFTY24500CE", -75, "120")  # short option
    rep = ex.execute([ExitAll("account", ExitReason.PROFIT_LOCK)], {"B": short_opt()}, at(11, 0))
    assert rep.sent and rep.sent[0]["side"] == "buy" and D(rep.sent[0]["price"]) > D("120")
    broker.on_tick("NIFTY24500CE", "119", at(11, 0, 1))
    ex.poll_exits(at(11, 0, 2))
    assert broker.position_qty("NIFTY24500CE") == 0


def test_gtt_stop_follows_trail(env):
    store, broker, ex, _ = env
    seed(broker, "STOCKA", 500, "500")
    g = broker.place_gtt_oco("STOCKA", "sell", 500, D("495"), D("494"), D("505"), D("505"))
    plan = long_plan(gtt_id=g["gtt_id"])
    ex.execute([MoveStop("A", D("501"), "trail")], {"A": plan}, at(11, 0))
    assert broker.gtts()[0]["stop_trigger"] == D("501")


def test_alerts_are_audited(env):
    store, broker, ex, _ = env
    ex.execute([Alert("info", "hello")], {}, at(11, 0))
    assert store.read_audit()[-1]["text"] == "hello"
