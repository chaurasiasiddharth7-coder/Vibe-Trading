"""The chart's strategy overlays: what each strategy predicted on one stock, and saved Kronos forecasts."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.alphadesk import chart_signals as C
from src.alphadesk import kronos_log as K
from src.alphadesk import strategies as S


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".vibe-trading" / "alphadesk"
    root.mkdir(parents=True)
    monkeypatch.setattr("src.alphadesk.store.alphadesk_root", lambda: root)
    monkeypatch.setattr("src.alphadesk.profiles.root_for", lambda pid: root)
    monkeypatch.setattr("src.alphadesk.board_paper._dir", lambda: (root / "bp").mkdir(exist_ok=True) or root / "bp")
    monkeypatch.setattr("src.alphadesk.btst.days.list_days", lambda kind="live", limit=None: [])
    return root


def _df(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0.0008, 0.015, n))
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"open": c * (1 + rng.normal(0, 0.003, n)), "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": rng.integers(1e5, 1e6, n).astype(float)}, index=idx)


def test_options_list_builtins_and_lab_strategies_with_colours(home):
    S.create_custom({"name": "Gap up", "rules": {"side": "LONG", "entry": [{"kind": "day_up", "pct": 2}],
                                                 "exit": {"stop_pct": 3}}}, author="sid")
    opts = C.options()
    ids = [o["id"] for o in opts]
    assert ids[:3] == ["signal_board", "kronos", "btst"] and any(i.startswith("lab_") for i in ids)
    assert all(o["color"].startswith("#") for o in opts) and opts[1]["kind"] == "forecast"


def test_lab_and_board_signals_carry_levels_and_outcomes(home, monkeypatch):
    rec = S.create_custom({"name": "Gap up", "rules": {"side": "LONG", "entry": [{"kind": "day_up", "pct": 1}],
                                                       "exit": {"stop_pct": 3, "target_pct": 6, "max_hold_days": 10}}},
                          author="sid")
    monkeypatch.setattr(C, "_bars", lambda symbol, years: _df())
    out = C.signals("testco", [rec["id"], "signal_board", "nope"], 3)
    assert out["symbol"] == "TESTCO" and [s["id"] for s in out["strategies"]] == [rec["id"], "signal_board"]
    lab = out["strategies"][0]
    assert lab["items"], "the lab rule fires on a trending series"
    t = lab["items"][0]
    assert t["source"] == "replay" and t["stop"] < t["entry"] < t["target"] and t["outcome"] in {"target", "stop", "time"}
    assert t["signal_date"] <= t["entry_date"] <= t["exit_date"]
    assert lab["summary"]["signals"] == len(lab["items"])
    board = out["strategies"][1]
    assert board["error"] is None
    for b in board["items"]:
        assert b["off"] == (b["side"] != "LONG")


def test_kronos_log_saves_and_scores_forecasts_on_real_trading_days(home, monkeypatch):
    df = _df(60)
    days = [str(d)[:10] for d in df.index]
    start = float(df["close"].iloc[30])
    payload = {"symbol": "TESTCO", "historical_data": [{"time": days[30], "close": start}],
               "forecast_data": [{"time": f"x{k}", "close": start * 1.05} for k in range(5)],
               "kronos_projection": {"model": "m"}}
    K.record("TESTCO", payload)
    K.record("TESTCO", {**payload, "forecast_data": payload["forecast_data"][:3]}, source="backfill")
    assert len(K.forecasts("TESTCO")) == 1 and K.forecasts("TESTCO")[0]["source"] == "live"
    monkeypatch.setattr(C, "_bars", lambda symbol, years: df)
    k = C.signals("TESTCO", ["kronos"])["strategies"][0]
    f = k["items"][0]
    assert [p["time"] for p in f["path"]] == days[31:36]
    actual_up = df["close"].iloc[35] > start
    assert f["direction_right"] == bool(actual_up) and f["predicted_pct"] == 5.0
    assert k["summary"]["forecasts"] == 1 and k["summary"]["judged"] == 1


def test_backfill_due_days_skip_what_is_saved(home):
    days = [str(d)[:10] for d in pd.bdate_range("2025-01-01", periods=300)]
    due = K._due("TESTCO", days)
    assert due[0] == days[-K.PRED_LEN - 1] and len(due) == len(range(0, K.BACKFILL_DAYS, K.BACKFILL_EVERY))
    K.record("TESTCO", {"symbol": "TESTCO", "historical_data": [{"time": due[0], "close": 1}],
                        "forecast_data": [{"time": "x", "close": 1}]}, source="backfill")
    assert K._due("TESTCO", days)[0] == due[1]
