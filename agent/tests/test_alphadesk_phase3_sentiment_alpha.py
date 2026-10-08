"""Phase 3 sentiment + alpha forge unit tests (offline)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.alphadesk.engines.sentiment_engine import proposal_for_symbol, score_headlines
from src.alphadesk import survival as surv


@pytest.fixture()
def alphadesk_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "alphadesk"
    root.mkdir()
    monkeypatch.setattr(surv, "alphadesk_root", lambda: root)
    import src.alphadesk.store as store

    monkeypatch.setattr(store, "alphadesk_root", lambda: root)
    for sub in ("signals", "outcomes", "portfolio", "survival", "cache"):
        (root / sub).mkdir()
    surv.ensure_engines(["sentiment", "alpha_forge"])
    return root


def test_lexicon_bullish_bearish():
    bull = score_headlines(["Company beats estimates with strong growth and upgrade"])
    bear = score_headlines(["Shares fall after fraud probe and dividend cut"])
    assert bull["score"] > 0
    assert bear["score"] < 0


def test_sentiment_proposal_tilt(alphadesk_tmp: Path):
    plan = proposal_for_symbol(
        "RELIANCE",
        price=2800,
        headlines=["Reliance wins big order; profit surge and expansion deal"],
    )
    assert plan.engine_id == "sentiment"
    assert plan.action in {"BUY", "WATCH"}
    assert plan.position_size_percent <= 1.0


def test_sentiment_empty_is_watch(alphadesk_tmp: Path):
    plan = proposal_for_symbol("FOO", price=100, headlines=[])
    assert plan.action == "WATCH"


def test_alpha_forge_gate_offline(alphadesk_tmp: Path):
    from src.alphadesk.engines.alpha_forge import proposal_for_symbol as alpha_prop

    dates = pd.bdate_range("2023-01-01", periods=90)
    close = [100 + i * 0.2 for i in range(90)]
    df = pd.DataFrame(
        {
            "open": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "close": close,
            "volume": [1e6] * 90,
        },
        index=dates,
    )
    plan = alpha_prop("RELIANCE", df, price=close[-1])
    assert plan.engine_id == "alpha_forge"
    assert plan.action in {"BUY", "SELL", "WATCH", "HOLD", "TRIM"}
