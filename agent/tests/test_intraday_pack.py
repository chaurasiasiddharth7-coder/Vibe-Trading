"""Indian intraday pack: opening range, trigger, plan, simulation, costs, replay, calibration."""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.alphadesk.intraday import calibrate, costs, rules, scanner, timemachine
from src.alphadesk.intraday.bars import days_from_frame, parse_chart
from src.alphadesk.intraday.rules import OPEN_MIN, Day, hhmm


def make_day(closes, d=date(2026, 10, 5), spread=0.2, vol=1000.0, vols=None):
    n = len(closes)
    c = np.array(closes, dtype=float)
    o = np.concatenate(([c[0]], c[:-1]))
    return Day(d, OPEN_MIN + 5 * np.arange(n), o, np.maximum(o, c) + spread,
               np.minimum(o, c) - spread, c, np.array(vols if vols is not None else [vol] * n))


def flat_then(path, base=100.0, n=75):
    """Three range bars around `base`, then the given closes, padded flat."""
    closes = [base, base + 0.5, base, *path]
    return closes + [closes[-1]] * (n - len(closes))


def manual_day(n=72, price=101.0, d=date(2026, 10, 5)):
    a = np.full(n, price)
    return Day(d, OPEN_MIN + 5 * np.arange(n), a.copy(), a + 0.1, a - 0.1, a.copy(), np.full(n, 1000.0))


# --- trigger -------------------------------------------------------------------------------------


def test_opening_range_and_close_beyond_level():
    day = make_day(flat_then([100.4, 100.6, 101.5]))  # bars 3 and 4 stay under the 100.7 high
    rng = rules.opening_range(day)
    assert rng.bars == 3 and rng.high == pytest.approx(100.7)
    trig = rules.orb_trigger(day, "long")
    assert trig["status"] == "triggered" and trig["price"] == 101.5 and trig["idx"] == 5
    assert trig["time"] == OPEN_MIN + 5 * 6  # the bar closes 5 minutes after it starts


def test_wick_or_exact_level_is_not_a_trigger():
    day = make_day(flat_then([100.6, 100.7, 100.7]))  # closes at/below the 100.7 range high
    assert rules.orb_trigger(day, "long")["status"] == "expired"


def test_short_triggers_below_range_low():
    day = make_day(flat_then([99.9, 99.2]))
    assert rules.orb_trigger(day, "short")["status"] == "triggered"
    assert rules.orb_trigger(day, "long")["status"] == "expired"


def test_only_closed_bars_count_and_cutoffs():
    day = make_day(flat_then([100.4, 101.5]))  # the breakout bar starts 09:30, closes 09:40... index 4
    start = OPEN_MIN + 5 * 4
    assert rules.orb_trigger(day, "long", now_min=start + 3)["status"] == "waiting"
    assert rules.orb_trigger(day, "long", now_min=start + 5)["status"] == "triggered"
    assert rules.orb_trigger(day, "long", now_min=hhmm("09:25"))["status"] == "forming"
    late = make_day(flat_then([100.0] * 65 + [105.0]))
    assert rules.orb_trigger(late, "long")["status"] == "expired"  # after 14:45


def test_volume_filter():
    assert rules.orb_trigger(make_day(flat_then([100.4, 101.5])), "long", min_vol_x=1.5)["status"] == "expired"
    vols = [1000.0] * 75
    vols[4] = 3000.0
    day = make_day(flat_then([100.4, 101.5]), vols=vols)
    assert rules.orb_trigger(day, "long", min_vol_x=1.5)["status"] == "triggered"


# --- plan ----------------------------------------------------------------------------------------


def test_plan_rounds_stop_away_and_target_toward_entry():
    p = rules.make_plan("long", 364.18, 362.18, 354.47, "or-mid", "2R")
    assert p.stop == 358.3 and p.risk == pytest.approx(5.88)
    assert p.target == 375.9  # 364.18 + 2 * 5.88 = 375.94, floored to the tick
    s = rules.make_plan("short", 98.5, 101.0, 99.0, "or-mid", "1R")
    assert s.stop == 100.0 and s.target == 97.0 and s.reward_risk == pytest.approx(1.0)


def test_plan_refusals():
    with pytest.raises(ValueError, match="wrong side"):
        rules.make_plan("short", 100.0, 101.0, 99.0, "or-mid", "1R")  # midpoint == entry
    with pytest.raises(ValueError, match="too tight"):
        rules.make_plan("long", 100.0, 101.0, 99.0, "pct:0.05", "2R")
    with pytest.raises(ValueError, match="atr"):
        rules.make_plan("long", 100.0, 101.0, 99.0, "atr:1.5", "2R")
    with pytest.raises(ValueError, match="unknown"):
        rules.make_plan("long", 100.0, 101.0, 99.0, "nope", "2R")


