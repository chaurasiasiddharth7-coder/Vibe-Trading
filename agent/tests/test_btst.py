"""BTST top-gainer pack: rules, warnings, exits, history stats, NSE parsing, scan/settle/replay,
statistics, the scheduler and the API."""
from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from src.alphadesk.btst import days, rules as R, scheduler, snapshot

SCAN_DAY = date(2026, 3, 16)  # a Monday
NEXT_DAY = date(2026, 3, 17)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NSE_DATA_DIR", str(tmp_path / "nse"))
    (tmp_path / "nse").mkdir()
    return tmp_path


def make_store(root, end=date(2026, 3, 13), n_sym=30, seed=3):
    """Weekday bars for S00..S29 up to ``end``; traded value about Rs 5-60 crore a day."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", end)
    rows = []
    for i in range(n_sym):
        px, base_vol = 100 + 10 * i, (5e7 + i * 2e7) / (100 + 10 * i)
        prev = px
        for d in dates:
            r = rng.normal(0.0005, 0.015)
            c = prev * (1 + r)
            o = prev * (1 + rng.normal(0, 0.003))
            h, lo = max(o, c) * 1.006, min(o, c) * 0.994
            rows.append(dict(symbol=f"S{i:02d}", date=d, isin=f"INE{i:06d}01", open=o, high=h, low=lo, close=c,
                             prev_close=prev, volume=base_vol * rng.uniform(0.7, 1.3)))
            prev = c
    bars = pd.DataFrame(rows)
    bars.to_parquet(root / "nse" / "bars.parquet", index=False)
    return bars


def add_day(root, day: date, overrides: dict[str, dict]):
    """Append a session where each symbol moves +0.2% except the ``overrides`` (gain, gap, vol_x, high_x)."""
    bars = pd.read_parquet(root / "nse" / "bars.parquet")
    last = bars.sort_values("date").groupby("symbol").tail(1)
    new = []
    for r in last.itertuples():
        o = overrides.get(r.symbol, {})
        g = o.get("gain", 0.002)
        c = r.close * (1 + g)
        op = r.close * (1 + o.get("gap", 0.0))
        h = max(op, c) * o.get("high_x", 1.004)
        new.append(dict(symbol=r.symbol, date=pd.Timestamp(day), isin=r.isin, open=op, high=h, low=min(op, c) * 0.995,
                        close=c, prev_close=r.close, volume=r.volume * o.get("vol_x", 1.0)))
    pd.concat([bars, pd.DataFrame(new)]).to_parquet(root / "nse" / "bars.parquet", index=False)
    return pd.DataFrame(new)


# --- pure rules ------------------------------------------------------------------------------


def test_plan_prices_round_to_the_tick():
    assert R.round_tick(101.01, up=True) == 101.05 and R.round_tick(97.99, up=False) == 97.95
    assert R.plan_prices(250.0) == {"entry": 250.0, "target": 252.5, "stop": 245.0}
    from dataclasses import replace
    assert R.plan_prices(250.0, replace(R.DEFAULT_RULES, use_stop=False))["stop"] is None


def row(**kw):
    base = dict(gain=0.06, turn_x=2.0, ret5=0.01, ret20=0.03, breadth=0.55, dist_hi250=-0.02, gap=0.005, vol20=0.02)
    base.update(kw)
    return pd.Series(base)


@pytest.mark.parametrize("kw,key", [
    (dict(turn_x=12), "volume_blowoff"), (dict(gain=0.15), "too_hot"), (dict(ret5=0.10), "overextended"),
    (dict(ret20=0.25), "overextended"), (dict(breadth=0.30), "weak_market"), (dict(ret20=-0.08), "downtrend"),
    (dict(dist_hi250=-0.35), "downtrend"), (dict(gap=0.03), "big_gap"), (dict(vol20=0.05), "very_volatile"),
])
def test_each_warning_fires_alone(kw, key):
    assert R.warnings_for(row()) == []
    w = R.warnings_for(row(**kw))
    assert [x["key"] for x in w] == [key] and w[0]["detail"] and w[0]["big_loss_flagged_pct"] > w[0]["big_loss_clean_pct"]


def test_missing_values_never_warn():
    assert R.warnings_for(row(turn_x=np.nan, ret5=None, ret20=np.nan, breadth=None, dist_hi250=np.nan, vol20=np.nan)) == []


def snap_rows(**over):
    base = dict(symbol="A", series="EQ", open=100.5, high=106.5, low=100.0, last=106.0, prev_close=100.0, traded=50e7, source="t")
    base.update(over)
    return base


def test_candidates_filters_and_ranks():
    snap = pd.DataFrame([
        snap_rows(symbol="GOOD", last=106.0),
        snap_rows(symbol="BEST", last=109.0, high=110.0),
        snap_rows(symbol="LOCKED", last=110.0, high=110.0),  # +10%, at the high: upper band
        snap_rows(symbol="THIN", traded=5e7),  # Rs 5 cr today
        snap_rows(symbol="CHEAP", last=42.4, prev_close=40.0, open=40.0, high=43.0, low=40.0),
        snap_rows(symbol="FLAT", last=101.0),
        snap_rows(symbol="BEONLY", series="BE"),
        snap_rows(symbol="NEW"),
    ])
    prior = pd.DataFrame({"turn20": [30e7] * 7 + [np.nan], "vol20": 0.02, "ret5": 0.01, "ret20": 0.03, "prev_ret": 0.0,
                          "hi250_rel": 1.0, "sessions": [200] * 7 + [3], "last_date": pd.Timestamp("2026-03-13")},
                         index=["GOOD", "BEST", "LOCKED", "THIN", "CHEAP", "FLAT", "BEONLY", "NEW"])
    feat = R.features(snap, prior, 0.6)
    c, left = R.candidates(feat)
    assert c["symbol"].tolist() == ["BEST", "GOOD"] and c["rank"].tolist() == [1, 2]
    assert left == {"illiquid_today": 1, "thin_history": 1, "cheap": 1, "upper_band": 1, "small_move": 1, "not_eq": 1}
    rows, pick, reason = R.choose(c)
    assert pick["symbol"] == "BEST" and rows[0]["chosen"] and reason is None
    assert pick["target"] == 110.1 and pick["stop"] == 106.8


def test_choose_skips_when_every_candidate_warns():
    c = pd.DataFrame([dict(symbol="X", rank=1, gain=0.15, last=115.0, prev_close=100.0, open=100, high=116, low=99,
                           traded=50e7, turn_x=2, ret5=0, ret20=0, dist_hi250=0, vol20=0.02, gap=0, off_high=-0.01, breadth=0.6)])
    rows, pick, reason = R.choose(c)
    assert pick is None and "warning" in reason and rows[0]["warnings"][0]["key"] == "too_hot"


def test_exit_daily_cases():
    e = 100.0  # target 101.00, stop 98.00
    assert R.exit_daily(e, 101.5, 102, 101, 101.2)["reason"] == "gap_target"
    assert R.exit_daily(e, 101.5, 102, 101, 101.2)["exit"] == 101.5
    assert R.exit_daily(e, 97.0, 99, 96, 98.5)["reason"] == "gap_stop"
    t = R.exit_daily(e, 100.2, 101.3, 99.5, 100.0)
    assert t["reason"] == "target" and t["hit"] and t["net_pct"] == pytest.approx(0.75)
    amb = R.exit_daily(e, 100.2, 101.3, 97.5, 100.0)
    assert amb["reason"] == "stop" and amb["ambiguous"] and amb["alt_net_pct"] == pytest.approx(0.75)
    assert amb["net_pct"] == pytest.approx(-2.25)
    tx = R.exit_daily(e, 100.1, 100.6, 99.0, 100.4)
    assert tx["reason"] == "time_exit" and tx["exit"] == 100.4 and not tx["hit"]


def test_exit_path_knows_the_order():
    idx = pd.date_range("2026-03-17 09:15", periods=4, freq="5min")
    up_first = pd.DataFrame({"open": [100.2, 100.8, 100.5, 98.5], "high": [100.9, 101.2, 100.6, 98.6],
                             "low": [100.1, 100.6, 98.4, 97.0], "close": [100.8, 100.9, 98.6, 97.2]}, index=idx)
    o = R.exit_path(100.0, up_first)
    assert o["reason"] == "target" and o["method"] == "intraday" and not o["ambiguous"]
    down_first = up_first.iloc[[0, 3, 1]].copy()
    down_first.index = idx[:3]
    assert R.exit_path(100.0, down_first)["reason"] == "stop"
    late = pd.DataFrame({"open": [100.1, 100.2], "high": [100.3, 100.4], "low": [99.9, 100.0], "close": [100.2, 100.3]},
                        index=pd.DatetimeIndex(["2026-03-17 15:20", "2026-03-17 15:25"]))
    assert R.exit_path(100.0, late, exit_time="15:20")["exit"] == 100.2


def test_rolling_stats_survive_a_split():
    d = pd.bdate_range("2025-10-01", periods=70)  # the 1-year high needs 60 sessions
    close = [100.0] * 55 + [50.0] * 15  # 1:2 split on day 55; NSE adjusts the previous close to 50
    prev = [100.0] * 55 + [50.0] * 15
    bars = pd.DataFrame({"symbol": "X", "date": d, "open": close, "high": [c * 1.01 for c in close], "low": close,
                         "close": close, "prev_close": prev, "volume": 1e6})
    s = R.rolling_stats(bars)
    assert s["ret"].abs().max() < 1e-9 and s["ret20"].dropna().abs().max() < 1e-9
    p = R.prior_stats(s, pd.Timestamp(d[-1]) + pd.Timedelta(days=1)).loc["X"]
    assert p["hi250_rel"] == pytest.approx(1.01) and p["sessions"] == 70


# --- NSE pages -------------------------------------------------------------------------------------


def test_parse_nse_index_page_and_gainers():
    page = {"name": "NIFTY TOTAL MARKET", "data": [
        {"priority": 1, "symbol": "NIFTY TOTAL MARKET", "lastPrice": 13000},
        {"priority": 0, "symbol": "ABC", "open": 100, "dayHigh": 106, "dayLow": 99, "lastPrice": 105.5,
         "previousClose": 100, "totalTradedValue": 1.2e9, "meta": {"series": "EQ"}},
        {"priority": 0, "symbol": "BAD", "lastPrice": "-", "previousClose": 50},
    ]}
    s = snapshot.parse_index_page(page)
    assert s["symbol"].tolist() == ["ABC"] and s.iloc[0]["traded"] == 1.2e9 and s.iloc[0]["high"] == 106
    g = {"allSec": {"data": [{"symbol": "XYZ", "series": "EQ", "open_price": 10, "high_price": 12, "low_price": 9.8,
                              "ltp": "11.9", "prev_price": 10, "turnover": "2,500.5"}]}}
    gs = snapshot.parse_gainers(g)
    assert gs.iloc[0]["traded"] == pytest.approx(2500.5e5) and gs.iloc[0]["last"] == 11.9


def test_take_merges_sources_and_reports_coverage(env, monkeypatch):
    f = env / "snap.json"
    f.write_text(json.dumps({"rows": [snap_rows(symbol="A"), snap_rows(symbol="B")]}))
    monkeypatch.setenv("BTST_SNAPSHOT_FILE", str(f))
    snap, info = snapshot.take(["A", "B", "C"], sources=["offline", "nosuch"])
    assert len(snap) == 2 and info["covered"] == 2 and info["universe"] == 3 and "unknown quote source" in info["notes"][0]


# --- live scan, settle, replay, stats ---------------------------------------------------------------------


def write_snapshot(env, monkeypatch, bars_today: pd.DataFrame):
    rows = [dict(symbol=r.symbol, series="EQ", open=r.open, high=r.high, low=r.low, last=r.close, prev_close=r.prev_close,
                 traded=r.close * r.volume) for r in bars_today.itertuples()]
    f = env / "snap.json"
    f.write_text(json.dumps({"rows": rows}))
    monkeypatch.setenv("BTST_SNAPSHOT_FILE", str(f))
    monkeypatch.setenv("BTST_QUOTE_SOURCES", "offline")


def test_live_scan_then_settle(env, monkeypatch):
    make_store(env)
    # S05 +6% clean, S07 +15% (too hot), S09 +5% on 15x volume
    today = add_day(env, SCAN_DAY, {"S05": {"gain": 0.06}, "S07": {"gain": 0.15}, "S09": {"gain": 0.05, "vol_x": 15}})
    # the scan reads history only up to yesterday: take today's bars back out of the store
    bars = pd.read_parquet(env / "nse" / "bars.parquet")
    bars[bars["date"] < pd.Timestamp(SCAN_DAY)].to_parquet(env / "nse" / "bars.parquet", index=False)
    write_snapshot(env, monkeypatch, today)
    rec = days.run_scan(SCAN_DAY, "test", now=datetime(2026, 3, 16, 15, 20))
    syms = [c["symbol"] for c in rec["candidates"]]
    assert syms[:3] == ["S07", "S05", "S09"] and rec["pick"]["symbol"] == "S05" and rec["status"] == "picked"
    assert {w["key"] for w in rec["candidates"][2]["warnings"]} >= {"volume_blowoff"}
    assert rec["scanned_at"] == "15:20" and rec["history_to"] == "2026-03-13"
    assert days.load_day(SCAN_DAY)["pick"]["symbol"] == "S05"
    with pytest.raises(days.NotReady):
        days.run_settle(SCAN_DAY, "test", now=datetime(2026, 3, 17, 12, 0))
    # next session in the store: S05 opens flat and trades up 1.5% (take-profit), S07 gives back 6%
    entry = rec["pick"]["entry"]
    bars = pd.read_parquet(env / "nse" / "bars.parquet")
    bars = pd.concat([bars, today])
    bars.to_parquet(env / "nse" / "bars.parquet", index=False)
    add_day(env, NEXT_DAY, {"S05": {"gain": 0.012, "high_x": 1.01}, "S07": {"gain": -0.06}})
    monkeypatch.setattr(days, "_intraday_from_yahoo", lambda s, d: pd.DataFrame())
    out = days.run_settle(SCAN_DAY, "test", now=datetime(2026, 3, 17, 15, 45))
    po = out["pick"]["outcome"]
    assert out["status"] == "settled" and po["hit"] and po["reason"] == "target" and po["exit"] == R.plan_prices(entry)["target"]
    s7 = next(c for c in out["candidates"] if c["symbol"] == "S07")["outcome"]
    assert s7["reason"] in ("stop", "gap_stop") and s7["net_pct"] < 0
    st = days.stats("live")
    assert st["picks"]["trades"] == 1 and st["picks"]["hit_rate"] == 1.0 and st["candidates"]["settled"] >= 3


def test_scan_refuses_past_days_and_settled_today(env, monkeypatch):
    with pytest.raises(ValueError):
        days.run_scan(date(2026, 3, 13), "test", now=datetime(2026, 3, 16, 15, 20))
    with pytest.raises(ValueError):
        days.run_scan(date(2026, 3, 15), "test", now=datetime(2026, 3, 15, 15, 20))  # Sunday


def test_settle_uses_intraday_bars_when_given(env):
    rec = {"date": "2026-03-16", "rules": R.DEFAULT_RULES.as_dict(),
           "candidates": [{"symbol": "A", "last": 100.0, "chosen": True, "warnings": []}],
           "pick": {"symbol": "A", "entry": 100.0, "target": 101.0, "stop": 98.0}}
    daily = {"A": {"open": 100.2, "high": 101.5, "low": 97.5, "close": 99.0, "prev_close": 100.0}}
    idx = pd.date_range("2026-03-17 09:15", periods=2, freq="5min")
    path = pd.DataFrame({"open": [100.2, 100.9], "high": [100.9, 101.5], "low": [100.1, 100.8], "close": [100.9, 101.2]}, index=idx)
    out = days.settle_record(json.loads(json.dumps(rec)), NEXT_DAY, daily, path_for=lambda s: path)
    assert out["pick"]["outcome"]["method"] == "intraday" and out["pick"]["outcome"]["reason"] == "target"
    assert out["candidates"][0]["outcome"]["ambiguous"]  # the daily bar alone cannot tell
    gone = days.settle_record(json.loads(json.dumps(rec)), NEXT_DAY, {}, strict=False)
    assert gone["pick"]["outcome"]["data_check"]
    with pytest.raises(days.NotReady):
        days.settle_record(json.loads(json.dumps(rec)), NEXT_DAY, {})


def test_split_on_the_next_day_is_left_out(env):
    rec = {"date": "2026-03-16", "rules": R.DEFAULT_RULES.as_dict(),
           "candidates": [{"symbol": "A", "last": 100.0, "chosen": False, "warnings": []}], "pick": None}
    out = days.settle_record(rec, NEXT_DAY, {"A": {"open": 50, "high": 51, "low": 49, "close": 50, "prev_close": 50}})
    assert out["candidates"][0]["outcome"]["data_check"]


def test_replay_files_days_apart_from_live(env):
    make_store(env, end=date(2026, 3, 20))
    res = days.replay(date(2026, 2, 2), date(2026, 3, 19))
    assert res["filed"] >= 30
    assert days.list_days("live") == [] and len(days.list_days("replay")) == res["filed"]
    st = days.stats("replay")
    assert st["settled_days"] == res["filed"] and "picks_no_stop" in st and len(st["candidates"]["by_warning"]) == 7
    y = st["by_year"][0]
    assert y["avg_target_first_pct"] >= y["avg_net_pct"] and y["avg_no_stop_pct"] is not None
    one = days.list_days("replay", 1)[0]
    assert one["kind"] == "replay" and one["rules"]["locked_near_high"] == days.REPLAY_LOCKED_NEAR_HIGH
    row_ = days.row_of(one)
    assert set(row_) >= {"date", "symbol", "net_pct", "candidates", "status"}


# --- scheduler ---------------------------------------------------------------------------------------


def test_due_jobs(env):
    st = {}
    assert scheduler.due_jobs(datetime(2026, 3, 16, 15, 19), st) == []
    assert scheduler.due_jobs(datetime(2026, 3, 16, 15, 20), st) == ["scan"]
    assert scheduler.due_jobs(datetime(2026, 3, 16, 15, 41), st) == ["settle"]
    assert scheduler.due_jobs(datetime(2026, 3, 16, 15, 25), {"scan": {"date": "2026-03-16"}}) == []
    assert scheduler.due_jobs(datetime(2026, 3, 15, 15, 25), st) == []  # Sunday
    ran = []
    assert scheduler.tick(datetime(2026, 3, 16, 15, 21), runner=lambda k, n: ran.append(k)) == ["scan"]
    assert scheduler.load_state()["scan"]["ok"] and ran == ["scan"]
    assert scheduler.tick(datetime(2026, 3, 16, 15, 22), runner=lambda k, n: ran.append(k)) == []
    assert "CRON_TZ=Asia/Kolkata" in scheduler.cron_lines() and "20 15 * * 1-5" in scheduler.cron_lines()


# --- API -----------------------------------------------------------------------------------------------


def test_api(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.btst_routes import register_btst_routes

    app = FastAPI()
    register_btst_routes(app, lambda: None)
    c = TestClient(app)
    body = c.get("/api/desk/btst/today").json()
    assert body["ok"] and body["data"] is None and body["schedule"]["scan_time"] == "15:20"
    assert c.get("/api/desk/btst/days").json()["data"] == []
    assert set(c.get("/api/desk/btst/stats").json()["data"]) == {"live", "replay"}
    assert len(c.get("/api/desk/btst/rules").json()["data"]["warnings"]) == 7
    assert c.post("/api/desk/btst/settle?day=2026-03-16").status_code == 404
    assert c.get("/api/desk/btst/day/bad").status_code == 422
    assert c.post("/api/desk/btst/replay").status_code == 400  # no store on this server
    days.save_day({"date": "2026-03-16", "kind": "live", "status": "picked", "candidates": [], "pick": None})
    assert c.get("/api/desk/btst/day/2026-03-16").json()["data"]["status"] == "picked"
    assert c.get("/api/desk/btst/days").json()["data"][0]["date"] == "2026-03-16"


def test_store_with_plain_dates_like_nse_pull_writes(env):
    """nse_pull.py stores ``date`` as a date32 column, not timestamps."""
    bars = make_store(env, end=date(2026, 3, 20))
    bars["date"] = bars["date"].dt.date
    bars.to_parquet(env / "nse" / "bars.parquet", index=False)
    assert days.store_last_date() == date(2026, 3, 20)
    got = days.store_bars(date(2026, 3, 2), date(2026, 3, 6))
    assert got["date"].dt.date.min() == date(2026, 3, 2) and got["date"].dt.date.max() == date(2026, 3, 6)
    assert days.replay(date(2026, 3, 2), date(2026, 3, 19))["filed"] >= 10
