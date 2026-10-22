"""Tests for board paper-trade exits and sizing."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.alphadesk import board_paper
from src.alphadesk.board_paper import simulate_exit
from src.alphadesk.signal_board import IST, expected_session


def _bar(t, high, low, close):
    return {"time": t, "high": high, "low": low, "close": close}


def test_long_hits_target():
    out = simulate_exit([_bar("d1", 103, 99, 102), _bar("d2", 107, 101, 106)],
                        side="LONG", entry=100, stop=97, target=106, cost_pct=0)
    assert out["exit_reason"] == "target"
    assert out["r_multiple"] == 2.0
    assert out["bars_held"] == 2


def test_same_bar_stop_and_target_counts_as_stop():
    out = simulate_exit([_bar("d1", 107, 96, 100)],
                        side="LONG", entry=100, stop=97, target=106, cost_pct=0)
    assert out["exit_reason"] == "stop"
    assert out["r_multiple"] == -1.0


def test_short_hits_target_and_cost_is_charged():
    out = simulate_exit([_bar("d1", 101, 93, 94)],
                        side="SHORT", entry=100, stop=103, target=94, cost_pct=0.3)
    assert out["exit_reason"] == "target"
    assert out["r_multiple"] == pytest.approx((6 - 0.3) / 3, abs=1e-4)


def test_time_exit_and_still_open():
    bars = [_bar(f"d{i}", 101, 99, 100.5) for i in range(3)]
    assert simulate_exit(bars, side="LONG", entry=100, stop=97, target=106, max_bars=5) is None
    out = simulate_exit(bars, side="LONG", entry=100, stop=97, target=106, max_bars=3, cost_pct=0)
    assert out["exit_reason"] == "time"
    assert out["r_multiple"] == pytest.approx(0.5 / 3, abs=1e-4)


def test_open_trade_sizes_by_risk_and_blocks_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(board_paper, "_dir", lambda: tmp_path)
    row = {"symbol": "TESTCO", "side": "LONG", "entry": 100.0, "stop": 97.0,
           "target": 106.0, "as_of": "2026-10-13"}
    trade = board_paper.open_trade(row, risk_inr=1000)
    assert trade["qty"] == 333
    with pytest.raises(ValueError):
        board_paper.open_trade(row)
    with pytest.raises(ValueError):
        board_paper.open_trade({**row, "symbol": "X", "side": "NO_EDGE"})


def test_expected_session_handles_weekend_and_premarket():
    assert expected_session(datetime(2026, 10, 14, 1, 36, tzinfo=IST)) == "2026-10-13"
    assert expected_session(datetime(2026, 10, 14, 10, 0, tzinfo=IST)) == "2026-10-14"
    assert expected_session(datetime(2026, 10, 18, 12, 0, tzinfo=IST)) == "2026-10-16"


def test_with_live_prices_marks_open_trades_only():
    trades = [
        {"symbol": "A", "side": "LONG", "entry": 100.0, "stop": 97.0, "target": 106.0, "qty": 10,
         "status": "OPEN", "opened_bar": "d0"},
        {"symbol": "B", "side": "SHORT", "entry": 50.0, "stop": 53.0, "target": 44.0, "qty": 4,
         "status": "OPEN", "opened_bar": "d0"},
        {"symbol": "C", "side": "LONG", "entry": 10.0, "qty": 1, "status": "CLOSED"},
    ]
    bars = {
        "A": [{"time": "d1", "open": 101, "high": 104, "low": 100, "close": 103.5}],
        "B": [{"time": "d1", "open": 51, "high": 52.5, "low": 50.5, "close": 52.0}],
    }
    out = board_paper.with_live_prices(trades, load_bars=lambda s: bars[s])
    assert out[0]["current_price"] == 103.5 and out[0]["unrealized_inr"] == 35.0
    assert out[0]["stop_now"] == 97.0 and out[0]["plan"] == "fixed"
    assert out[1]["unrealized_inr"] == -8.0
    assert "current_price" not in out[2]


def test_open_trade_snapshots_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(board_paper, "_dir", lambda: tmp_path)
    row = {"symbol": "TESTCO", "side": "LONG", "entry": 100.0, "stop": 97.0, "target": 106.0,
           "as_of": "2026-10-13", "votes": {"trend": 1}, "score": 3, "name": "Test Co", "industry": "IT",
           "reasons": ["SuperTrend bullish"], "cautions": [], "rsi": 55.0, "kronos_change_pct": 2.1,
           "history": {"symbol_side": {"trades": 9, "win_rate": 0.5, "avg_r": 0.2}}}
    trade = board_paper.open_trade(row)
    assert trade["decision"]["name"] == "Test Co"
    assert trade["decision"]["history"]["trades"] == 9
    assert trade["decision"]["kronos_change_pct"] == 2.1


def test_gap_through_stop_fills_at_open():
    bar = {"time": "d1", "open": 95.0, "high": 96.0, "low": 94.0, "close": 95.5}
    out = simulate_exit([bar], side="LONG", entry=100, stop=97, target=106, cost_pct=0)
    assert out["exit_reason"] == "stop"
    assert out["exit_price"] == 95.0
    assert out["r_multiple"] == pytest.approx(-5 / 3, abs=1e-4)


def test_short_gap_through_target_fills_at_open():
    bar = {"time": "d1", "open": 92.0, "high": 93.0, "low": 91.0, "close": 92.5}
    out = simulate_exit([bar], side="SHORT", entry=100, stop=103, target=94, cost_pct=0)
    assert out["exit_reason"] == "target"
    assert out["exit_price"] == 92.0


def _rising(n, start=100.0, step=1.0):
    return [{"time": f"d{i:02d}", "open": start + i * step, "high": start + i * step + 0.5,
             "low": start + i * step - 0.5, "close": start + i * step} for i in range(1, n + 1)]


def test_trailing_stop_ratchets_up_and_locks_profit():
    bars = _rising(5) + [{"time": "d06", "open": 104, "high": 104.2, "low": 100.0, "close": 100.5}]
    w = board_paper.walk_trade(bars, side="LONG", entry=100, stop=97, target=120, atr=2.0,
                               plans=[{"plan": "trail", "from": None}], cost_pct=0)
    # best high before d06 is 105.5 -> trail 105.5 - 3.0 = 102.5; d06 low 100 hits it
    assert w["exit"]["exit_reason"] == "trail"
    assert w["exit"]["exit_price"] == 102.5
    assert w["exit"]["r_multiple"] > 0


def test_fixed_plan_never_moves_stop():
    w = board_paper.walk_trade(_rising(5), side="LONG", entry=100, stop=97, target=120, atr=2.0,
                               plans=[{"plan": "fixed", "from": None}], cost_pct=0)
    assert w["exit"] is None and w["stop_now"] == 97.0 and w["best"] == 105.5


def test_breakeven_moves_stop_to_entry_after_one_r():
    w = board_paper.walk_trade(_rising(4), side="LONG", entry=100, stop=97, target=120, atr=2.0,
                               plans=[{"plan": "breakeven", "from": None}], cost_pct=0)
    assert w["stop_now"] == 100.0


def test_short_trailing_stop_moves_down():
    bars = [{"time": f"d{i}", "open": 100 - i, "high": 100 - i + 0.5, "low": 100 - i - 0.5, "close": 100 - i}
            for i in range(1, 5)]
    w = board_paper.walk_trade(bars, side="SHORT", entry=100, stop=103, target=80, atr=2.0,
                               plans=[{"plan": "trail", "from": None}], cost_pct=0)
    assert w["stop_now"] == 95.5 + 3.0  # best low 95.5 + 1.5*ATR


def test_plan_switch_applies_only_after_its_session():
    bars = _rising(5)
    w = board_paper.walk_trade(bars, side="LONG", entry=100, stop=97, target=120, atr=2.0,
                               plans=[{"plan": "fixed", "from": None}, {"plan": "trail", "from": "d05"}],
                               cost_pct=0)
    assert w["stop_now"] == 97.0  # trail rule only governs bars after d05
    bars.append({"time": "d06", "open": 106, "high": 106.5, "low": 105.5, "close": 106})
    w = board_paper.walk_trade(bars, side="LONG", entry=100, stop=97, target=120, atr=2.0,
                               plans=[{"plan": "fixed", "from": None}, {"plan": "trail", "from": "d05"}],
                               cost_pct=0)
    assert w["stop_now"] == 106.5 - 3.0


def test_set_plan_rejects_closed_and_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(board_paper, "_dir", lambda: tmp_path)
    row = {"symbol": "TESTCO", "side": "LONG", "entry": 100.0, "stop": 97.0, "target": 106.0, "as_of": "d0"}
    trade = board_paper.open_trade(row, plan="trail")
    assert trade["plans"][0]["plan"] == "trail"
    updated = board_paper.set_plan(trade["id"], "fixed")
    assert board_paper.current_plan(updated) == "fixed"
    with pytest.raises(ValueError):
        board_paper.set_plan(trade["id"], "nope")


def test_set_plan_rejects_non_uuid_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(board_paper, "_dir", lambda: tmp_path)
    with pytest.raises(FileNotFoundError):
        board_paper.set_plan("../../survival/pm", "trail")


def _stub_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(board_paper, "_dir", lambda: tmp_path)
    monkeypatch.setattr("src.alphadesk.store.record_outcome", lambda *a, **k: None)
    monkeypatch.setattr("src.alphadesk.survival.apply_outcome_to_engine", lambda *a, **k: None)
    monkeypatch.setattr("src.alphadesk.signal_board.expected_session", lambda *a, **k: "d1")


def _open(monkeypatch, tmp_path, side="LONG", entry=100.0, stop=97.0, target=106.0):
    _stub_ledger(monkeypatch, tmp_path)
    return board_paper.open_trade(
        {"symbol": "T", "side": side, "entry": entry, "stop": stop, "target": target, "as_of": "d0"}, risk_inr=300
    )


def test_set_stop_only_tightens_and_applies_next_session(tmp_path, monkeypatch):
    trade = _open(monkeypatch, tmp_path)
    bars = [{"time": "d1", "open": 101, "high": 104, "low": 100, "close": 103.5}]
    with pytest.raises(ValueError):
        board_paper.set_stop(trade["id"], 96.0, load_bars=lambda s: bars)  # looser than 97
    with pytest.raises(ValueError):
        board_paper.set_stop(trade["id"], 104.0, load_bars=lambda s: bars)  # above price
    saved = board_paper.set_stop(trade["id"], 100.0, load_bars=lambda s: bars)
    assert saved["stop_moves"][0]["price"] == 100.0
    later = bars + [{"time": "d2", "open": 102, "high": 103, "low": 99.5, "close": 100.5}]
    walked = board_paper._walk(saved, later)
    assert walked["exit"]["exit_price"] == 100.0  # d2 hit the moved stop, d1 did not


def test_exit_now_closes_at_last_close_as_manual(tmp_path, monkeypatch):
    trade = _open(monkeypatch, tmp_path)
    bars = [{"time": "d1", "open": 101, "high": 104, "low": 100, "close": 103.0}]
    closed = board_paper.exit_now(trade["id"], load_bars=lambda s: bars)
    assert closed["status"] == "CLOSED" and closed["exit_reason"] == "manual"
    assert closed["exit_price"] == 103.0 and closed["bars_held"] == 1
    with pytest.raises(ValueError):
        board_paper.exit_now(trade["id"], load_bars=lambda s: bars)


def test_review_flags_giveback_vs_never_worked():
    base = {"side": "LONG", "entry": 100.0, "stop": 97.0, "target": 106.0, "opened_bar": "d0",
            "exit_reason": "stop", "r_multiple": -1.05, "exit_date": "d3", "bars_held": 3}
    gave_back = [
        {"time": "d1", "open": 100, "high": 103, "low": 99, "close": 102},
        {"time": "d2", "open": 102, "high": 102.5, "low": 98.5, "close": 99},
        {"time": "d3", "open": 98, "high": 98.5, "low": 96, "close": 96.5},
    ]
    rv = board_paper.review_closed(base, gave_back)
    assert rv["best_move_r"] >= 1.0 and "reversed" in rv["what_happened"]
    never = [{"time": "d1", "open": 99, "high": 99.5, "low": 96.5, "close": 97}]
    rv = board_paper.review_closed({**base, "exit_date": "d1", "bars_held": 1}, never)
    assert "never really worked" in rv["what_happened"]


def test_suggest_exit_branches():
    t = {"side": "LONG", "entry": 100.0, "stop": 97.0, "plan": "fixed"}
    assert board_paper.suggest_exit(t, 104.0, 97.0, 104.0)["action"] == "trail"
    assert board_paper.suggest_exit(t, 101.0, 97.0, 104.0)["action"] == "exit"
    assert board_paper.suggest_exit(t, 101.0, 97.0, 101.0)["action"] == "hold"


def test_save_is_atomic_and_backup_runs_once_a_day(tmp_path, monkeypatch):
    trade = _open(monkeypatch, tmp_path)
    assert not list(tmp_path.glob("*.tmp"))
    first = board_paper.backup_ledger()
    assert first is not None and (first / f"{trade['id']}.json").is_file()
    assert board_paper.backup_ledger() is None  # already done today
    for i in range(20):  # old days are pruned
        (tmp_path / "_backups" / f"2026-01-{i + 1:02d}").mkdir()
    board_paper.backup_ledger(keep=5)
    assert len(list((tmp_path / "_backups").iterdir())) <= 5


def test_suspect_move_flags_big_jumps_inside_window_only():
    bars = [
        {"time": "d0", "close": 100}, {"time": "d1", "close": 101},
        {"time": "d2", "close": 80}, {"time": "d3", "close": 81},
    ]
    assert board_paper.suspect_move(bars, after="d0") == {"date": "d2", "change_pct": -20.8}
    assert board_paper.suspect_move(bars, after="d2") is None
    assert board_paper.suspect_move(bars, after="d0", until="d1") is None


def test_list_trades_skips_hidden_and_corrupt_files(tmp_path, monkeypatch):
    trade = _open(monkeypatch, tmp_path)
    (tmp_path / "._junk.json").write_bytes(b"\x00\x05\x16\x07\xa3\xff")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "binary.json").write_bytes(b"\xa3\xa3\xa3")
    assert [t["id"] for t in board_paper.list_trades()] == [trade["id"]]
