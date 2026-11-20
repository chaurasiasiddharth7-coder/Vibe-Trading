"""P7 harness: the exit variants run through the live rules and behave as their names say."""

from datetime import date

import pytest

from src.live.profit_guard import backtest_btst as B
from src.live.profit_guard.config import load_config


@pytest.fixture(scope="module")
def base():
    return load_config()


def _t(o, h, l, c, entry=100.0):
    return B.Trade(date(2025, 3, 3), date(2025, 3, 4), "TEST", entry, o, h, l, c)


V = {v.key: v for v in B.VARIANTS}


def test_target_and_stop_order_by_path(base):
    t = _t(100.5, 101.5, 97.5, 99.0)  # both +1% and -2% inside the bar
    worst = B.simulate(t, V["A"], base, path="worst")
    best = B.simulate(t, V["A"], base, path="best")
    assert worst["reason"] == "STOP" and best["reason"] == "TARGET"
    assert worst["gross"] == pytest.approx(-2.0, abs=0.06)
    assert best["gross"] == pytest.approx(1.0, abs=0.06)


def test_gap_below_stop_exits_at_open(base):
    r = B.simulate(_t(96.0, 97.0, 95.0, 96.5), V["A"], base)
    assert r["reason"] == "GAP" and r["gross"] == pytest.approx(-4.0)


def test_sell_at_open_and_hold(base):
    t = _t(101.0, 103.0, 99.0, 100.0)
    assert B.simulate(t, V["C"], base)["gross"] == pytest.approx(1.0)
    assert B.simulate(t, V["B"], base)["gross"] == pytest.approx(0.0)


def test_tiered_lock_keeps_part_of_the_peak(base):
    # best path: up to +3% first, then down to -1%: the tiered floor (75% of a 3% peak) must bank about +2.25%
    r = B.simulate(_t(100.0, 103.0, 99.0, 99.5), V["E"], base, path="best")
    assert r["reason"] == "PROFIT_LOCK"
    assert r["gross"] == pytest.approx(2.25, abs=0.05)


def test_costs_and_score(base):
    r = B.simulate(_t(101.0, 101.0, 101.0, 101.0), V["C"], base)
    assert r["net"] == pytest.approx(1.0 - B.COST_PCT)
    s = B.score([r, {**r, "net": -2.0, "gross": -1.75}])
    assert s["n"] == 2 and s["worst_pct"] == -2.0 and s["max_dd_pct"] == -2.0
