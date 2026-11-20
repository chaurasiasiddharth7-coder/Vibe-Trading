"""Past-price tests for the built-in strategies (signal board, BTST), scored like Lab tests."""

from __future__ import annotations

import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.alphadesk import strategies as S
from src.alphadesk import strategies_builtin as SB


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("STRATEGY_SCHEDULER", raising=False)
    return tmp_path


def trending(n=900, seed=1, drift=0.0012):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(drift, 0.012, n))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.006, "low": np.minimum(o, c) * 0.994,
                         "close": c, "volume": 1e5}, index=idx)


def test_scorecard_shape_and_gate():
    trades = [{"symbol": f"S{i % 3}", "entry_date": f"2025-01-{i % 28 + 1:02d}", "exit_date": f"2025-02-{i % 28 + 1:02d}",
               "gross_pct": 1.5 if i % 3 else -1.0, "net_pct": 1.35 if i % 3 else -1.15, "reason": "x"} for i in range(40)]
    r = SB.scorecard(trades, random_avg=0.1, random_label="random", years=3, cost_pct=0.15, symbols_tested=3, note="n")
    assert r["summary"]["trades"] == 40 and r["gate"]["passed"] is True
    assert {c["label"] for c in r["gate"]["checks"]} >= {"At least 30 trades", "Profitable after costs", "random"}
    assert set(r["robustness"]) == {"first_half", "second_half", "double_costs", "holdout_last_30pct", "random_entries"}
    assert r["equity"][-1]["cum_pct"] == pytest.approx(sum(t["net_pct"] for t in trades), abs=0.05)


def test_scorecard_no_trades_fails_gate():
    r = SB.scorecard([], random_avg=None, random_label="", years=1, cost_pct=0.1, symbols_tested=5, note="")
    assert r["gate"]["passed"] is False and r["summary"]["trades"] == 0


def test_signal_board_replay_uses_the_live_rules(monkeypatch):
    from src.alphadesk import index_lists

    monkeypatch.setattr(index_lists, "scan_list", lambda u, include_funds: [{"symbol": "AAA"}, {"symbol": "BBB"}])
    frames = {"AAA": trending(seed=1), "BBB": trending(seed=2, drift=-0.001)}
    seen = []
    r = SB.run_signal_board(2, "nifty50", progress=lambda i, n: seen.append((i, n)), frame=lambda s, p: frames[s])
    assert r["symbols_tested"] == 2 and r["universe"] == "nifty50"
    assert r["summary"]["trades"] > 0
    since = (date.today() - timedelta(days=int(365.25 * 2))).isoformat()
    for t in r["trades"]:
        assert t["entry_date"] >= since  # only the asked-for years count
        assert t["net_pct"] == pytest.approx(t["gross_pct"] - 0.15, abs=1e-6)
        assert t["reason"] in {"target", "stop", "trail", "time"}
    assert seen[-1] == (2, 2)
    assert any(c["label"].startswith("Beats the same exits") for c in r["gate"]["checks"])


def test_btst_reuses_replay_records(monkeypatch):
    from src.alphadesk.btst import days

    last = date.today()
    end = last - timedelta(days=1)
    recs = []
    d = end - timedelta(days=370)
    i = 0
    while d <= end:
        if d.weekday() < 5:
            i += 1
            recs.append({"date": d.isoformat(), "status": "settled", "settled_on": (d + timedelta(days=1)).isoformat(),
                         "pick": {"symbol": "PICK", "outcome": {"net_pct": 0.8 if i % 2 else -0.4, "gross_pct": 1.05 if i % 2 else -0.15, "reason": "target"}},
                         "candidates": [{"outcome": {"net_pct": 0.1}}, {"outcome": {"net_pct": -0.3}}]})
        d += timedelta(days=1)
    recs.reverse()
    called = []
    monkeypatch.setattr(days, "store_last_date", lambda: last)
    monkeypatch.setattr(days, "list_days", lambda kind, limit=None: recs)
    monkeypatch.setattr(days, "replay", lambda *a, **k: called.append(a))
    r = SB.run_btst(1)
    assert not called  # every session already replayed: nothing re-run
    assert r["summary"]["trades"] > 200 and r["summary"]["avg_pct"] == pytest.approx(0.2, abs=0.01)
    rnd = next(c for c in r["gate"]["checks"] if "shortlist" in c["label"])
    assert rnd["ok"] and rnd["value"][1] == pytest.approx(-0.1)


