from __future__ import annotations

import random
from decimal import Decimal

import pytest

from src.live.profit_guard.config import load_config
from src.live.profit_guard.models import (
    ExitAll,
    ExitPosition,
    ExitReason,
    MoveStop,
    PortfolioState,
    Side,
    State,
)
from src.live.profit_guard.rules import (
    Signals,
    initial_levels,
    portfolio_mtm,
    start_of_day,
    update_portfolio,
    update_position,
)
from tests.profit_guard.conftest import DAY, NEXT, at, long_plan, short_opt, tk

D = Decimal


def exits(actions):
    return [a for a in actions if isinstance(a, ExitPosition)]


# ------------------------------------------------------------------ portfolio guard (layer 2)
def run_path(cfg, path):
    st = PortfolioState(trade_date=DAY.isoformat())
    log = []
    for m in path:
        st, acts = update_portfolio(st, D(str(m)), cfg)
        log.append((m, st, [a for a in acts if isinstance(a, ExitAll)]))
    return st, log


def test_reddit_replay_tiered(cfg):
    path = [-7842.75, -2000, 3000, 5000, 10670.25, 9000, 7000, 6935.5, 1708.75]
    st, log = run_path(cfg, path)
    by = {m: (s, e) for m, s, e in log}
    assert not by[3000][0].armed
    assert by[5000][0].armed
    assert by[10670.25][0].floor == D("10670.25") * D("0.65")  # 6935.6625
    assert by[7000][1] == []
    assert [e.reason for e in by[6935.5][1]] == [ExitReason.PROFIT_LOCK]
    assert by[1708.75][1] == []  # sticky: nothing after the lock
    assert st.lock_hit


def test_loss_cap_boundary(cfg):
    st, acts = update_portfolio(PortfolioState("d"), D("-14999"), cfg)
    assert not st.loss_cap_hit and not acts
    st, acts = update_portfolio(st, D("-15000"), cfg)
    assert st.loss_cap_hit
    assert [a.reason for a in acts if isinstance(a, ExitAll)] == [ExitReason.LOSS_CAP]
    st, acts = update_portfolio(st, D("-20000"), cfg)
    assert acts == []


def test_floor_never_falls(cfg):
    rng = random.Random(3)
    st = PortfolioState("d")
    last_floor = None
    for _ in range(2000):
        st, _ = update_portfolio(st, D(rng.randint(-5000, 30000)), cfg)
        if st.lock_hit or st.loss_cap_hit:
            st = PortfolioState("d", peak_mtm=st.peak_mtm, floor=st.floor, armed=st.armed)
        if last_floor is not None and st.floor is not None:
            assert st.floor >= last_floor
        last_floor = st.floor


def test_lock_fixed():
    cfg = load_config(overrides={"layer2": {"mode": "lock_fixed"}})
    st, log = run_path(cfg, [4000, 6000, 3000, 2500])
    assert not log[0][1].armed
    assert log[1][1].floor == D("2500")
    assert log[2][2] == []
    assert [e.reason for e in log[3][2]] == [ExitReason.PROFIT_LOCK]


def test_lock_fixed_floor_does_not_move_with_peak():
    cfg = load_config(overrides={"layer2": {"mode": "lock_fixed"}})
    st, log = run_path(cfg, [6000, 20000])
    assert st.floor == D("2500")


def test_lock_and_trail():
    cfg = load_config(overrides={"layer2": {"mode": "lock_and_trail"}})
    st, log = run_path(cfg, [5000, 10000, 5600, 5500])
    assert log[0][1].floor == D("2500")
    assert log[1][1].floor == D("5500")  # 2 steps of 2500 above 5000 -> 2500 + 2*1500
    assert log[2][2] == []
    assert [e.reason for e in log[3][2]] == [ExitReason.PROFIT_LOCK]


def test_off_mode_only_loss_cap():
    cfg = load_config(overrides={"layer2": {"mode": "off"}})
    st, log = run_path(cfg, [20000, 100])
    assert all(not e for *_, e in log)
    st, acts = update_portfolio(st, D("-15000"), cfg)
    assert st.loss_cap_hit