# --- simulation ----------------------------------------------------------------------------------


def _plan(side="long"):
    return rules.make_plan(side, 101.0, 101.0, 99.0, "price:100", "1R") if side == "long" else \
        rules.make_plan(side, 101.0, 103.0, 101.0, "price:102", "1R")


def test_simulation_target_stop_both_gap_and_flat():
    plan = _plan()
    assert (plan.stop, plan.target) == (100.0, 102.0)
    d = manual_day()
    d.h[6], d.c[6] = 102.5, 102.0
    t = rules.simulate(d, 5, plan)
    assert t.reason == "target" and t.r_gross == pytest.approx(1.0) and t.r_net < t.r_gross

    d = manual_day()
    d.h[6], d.l[6] = 102.5, 99.5  # touches both: the stop is taken
    assert rules.simulate(d, 5, plan).reason == "stop"

    d = manual_day()
    d.o[6] = d.l[6] = 99.0  # gaps through the stop: fills at the open, worse than -1R
    t = rules.simulate(d, 5, plan)
    assert t.reason == "stop_gap" and t.exit == 99.0 and t.r_gross == pytest.approx(-2.0)

    d = manual_day()
    d.c[:] = 101.2
    t = rules.simulate(d, 5, plan)
    assert t.reason == "flat" and t.r_gross == pytest.approx(0.2)
    assert t.bars_held == 66  # bars 6..71: the 15:10 bar closes at the 15:15 square-off


def test_short_simulation_mirrors():
    plan = _plan("short")
    assert (plan.stop, plan.target) == (102.0, 100.0)
    d = manual_day()
    d.l[6] = 99.9
    t = rules.simulate(d, 5, plan)
    assert t.reason == "target" and t.r_gross == pytest.approx(1.0)


# --- costs ---------------------------------------------------------------------------------------


def test_costs_formula_and_net_r():
    # 100 shares at Rs 1000 each way: brokerage 2 x 20, exchange 5.94, SEBI 0.2,
    # GST 18% of those, STT 25 on the sell, stamp 3 on the buy
    total = costs.trade_costs("long", 1000.0, 1000.0, 100)
    assert total == pytest.approx(40 + 5.94 + 0.2 + 0.18 * (40 + 5.94 + 0.2) + 25 + 3)
    assert costs.trade_costs("short", 1000.0, 1000.0, 100) == pytest.approx(total)
    assert costs.net_r("long", 100.0, 102.0, 2.0, 1000.0) < 1.0
    arr = costs.net_r("long", 100.0, np.array([98.0, 102.0]), 2.0, 1000.0)
    assert arr[0] < -1.0 < 0 < arr[1] < 1.0


# --- bars ----------------------------------------------------------------------------------------


def _ts(h, m, s=0):
    return int(datetime(2026, 10, 5, h, m, s, tzinfo=timezone.utc).timestamp())


def test_parse_chart_converts_ist_filters_grid_and_nulls():
    stamps = [_ts(3, 45), _ts(3, 50), _ts(3, 52, 30), _ts(3, 55)]  # 09:15, 09:20, off-grid, 09:25 IST
    payload = {"chart": {"result": [{"timestamp": stamps, "meta": {"gmtoffset": 19800},
        "indicators": {"quote": [{"open": [1, 2, 3, 4], "high": [2, 3, 4, 5],
                                  "low": [0.5, 1, 2, 3], "close": [1.234, None, 3, 4.5],
                                  "volume": [10, 20, 30, None]}]}}]}}
    f = parse_chart(payload)
    assert [t.strftime("%H:%M") for t in f.index] == ["09:15", "09:25"]
    assert f["close"].iloc[0] == 1.23 and f["volume"].iloc[1] == 0


def test_a_session_with_dropped_closing_bars_is_still_usable():
    idx = pd.date_range("2026-10-05 09:15", periods=75, freq="5min")
    full = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0}, index=idx)
    no_tail = full.drop(full.index[73])  # Yahoo often omits the 15:20 bar
    assert len(days_from_frame(no_tail)) == 1
    hole = full.drop(full.index[30])
    assert days_from_frame(hole) == []


# --- scanner -------------------------------------------------------------------------------------


def _daily(closes, highs=None, lows=None):
    c = np.array(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": highs if highs is not None else c + 1,
                         "low": lows if lows is not None else c - 1, "close": c,
                         "volume": 1000.0}, index=pd.bdate_range("2026-01-01", periods=len(c)))


