"""Tests for NSE universe lists and the board's Kronos pre-filter."""

from __future__ import annotations

from src.alphadesk import index_lists, signal_board

CSV = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "ABB India Ltd.,Capital Goods,ABB,EQ,INE117A01022\n"
    "Bajaj Auto Ltd.,Automobile and Auto Components,BAJAJ-AUTO,EQ,INE917I01010\n"
    "Odd Ltd.,Other,ODD,BE,INE000000000\n"
    "Placeholder,Other,DUMMYHEG,EQ,INE000000001\n"
)


def test_parse_keeps_eq_series_with_name_and_industry():
    rows = index_lists._parse(CSV)
    assert [r["symbol"] for r in rows] == ["ABB", "BAJAJ-AUTO"]
    assert rows[1]["industry"] == "Automobile and Auto Components"


def test_members_falls_back_to_saved_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(index_lists, "_dir", lambda: tmp_path)
    (tmp_path / "nifty50.csv").write_text(CSV, encoding="utf-8")

    def boom(_file):
        raise OSError("offline")

    monkeypatch.setattr(index_lists, "MAX_AGE_S", -1)
    monkeypatch.setattr(index_lists, "_fetch_csv", boom)
    assert [m["symbol"] for m in index_lists.members("nifty50")] == ["ABB", "BAJAJ-AUTO"]


def test_scan_list_adds_funds_but_not_liquid(monkeypatch):
    monkeypatch.setattr(index_lists, "members", lambda key: [{"symbol": "ABB", "name": "ABB", "industry": "x"}])
    syms = [m["symbol"] for m in index_lists.scan_list("nifty50", include_funds=True)]
    assert "ABB" in syms and "NIFTYBEES" in syms and "LIQUIDBEES" not in syms
    assert [m["symbol"] for m in index_lists.scan_list("nifty50", include_funds=False)] == ["ABB"]


def _analysis(st, macd, ema50):
    return {
        "symbol": "X",
        "current_price": 100.0,
        "metrics": {"supertrend": st, "macd_status": macd, "rsi_14": 50, "ema_50": ema50, "ema_200": 90 if ema50 < 100 else 110, "atr": 2.0},
        "chart_data": [{"time": "2026-10-13"}],
    }


def test_kronos_runs_only_when_price_votes_lean(monkeypatch):
    data = {
        "LEAN": _analysis("BULLISH", "Bullish Crossover", 95),  # 3 price votes up
        "MIXED": _analysis("BULLISH", "Bearish Crossover", 105),  # +1 -1 -1
    }
    called: list[str] = []
    monkeypatch.setattr(signal_board, "_kronos_available", lambda: True)
    monkeypatch.setattr(signal_board, "_load_analysis", lambda s: {**data[s], "symbol": s})

    def fake_kronos(sym):
        called.append(sym)
        return {"kronos_projection": {"expected_change_percent": 3.0}}

    monkeypatch.setattr(signal_board, "_load_kronos", fake_kronos)
    monkeypatch.setattr("src.alphadesk.board_replay.load_replay", lambda key="core": None)
    monkeypatch.setattr(signal_board, "track_record", lambda: {"verdict": "untested"})
    board = signal_board.build_board(
        members=[{"symbol": "LEAN", "name": "Lean Ltd", "industry": "IT"}, {"symbol": "MIXED", "name": "M", "industry": "IT"}]
    )
    assert called == ["LEAN"]
    lean = next(r for r in board["rows"] if r["symbol"] == "LEAN")
    assert lean["side"] == "LONG" and lean["score"] == 4 and lean["name"] == "Lean Ltd"


def test_short_price_history_is_flagged_from_past_test(monkeypatch):
    monkeypatch.setattr(signal_board, "_kronos_available", lambda: False)
    monkeypatch.setattr(signal_board, "_load_analysis", lambda s: {**_analysis("BULLISH", "Bullish Crossover", 95), "symbol": s})
    monkeypatch.setattr(
        "src.alphadesk.board_replay.load_replay",
        lambda key="core": {"by_symbol": {}, "bars": {"NEWCO": 210, "OLDCO": 1200}},
    )
    monkeypatch.setattr(signal_board, "track_record", lambda: {"verdict": "untested"})
    board = signal_board.build_board(
        members=[{"symbol": "NEWCO", "name": "N", "industry": "x"}, {"symbol": "OLDCO", "name": "O", "industry": "x"}]
    )
    rows = {r["symbol"]: r for r in board["rows"]}
    assert any("listed recently: about 10 months" in c for c in rows["NEWCO"]["cautions"])
    assert not any("listed recently" in c for c in rows["OLDCO"]["cautions"])