def test_start_of_day_halves_size_after_losing_streak(cfg):
    st = None
    for pnl in (-1, -1, -1):
        st = start_of_day(st, "d", cfg, D(pnl))
    assert st.consecutive_losing_days == 3 and st.size_multiplier == D("0.5")
    st = start_of_day(st, "d2", cfg, D("10"))
    assert st.consecutive_losing_days == 0 and st.size_multiplier == D("1")
    assert st.peak_mtm == 0 and st.floor is None and not st.lock_hit


# ------------------------------------------------------------------ per-position (layer 1)
def test_initial_levels_long(cfg):
    stop, t1, target, one_r = initial_levels(Side.LONG, D("500"), D("2"), cfg, swing_level=D("498"))
    assert stop == D("498.00")  # swing tighter than 1.5 ATR (497) and >= 0.5 ATR away
    assert one_r == D("2") and target == D("504.00") and t1 == D("502")


def test_initial_levels_min_distance(cfg):
    stop, *_ = initial_levels(Side.LONG, D("500"), D("2"), cfg, swing_level=D("499.8"))
    assert stop == D("499.00")  # pushed back to 0.5 ATR


def test_initial_levels_short_rounds_up(cfg):
    stop, _, target, one_r = initial_levels(Side.SHORT, D("120"), D("3.33"), cfg)
    assert stop == D("125.00")  # 120 + 4.995 -> 125.00 (rounded away from entry)


def test_breakeven_at_one_r(cfg):
    p = long_plan(atr=None)
    p, acts = update_position(p, tk("STOCKA", "504.95", at(10, 5)), cfg, at(10, 5))
    assert p.state is State.OPEN and acts == []
    p, acts = update_position(p, tk("STOCKA", "505", at(10, 6)), cfg, at(10, 6))
    assert p.state is State.BREAKEVEN and p.stop == D("500.25")
    assert [type(a) for a in acts] == [MoveStop]


def test_chandelier_trail_and_trail_exit(cfg):
    p = long_plan()  # atr 1 -> trail 3 below the high
    p, _ = update_position(p, tk("STOCKA", "506", at(10, 5)), cfg, at(10, 5))
    assert p.state is State.TRAILING and p.stop == D("503")
    p, acts = update_position(p, tk("STOCKA", "509", at(10, 6)), cfg, at(10, 6))
    assert p.stop == D("506")
    p, acts = update_position(p, tk("STOCKA", "505.90", at(10, 7)), cfg, at(10, 7))
    assert [a.reason for a in exits(acts)] == [ExitReason.TRAIL]
    # price traded through the 506 stop to 505.90: book the worse price, not the stop
    assert p.realized == D("2950.00")


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_long_stop_never_loosens(cfg, seed):
    rng = random.Random(seed)
    p = long_plan(target=None)
    price = D("500")
    for i in range(1000):
        price = max(D("1"), price + D(rng.randint(-60, 70)) / 100)
        before = p.stop
        p, acts = update_position(p, tk("STOCKA", price, at(10, 0) + i * 1000), cfg, at(10, 0) + i * 1000)
        assert p.stop >= before
        if p.state is State.EXITED:
            break


@pytest.mark.parametrize("seed", [4, 5])
def test_short_stop_never_moves_up(cfg, seed):
    rng = random.Random(seed)
    c = cfg.for_strategy("intraday_opt_buy")
    p = short_opt(target=None)
    price = D("120")
    for i in range(1000):
        price = max(D("1"), price + D(rng.randint(-80, 70)) / 100)
        before = p.stop
        p, _ = update_position(p, tk(p.symbol, price, at(10, 0) + i * 1000), c, at(10, 0) + i * 1000)
        assert p.stop <= before
        if p.state is State.EXITED:
            break


def test_scale_out_once(cfg):
    p = long_plan(target1=D("505"), target=D("520"))
    p, acts = update_position(p, tk("STOCKA", "505.20", at(10, 5), hi="505.50"), cfg, at(10, 5))
    part = [a for a in exits(acts) if a.reason is ExitReason.PARTIAL]
    assert len(part) == 1 and part[0].qty == 250 and p.qty_open == 250 and p.partial_done
    p, acts = update_position(p, tk("STOCKA", "505.60", at(10, 6)), cfg, at(10, 6))
    assert not [a for a in exits(acts) if a.reason is ExitReason.PARTIAL]