def test_daily_setups():
    assert "breakout_20d" in scanner.daily_setups(_daily([100.0] * 40 + [110.0]))
    assert "oversold" in scanner.daily_setups(_daily(list(np.linspace(150, 90, 40))))
    assert scanner.daily_setups(_daily([100.0] * 10)) == []  # too short
    rising = list(np.linspace(100, 160, 30)) + [160.0, 160.5, 160.0, 160.5, 160.0]
    assert "bull_flag" in scanner.daily_setups(_daily(rising))


# --- replay --------------------------------------------------------------------------------------


def random_days(n=30, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        c = 100 * np.cumprod(1 + rng.normal(0, 0.0015, 72))
        o = np.concatenate(([c[0]], c[:-1]))
        out.append(Day(date(2026, 8, 1 + i % 28), OPEN_MIN + 5 * np.arange(72), o,
                       np.maximum(o, c) * 1.001, np.minimum(o, c) * 0.999, c, np.full(72, 1000.0)))
    return out


def test_replay_odds_sum_to_one_and_repeat_with_the_seed():
    pool = timemachine.build_pool(random_days())
    plan = rules.make_plan("long", 100.0, 100.0, 98.0, "or-mid", "2R")
    a = timemachine.replay(plan, 5, pool, 2000, seed=19)
    b = timemachine.replay(plan, 5, pool, 2000, seed=19)
    assert a == b
    assert a["target_first"] + a["stop_first"] + a["neither"] == pytest.approx(1.0)
    assert a["avg_r_net"] < a["avg_r_gross"]
    with pytest.raises(ValueError, match="at least"):
        timemachine.replay(plan, 5, pool[:5])
    with pytest.raises(ValueError, match="flat-by"):
        timemachine.replay(plan, 71, pool)


def test_replay_of_a_frozen_market_hits_neither():
    flat = [manual_day(price=100.0, d=date(2026, 8, 1 + i)) for i in range(25)]
    plan = rules.make_plan("long", 100.0, 100.0, 98.0, "or-mid", "2R")
    mc = timemachine.replay(plan, 5, timemachine.build_pool(flat), 500)
    assert mc["neither"] == 1.0


def test_verdict_wording():
    base = {"avg_r_net": 0.1, "avg_r_gross": 0.2}
    assert timemachine.verdict({**base, "target_first": 0.30, "stop_first": 0.25}, 1.0).startswith("Close to a coin flip")
    assert timemachine.verdict({**base, "target_first": 0.30, "stop_first": 0.20}, 1.0).startswith("Leans toward the target")
    assert timemachine.verdict({**base, "target_first": 0.40, "stop_first": 0.10}, 1.0).startswith("Strongly toward the target")
    assert timemachine.verdict({**base, "target_first": 0.33, "stop_first": 0.67}, 2.0).startswith("Close to break-even")
    assert "Costs turn" in timemachine.verdict({"target_first": 0.3, "stop_first": 0.25,
                                                "avg_r_net": -0.1, "avg_r_gross": 0.05}, 1.0)


# --- calibration ---------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def trending_days(n=40):
    out = []
    for i in range(n):
        closes = flat_then([100.4, 101.0, 101.8, 102.6, 103.4] + [104.0] * 20, n=72)
        d = make_day(closes, d=date(2026, 8, 1) + pd.Timedelta(days=i))
        out.append(d)
    return out


SMALL_GRID = {"or_minutes": [15], "side": ["long", "short"], "stop": ["or-mid"],
              "target": ["1R"], "min_vol_x": [None]}


def test_calibration_splits_by_date_and_refuses_without_an_edge(home, monkeypatch):
    days = trending_days()
    monkeypatch.setattr(calibrate, "load_universe", lambda symbols, refresh=False: {"AAA": days, "BBB": days})
    res = calibrate.calibrate(["AAA", "BBB"], grid=SMALL_GRID)
    assert res["in_sample_sessions"] + res["out_of_sample_sessions"] == res["sessions"] == 40
    assert res["in_sample_sessions"] == 28 and res["variants_tried"] == 2
    assert res["best"]["config"]["side"] == "long"  # the trend only pays longs
    assert res["best"]["in_sample"]["trades"] == 56
    # identical sessions have no variance, so the selection bar cannot be cleared honestly
    assert res["edge_candidate"] is False and res["verdict"].startswith("No edge")


def test_record_oos_feeds_the_ledger_once(home, monkeypatch):
    days = trending_days()
    monkeypatch.setattr(calibrate, "load_universe", lambda symbols, refresh=False: {"AAA": days, "BBB": days})
    res = calibrate.calibrate(["AAA", "BBB"], grid=SMALL_GRID)
    first = calibrate.record_oos(res)
    assert first["recorded"] == res["best"]["out_of_sample"]["trades"] > 0
    assert calibrate.record_oos(res)["recorded"] == 0  # reruns cannot farm the scorecard
