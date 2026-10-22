"""Tests for the signal board classifier."""

from __future__ import annotations

from src.alphadesk.signal_board import classify


def _analysis(*, st="BULLISH", macd="Bullish Crossover", rsi=55.0, ema50=95.0, ema200=90.0, atr=2.0):
    return {
        "symbol": "TESTCO",
        "current_price": 100.0,
        "change_percent": 0.5,
        "metrics": {
            "supertrend": st,
            "macd_status": macd,
            "rsi_14": rsi,
            "ema_50": ema50,
            "ema_200": ema200,
            "atr": atr,
        },
    }


def _kronos(chg):
    return {"kronos_projection": {"expected_change_percent": chg}}


def test_long_when_all_votes_bull_with_atr_levels():
    row = classify("TESTCO", _analysis(), _kronos(3.0))
    assert row["side"] == "LONG"
    assert row["score"] == 4
    assert row["stop"] == 97.0
    assert row["target"] == 106.0


def test_overbought_long_is_not_chased():
    row = classify("TESTCO", _analysis(rsi=78), _kronos(3.0))
    assert row["side"] == "NO_EDGE"
    assert any("stretched" in c for c in row["cautions"])


def test_short_when_all_votes_bear():
    row = classify(
        "TESTCO",
        _analysis(st="BEARISH", macd="Bearish Crossover", rsi=45, ema50=105, ema200=110),
        _kronos(-2.5),
    )
    assert row["side"] == "SHORT"
    assert row["stop"] == 103.0
    assert row["target"] == 94.0
    assert row["short_note"]


def test_bearish_etf_is_steer_clear_not_short():
    row = classify(
        "NIFTYBEES",
        _analysis(st="BEARISH", macd="Bearish Crossover", rsi=45, ema50=105, ema200=110),
        _kronos(-2.5),
    )
    assert row["side"] == "STEER_CLEAR"


def test_mixed_votes_are_no_edge():
    row = classify("TESTCO", _analysis(macd="Bearish Crossover"), _kronos(0.2))
    assert row["score"] == 1
    assert row["side"] == "NO_EDGE"


def test_missing_atr_blocks_levels():
    row = classify("TESTCO", _analysis(atr=None), _kronos(3.0))
    assert row["side"] == "NO_EDGE"
    assert row["entry"] is None
