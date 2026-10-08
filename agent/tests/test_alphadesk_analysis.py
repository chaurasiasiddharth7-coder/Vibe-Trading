"""Tests for alphadesk analysis helpers with mocked OHLCV."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.alphadesk import analysis as analysis_mod


def _fake_ohlcv(n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = np.linspace(100, 120, n) + np.random.randn(n) * 0.3
    open_ = close - 0.2
    high = close + 0.5
    low = close - 0.5
    volume = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_analyze_symbol_mocked(monkeypatch):
    frame = _fake_ohlcv()

    def fake_fetch(symbol, *, period="6mo", interval="1d"):
        return "RELIANCE.NS", frame

    monkeypatch.setattr(analysis_mod, "fetch_india_ohlcv", fake_fetch)
    out = analysis_mod.analyze_symbol("RELIANCE")
    assert out["ticker"] == "RELIANCE.NS"
    assert out["current_price"] > 0
    assert "rsi_14" in out["metrics"]
    assert len(out["chart_data"]) == len(frame)