def test_btst_runs_replay_when_missing(monkeypatch):
    from src.alphadesk.btst import days

    called = []
    monkeypatch.setattr(days, "store_last_date", lambda: date.today())
    monkeypatch.setattr(days, "list_days", lambda kind, limit=None: [])
    monkeypatch.setattr(days, "replay", lambda start, end, progress=None: called.append((start, end)) or {})
    r = SB.run_btst(1)
    assert called and r["summary"]["trades"] == 0


def test_start_records_test_for_the_catalogue(home, monkeypatch):
    monkeypatch.setattr(SB, "run_btst", lambda years, progress=None: SB.scorecard(
        [{"symbol": "X", "entry_date": "2025-01-01", "exit_date": "2025-01-02", "gross_pct": 1.0, "net_pct": 0.75, "reason": "target"}],
        random_avg=None, random_label="", years=years, cost_pct=0.25, symbols_tested=1, note="n"))
    jid = SB.start("btst", 2)
    for _ in range(100):
        j = S.job(jid)
        if j["status"] != "running":
            break
        time.sleep(0.02)
    assert j["status"] == "done" and j["result"]["note"] == "n"
    rows = {r["id"]: r for r in S.catalogue(False)["strategies"]}
    assert rows["btst"]["testable"] and rows["btst"]["last_test"]["summary"]["trades"] == 1
    assert rows["intraday"]["testable"] is True and rows["intraday"]["last_test"] is None
    assert S.detail("btst")["tests"][-1]["note"] == "n"
    with pytest.raises(S.StrategyError):
        SB.start("lab_x")


def test_intraday_replays_recent_days_without_filing(home, monkeypatch):
    from src.alphadesk.intraday import calendar as cal
    from src.alphadesk.intraday import days as D

    filed = []
    monkeypatch.setattr(cal, "is_trading_day", lambda d: d.weekday() < 5)
    monkeypatch.setattr(D, "save_day", lambda rec: filed.append(rec))
    monkeypatch.setattr(D, "plan_open", lambda d, settings, past=False: {"planned": [{"symbol": "LT"}], "universe": {}})

    def evaluate(d, rec, settings, live_now):
        assert live_now is None and rec["planned"]
        win = d.day % 2 == 0
        return {"trades": [{"symbol": "LT", "side": "long", "status": "target" if win else "stop", "entry": 100.0, "qty": 10,
                            "risk_per_share": 1.0, "r_gross": 2.0 if win else -1.0, "pnl_inr": 18.0 if win else -12.0}],
                "skipped": [], "data_gaps": [], "not_triggered": [], "as_of": None, "_frames": {}}

    monkeypatch.setattr(D, "evaluate", evaluate)
    r = SB.run_intraday(20, today=date(2026, 11, 20))
    assert not filed  # the paper record is left alone
    assert r["sessions"] == 20 and r["days"] == 20 and r["summary"]["trades"] == 20
    t = r["trades"][0]
    assert t["net_pct"] in (1.8, -1.2) and t["gross_pct"] in (2.0, -1.0)
    assert r["cost_pct"] == pytest.approx(0.2)
    assert "60 days" in r["note"]


def test_guard_text_reads_plainly():
    lines = S.guard_text({"layer1": {"target_r": None, "scale_out_frac": 0.0, "time_stop_bars": 0}})
    assert lines == ["No fixed take-profit: winners run until the trailing stop", "No partial booking", "No time stop"]
    assert S.guard_text({"layer1": {"scale_out_frac": 0.5, "scale_out_at_r": 1.5}}) == ["Book 50% of the position at +1.5R"]


def test_owner_polls_a_job_started_in_another_profile(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.alphadesk import profiles
    from src.api.security import require_auth
    from src.api.strategies_routes import register_strategies_routes

    profiles.create("ayushi", "Ayushi")
    with profiles.use("ayushi"):
        with S._JOBS_LOCK:
            S._JOBS["jx"] = {"id": "jx", "strategy_id": "signal_board", "status": "done", "profile": "ayushi",
                             "started": time.time(), "result": {"summary": {"trades": 0}}, "error": None}
    app = FastAPI()
    register_strategies_routes(app, require_auth)
    c = TestClient(app)
    assert c.get("/api/desk/strategies/jobs/jx").status_code == 404  # not the owner's own job
    r = c.get("/api/desk/strategies/jobs/jx?profile=ayushi")
    assert r.status_code == 200 and r.json()["data"]["status"] == "done"
