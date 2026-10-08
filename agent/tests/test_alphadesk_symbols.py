"""Unit tests for AlphaDesk symbol helpers and indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.alphadesk.indicators import detect_candle_patterns, ema, rsi
from src.alphadesk.symbols import (
    interval_to_loader,
    normalize_india_symbol,
    period_to_dates,
)


def test_normalize_bare_to_ns():
    assert normalize_india_symbol("reliance") == "RELIANCE.NS"


def test_normalize_preserves_bo():
    assert normalize_india_symbol("500325.BO") == "500325.BO"


def test_normalize_tatamotors_alias_to_tmpv():
    assert normalize_india_symbol("TATAMOTORS") == "TMPV.NS"
    assert normalize_india_symbol("tatamotors.ns") == "TMPV.NS"


def test_normalize_ampersand_tickers():
    assert normalize_india_symbol("M&M") == "M&M.NS"


def test_normalize_rejects_empty():
    with pytest.raises(ValueError):
        normalize_india_symbol("  ")


def test_period_to_dates_6mo():
    start, end = period_to_dates("6mo")
    assert len(start) == 10 and len(end) == 10
    assert start < end


def test_interval_to_loader():
    assert interval_to_loader("1d") == "1D"
    with pytest.raises(ValueError):
        interval_to_loader("2d")


def test_ema_rsi_finite():
    close = pd.Series(np.linspace(100, 120, 60) + np.random.randn(60) * 0.5)
    e = ema(close, 20)
    r = rsi(close, 14)
    assert e.iloc[-1] == e.iloc[-1]
    assert 0 <= float(r.iloc[-1]) <= 100


def test_detect_doji_like():
    # Flat body candle
    o = pd.Series([100.0, 100.0])
    h = pd.Series([101.0, 101.0])
    l = pd.Series([99.0, 99.0])
    c = pd.Series([100.5, 100.05])
    labels = detect_candle_patterns(o, h, l, c)
    assert any("DOJI" in x for x in labels)
