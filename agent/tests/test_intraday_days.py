"""The day log: settings, planning, evaluation with caps and the market filter, settling,
day types, statistics, the two daily jobs, and the API."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday import calendar as cal
from src.alphadesk.intraday import days, daytype, scheduler, universe
from src.alphadesk.intraday.rules import OPEN_MIN, Day

D = date(2026, 10, 5)  # a Monday
PREV = date(2026, 10, 2)


def frame(day: date, closes, spread=0.2, vol=1000.0, vols=None):
    n = 75
    c = list(closes) + [closes[-1]] * (n - len(closes))
    c = np.array(c, dtype=float)
    o = np.concatenate(([c[0]], c[:-1]))
    idx = pd.date_range(f"{day} 09:15", periods=n, freq="5min")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread,
                         "close": c, "volume": vols if vols is not None else vol}, index=idx)


AAA = [100, 100.5, 100, 101.5, 102, 103, 104.5]  # long breakout to its 2R target
BBB = [100, 99.5, 100, 98.5, 98.2, 99.0, 100.5]  # short breakdown, then squeezed back through the stop
CCC = [100.0] * 8  # never triggers
NIFTY_UP = [22000, 22010, 22000, 22090, 22150, 22210, 22290] + list(np.linspace(22300, 22420, 60))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NSE_DATA_DIR", str(tmp_path / "nse"))
    monkeypatch.setattr(cal, "is_trading_day", lambda d: d.weekday() < 5)
    series = {"AAA": AAA, "BBB": BBB, "CCC": CCC}
    monkeypatch.setattr(universe, "resolve", lambda key, limit=60, as_of=None: {
        "key": key, "label": "Test", "symbols": list(series), "covered": 3, "eligible": 3,
        "tags": {"AAA": ["nifty50"], "BBB": ["niftybank"], "CCC": []}})
    monkeypatch.setattr(days.scanner, "scan_daily", lambda syms, as_of=None: {
        "source": "test", "fetched": len(syms), "liquid": len(syms),
        "rows": [{"symbol": s, "price": 100.0, "traded_value_cr": 50.0 - i, "setups": ["nr7"] if s == "AAA" else [],
                  "volume_x": 1.0, "as_of": PREV.isoformat()} for i, s in enumerate(syms)]})
    monkeypatch.setattr(ibars, "load_store", lambda s: pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))

    def fake_fetch(symbol, range_="60d", interval="5m", offline=None, ttl=0.0):
        return symbol + ".NS", frame(D, series[symbol])
    monkeypatch.setattr(ibars, "fetch", fake_fetch)

    def fake_ticker(ticker, range_="60d", ttl=0.0):
        prev = frame(PREV, [21950] * 75)
        return pd.concat([prev, frame(D, NIFTY_UP)])
    monkeypatch.setattr(ibars, "fetch_ticker", fake_ticker)
    monkeypatch.setattr(ibars, "now_ist", lambda: datetime(2026, 10, 6, 10, 0))  # D is a past day
    monkeypatch.setattr(ibars, "merge_into_store", lambda s, f: 0)
    return tmp_path


# --- settings ------------------------------------------------------------------------------------


def test_settings_validation(env):
    s = days.save_settings({"max_trades": 3, "universe": "niftybank"})
    assert s["max_trades"] == 3 and days.load_settings()["universe"] == "niftybank"
    for bad in ({"max_trades": 0}, {"universe": "moon"}, {"sides": "up"}, {"or_minutes": 10},
                {"risk_inr": 5}, {"stop": "wobble"}, {"target": "big"}, {"close_time": "25:99x"}):
        with pytest.raises(ValueError):
            days.validate_settings(bad)


# --- the replay of a day --------------------------------------------------------------------------


def test_replay_files_the_day_with_trades_paths_and_type(env):
    rec = days.run_replay(D, "test")
    assert rec["status"] == "settled" and [p["symbol"] for p in rec["planned"]] == ["AAA", "BBB", "CCC"]
    by = {t["symbol"]: t for t in rec["trades"]}
    assert set(by) == {"AAA", "BBB"} and rec["not_triggered"] == ["CCC"]
    a, b = by["AAA"], by["BBB"]
    assert a["side"] == "long" and a["status"] == "target" and a["decided_at"] == "09:35"
    assert a["r_gross"] == pytest.approx(2.0) and a["r_net"] < a["r_gross"] and a["pnl_inr"] > 0
    assert a["path"][0] == [OPEN_MIN + 5 * 4, 0.0] and a["path"][-1][1] == pytest.approx(2.0)
    assert a["tags"] == ["nifty50"] and a["qty"] == int(1000 // a["risk_per_share"])
    assert b["side"] == "short" and b["status"] in ("stop", "stop_gap") and b["r_net"] < -1
    s = rec["summary"]
    assert (s["taken"], s["wins"], s["losses"]) == (2, 1, 1) and s["win_rate"] == 0.5
    assert rec["day"]["label"] == "Trend up" and rec["day"]["nifty50"]["metrics"]["gap_pct"] == pytest.approx(0.23, abs=0.02)
    assert days.load_day(D)["summary"] == s  # filed on disk
    assert days.list_days()[0]["date"] == D.isoformat() and days.list_days()[0]["taken"] == 2


def test_cap_and_one_position_per_symbol(env):
    days.save_settings({"max_trades": 1})
    rec = days.run_replay(D, "test")
    assert [t["symbol"] for t in rec["trades"]] == ["AAA"]  # AAA and BBB both fire at 09:35; higher volume ties go by name
    assert any("cap of 1" in s["reason"] for s in rec["skipped"])


def test_market_filter_skips_trades_against_the_nifty(env):
    days.save_settings({"market_filter": "with_nifty"})
    rec = days.run_replay(D, "test")
    # BBB's breakdown short is against a rising Nifty and is skipped; its later squeeze back above
    # the range is a long, which the market allows.
    assert [(t["symbol"], t["side"]) for t in rec["trades"]] == [("AAA", "long"), ("BBB", "long")]
    assert any(s["symbol"] == "BBB" and s["side"] == "short" and "against the market" in s["reason"]
               for s in rec["skipped"])


def test_sides_setting(env):
    days.save_settings({"sides": "short"})
    assert [t["symbol"] for t in days.run_replay(D, "test")["trades"]] == ["BBB"]


def test_closed_market_and_future_days_are_refused(env):
    with pytest.raises(ValueError, match="closed"):
        days.run_replay(date(2026, 10, 4), "test")  # a Sunday
    with pytest.raises(ValueError, match="past days"):
        days.run_replay(date(2026, 10, 6), "test")  # today


def test_failed_run_is_logged_on_the_day(env, monkeypatch):
    monkeypatch.setattr(days.scanner, "scan_daily", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        days.run_replay(D, "test")
    runs = days.load_day(D)["runs"]
    assert runs[-1]["ok"] is False and "boom" in runs[-1]["note"]


# --- open / refresh / close with a live clock -----------------------------------------------------


def test_open_plans_without_trades_then_refresh_marks_live(env, monkeypatch):
    rec = days.run_open(D, "test")
    assert rec["status"] == "planned" and rec["trades"] == [] and len(rec["planned"]) == 3
    live = datetime(2026, 10, 5, 9, 45)
    monkeypatch.setattr(ibars, "now_ist", lambda: live)

    def partial(symbol, range_="1d", interval="5m", offline=None, ttl=0.0):
        series = {"AAA": AAA, "BBB": BBB, "CCC": CCC}[symbol]
        return symbol + ".NS", frame(D, series).iloc[:6]  # bars through 09:40, which closes 09:45
    monkeypatch.setattr(ibars, "fetch", partial)
    rec = days.run_refresh(D, "test", live)
    a = next(t for t in rec["trades"] if t["symbol"] == "AAA")
    assert rec["status"] == "live" and a["status"] == "open" and a["exit_time"] is None
    assert a["r_gross"] > 0 and rec["summary"]["open"] >= 1
    series = {"AAA": AAA, "BBB": BBB, "CCC": CCC}
    monkeypatch.setattr(ibars, "fetch", lambda symbol, **k: (symbol + ".NS", frame(D, series[symbol])))
    rec = days.run_close(D, "test")  # the whole day is in now
    assert rec["status"] == "settled" and next(t for t in rec["trades"] if t["symbol"] == "AAA")["status"] == "target"


def test_close_without_an_open_scan_plans_late_and_says_so(env):
    rec = days.run_close(D, "test")
    assert rec["status"] == "settled" and "missed" in rec["runs"][-1]["note"]


# --- stats ---------------------------------------------------------------------------------------


def test_stats_cumulative_win_ratio_and_groups(env):
    days.run_replay(D, "test")
    st = days.stats()
    assert st["days"] == 1 and st["series"][0]["cum_trades"] == 2 and st["series"][0]["cum_win_rate"] == 0.5
    assert st["by_type"][0]["key"] == "Trend up" and st["by_type"][0]["days"] == 1
    assert {r["key"] for r in st["by_side"]} == {"long", "short"}
    assert {r["key"] for r in st["by_index"]} == {"nifty50", "niftybank"}


# --- day types -----------------------------------------------------------------------------------


def _day(closes, opens=None):
    f = frame(D, closes)
    n = 72
    f = f.iloc[:n]
    return Day(D, OPEN_MIN + 5 * np.arange(n), f.open.to_numpy(), f.high.to_numpy(), f.low.to_numpy(),
               f.close.to_numpy(), f.volume.to_numpy())


def test_day_type_rules():
    up = daytype.classify(_day(np.linspace(100, 101.2, 72)), 100.0)
    assert up["type"] == "trend_up"
    down = daytype.classify(_day(np.linspace(100, 98.8, 72)), 100.0)
    assert down["type"] == "trend_down"
    chop = daytype.classify(_day(100 + 0.1 * np.sin(np.linspace(0, 12, 72))), 100.0)
    assert chop["type"] == "range"
    rev = daytype.classify(_day(list(np.linspace(100, 100.5, 12)) + list(np.linspace(100.5, 99.6, 60))), 100.0)
    assert rev["type"] == "reversal"
    gap = daytype.classify(_day(100.8 + 0.1 * np.sin(np.linspace(0, 12, 72))), 100.0)
    assert gap["type"] == "gap" and gap["label"] == "Gap up day"
    assert daytype.classify(_day([100] * 5)["x"] if False else _day(np.linspace(100, 101, 72)).__class__(D, np.array([555]), *(np.array([1.0]),) * 5), 100.0)["type"] == "unknown"


# --- the two daily jobs --------------------------------------------------------------------------

SET = {"open_time": "09:15", "close_time": "15:40", "live_refresh_minutes": 0}


def at(h, m, day=D):
    return datetime(day.year, day.month, day.day, h, m)


def test_due_jobs(env):
    assert scheduler.due_jobs(at(9, 0), {}, SET) == []
    assert scheduler.due_jobs(at(9, 15), {}, SET) == ["open"]
    assert scheduler.due_jobs(at(9, 20), {"open": {"date": D.isoformat()}}, SET) == []
    assert scheduler.due_jobs(at(15, 40), {"open": {"date": D.isoformat()}}, SET) == ["close"]
    assert scheduler.due_jobs(at(16, 30), {}, SET) == ["close"]  # open window is over; close still owed
    assert scheduler.due_jobs(at(16, 30), {"open": {"date": D.isoformat()}, "close": {"date": D.isoformat()}}, SET) == []
    assert scheduler.due_jobs(at(10, 0, date(2026, 10, 3)), {}, SET) == []  # Saturday
    live = {**SET, "live_refresh_minutes": 15}
    opened = {"open": {"date": D.isoformat()}}
    assert scheduler.due_jobs(at(10, 0), opened, live) == ["refresh"]
    assert scheduler.due_jobs(at(10, 10), {**opened, "refresh": {"date": D.isoformat(), "minute": 600}}, live) == []
    assert scheduler.due_jobs(at(10, 15), {**opened, "refresh": {"date": D.isoformat(), "minute": 600}}, live) == ["refresh"]


def test_tick_runs_each_job_once_and_survives_a_failure(env, monkeypatch):
    ran = []
    monkeypatch.setattr(scheduler, "_recent_unsettled", lambda now, n=3: [])  # no missed days here

    def runner(kind, now):
        ran.append(kind)
        if kind == "close":
            raise RuntimeError("yahoo down")
    assert scheduler.tick(at(9, 16), runner) == ["open"]
    assert scheduler.tick(at(9, 17), runner) == []  # not repeated
    assert scheduler.tick(at(15, 41), runner) == ["close"]
    state = scheduler.load_state()
    assert state["open"]["ok"] is True and state["close"]["ok"] is False and "yahoo down" in state["close"]["note"]
    assert ran == ["open", "close"]


def test_catch_up_replays_missed_days_once_a_day(env, monkeypatch):
    calls = []

    def failing(d, source="x"):
        calls.append(d)
        raise RuntimeError("no data")
    monkeypatch.setattr(days, "run_replay", failing)
    monkeypatch.setattr(scheduler, "due_jobs", lambda *a: [])
    scheduler.tick(datetime(2026, 10, 6, 9, 20), lambda k, n: None)  # Tuesday morning, Monday never filed
    assert date(2026, 10, 5) in calls
    n = len(calls)
    scheduler.tick(datetime(2026, 10, 6, 9, 21), lambda k, n_: None)
    scheduler.tick(datetime(2026, 10, 6, 9, 22), lambda k, n_: None)
    assert len(calls) == n  # a failing day is not retried every 30 seconds
    scheduler.tick(datetime(2026, 10, 7, 9, 20), lambda k, n_: None)
    assert len(calls) > n  # but it is tried again the next day


def test_cron_lines_and_status(env):
    text = scheduler.cron_lines()
    assert "CRON_TZ=Asia/Kolkata" in text and "15 9 * * 1-5" in text and "40 15 * * 1-5" in text
    st = scheduler.status(at(8, 0))
    assert st["enabled"] is False and st["next"]["kind"] == "open" and st["next"]["at"].endswith("09:15")


# --- API ----------------------------------------------------------------------------------------


def test_api_roundtrip(env, monkeypatch):
    import api_server
    from fastapi.testclient import TestClient
    app = api_server.app
    app.dependency_overrides[api_server.require_auth] = lambda: None
    try:
        c = TestClient(app)
        assert c.get("/api/desk/intraday/settings").json()["data"]["max_trades"] == 5
        assert c.put("/api/desk/intraday/settings", json={"max_trades": 2}).json()["data"]["max_trades"] == 2
        assert c.put("/api/desk/intraday/settings", json={"max_trades": 99}).status_code == 422
        assert c.put("/api/desk/intraday/settings", json={"stop": "wobble"}).status_code == 400
        assert c.post("/api/desk/intraday/day/2026-10-04/run?kind=replay").status_code == 400  # Sunday
        assert c.post("/api/desk/intraday/day/2030-01-01/run?kind=open").status_code == 400
        assert c.post("/api/desk/intraday/day/bad/run?kind=open").status_code == 422
        days.run_replay(D, "test")
        body = c.get(f"/api/desk/intraday/day/{D.isoformat()}").json()
        assert body["data"]["summary"]["taken"] == 2 and body["running"] is None
        assert c.get("/api/desk/intraday/days").json()["data"][0]["date"] == D.isoformat()
        assert c.get("/api/desk/intraday/stats").json()["data"]["days"] == 1
        assert c.get("/api/desk/intraday/schedule").json()["data"]["cron"].startswith("CRON_TZ")
        days._RUNNING[D.isoformat()] = "close"
        assert c.post(f"/api/desk/intraday/day/{D.isoformat()}/run?kind=close").status_code == 409
    finally:
        days._RUNNING.clear()
        app.dependency_overrides.clear()


def test_backfill_files_trading_days_skips_settled_and_survives_a_bad_day(env, monkeypatch):
    # "today" is Tue 6 Oct (env fixture); ask for the previous week
    res = days.backfill(date(2026, 9, 28), date(2026, 10, 5), "test")
    assert len(res["filed"]) + len(res["failed"]) == 6 and res["kept"] == []  # Mon 28 Sep .. Mon 5 Oct, weekdays only
    again = days.backfill(date(2026, 9, 28), date(2026, 10, 5), "test")
    assert again["filed"] == [] and len(again["kept"]) == len(res["filed"])
    forced = days.backfill(date(2026, 10, 5), date(2026, 10, 5), "test", force=True)
    assert forced["filed"] == ["2026-10-05"]
    orig = days.run_replay

    def flaky(d, source="x"):
        if d == date(2026, 10, 1):
            raise RuntimeError("no prices")
        return orig(d, source)
    monkeypatch.setattr(days, "run_replay", flaky)
    res = days.backfill(date(2026, 9, 30), date(2026, 10, 2), "test", force=True)
    assert [f["date"] for f in res["failed"]] == ["2026-10-01"] and len(res["filed"]) == 2


def test_days_filed_under_other_rules_are_flagged_not_blended_silently(env):
    days.run_replay(D, "test")
    assert days.list_days()[0]["rules_match"] is True and days.stats()["mixed_rules"] == 0
    days.save_settings({"market_filter": "with_nifty"})
    assert days.list_days()[0]["rules_match"] is False and days.stats()["mixed_rules"] == 1
    days.save_settings({"open_time": "09:20", "live_refresh_minutes": 10})  # schedule only: not a trading rule
    days.save_settings({"market_filter": "off"})
    assert days.list_days()[0]["rules_match"] is True
    # refiling under the new rules clears the flag
    days.save_settings({"max_trades": 2})
    days.backfill(D, D, "test", force=True)
    assert days.stats()["mixed_rules"] == 0 and days.list_days()[0]["taken"] <= 2