def test_time_stop(cfg):
    p = long_plan()
    acts = []
    for i in range(6):
        p, acts = update_position(p, tk("STOCKA", "501.50", at(10, 10 + i), bar=True), cfg, at(10, 10 + i))
    assert [a.reason for a in exits(acts)] == [ExitReason.TIME]


def test_no_time_stop_when_working(cfg):
    p = long_plan()
    for i in range(8):
        p, acts = update_position(p, tk("STOCKA", "503", at(10, 10 + i), bar=True), cfg, at(10, 10 + i))
        assert not exits(acts)


def test_square_off_beats_everything(cfg):
    p = long_plan()
    p, acts = update_position(p, tk("STOCKA", "509.99", at(15, 16)), cfg, at(15, 16), Signals(opposite_signal=True))
    assert [a.reason for a in exits(acts)] == [ExitReason.SESSION_CLOSE]


def test_carry_skips_entry_day_square_off_then_gap_exit(cfg):
    p = long_plan(opened_ms=at(15, 20), carry=True, target=None)
    p, acts = update_position(p, tk("STOCKA", "500", at(15, 25)), cfg, at(15, 25))
    assert not exits(acts)
    nxt = at(9, 15, 2, d=NEXT)
    p, acts = update_position(p, tk("STOCKA", "490", nxt), cfg, nxt)
    assert [a.reason for a in exits(acts)] == [ExitReason.GAP]
    assert p.exit_price == D("490")


def test_expiry_exit_for_options_only():
    c = load_config(overrides={"layer3": {"weekly_expiry_weekday": DAY.weekday()}})
    o = short_opt()
    o, acts = update_position(o, tk(o.symbol, "100", at(14, 31)), c, at(14, 31))
    assert [a.reason for a in exits(acts)] == [ExitReason.EVENT]
    s = long_plan()
    s, acts = update_position(s, tk("STOCKA", "501", at(14, 31)), c, at(14, 31))
    assert not exits(acts)


def test_event_exit(cfg):
    p = long_plan()
    p, acts = update_position(p, tk("STOCKA", "501", at(11, 0)), cfg, at(11, 0), Signals(event_in_minutes=10))
    assert [a.reason for a in exits(acts)] == [ExitReason.EVENT]


def test_no_decisions_in_preopen_or_closed(cfg):
    p = long_plan()
    for ts in (at(9, 5), at(16, 0)):
        p2, acts = update_position(p, tk("STOCKA", "400", ts), cfg, ts)
        assert acts == [] and p2 == p


def test_short_option_stop_and_target(cfg):
    o = short_opt()
    o2, acts = update_position(o, tk(o.symbol, "157", at(11, 0)), cfg, at(11, 0))
    assert [a.reason for a in exits(acts)] == [ExitReason.STOP]
    o3, acts = update_position(o, tk(o.symbol, "59", at(11, 0)), cfg, at(11, 0))
    assert [a.reason for a in exits(acts)] == [ExitReason.TARGET]


def test_portfolio_mtm(cfg):
    a = long_plan(stop=D("503.40"))
    b = short_opt()
    m = portfolio_mtm([a, b], {"STOCKA": D("506.20"), b.symbol: D("96.50")})
    assert m == D("3100.00") + D("1762.50")


def test_portfolio_mtm_ignores_previous_days(cfg):
    old = long_plan(id="OLD").with_(state=State.EXITED, realized=D("7000"), exit_ms=at(12, 0), qty_open=0)
    today = long_plan(id="NEW", symbol="STOCKB", opened_ms=at(9, 30, d=NEXT))
    m = portfolio_mtm([old, today], {"STOCKB": D("490")}, day_start_ms=at(9, 15, d=NEXT))
    assert m == D("-5000")  # yesterday's +7000 must not hide today's loss
