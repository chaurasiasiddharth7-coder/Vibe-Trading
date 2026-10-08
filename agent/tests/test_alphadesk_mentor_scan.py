"""Tests for quick signal + opportunity horizon scan."""

from __future__ import annotations

from src.alphadesk.opportunity_scan import _horizon_from_plan, _score_plan
from src.alphadesk.quick_signal import build_quick_signal


def test_quick_signal_always_has_levels():
    analysis = {
        "current_price": 100.0,
        "metrics": {
            "rsi_14": 55,
            "supertrend": "BULLISH",
            "ema_50": 98,
        },
    }
    kronos = {
        "kronos_projection": {
            "trajectory_bias": "BULLISH",
            "expected_change_percent": 4.2,
        }
    }
    plan, sid = build_quick_signal(
        "TESTCO", analysis=analysis, kronos=kronos, persist=False
    )
    assert sid is None
    assert plan.target_price > 0
    assert plan.stop_loss > 0
    assert plan.entry_range
    assert plan.action in {"BUY", "SELL", "WATCH", "HOLD", "TRIM"}


def test_horizon_buckets():
    assert _horizon_from_plan("BUY", 100, 102, 5.0) == "1 week"
    assert _horizon_from_plan("BUY", 100, 107, 1.0) == "1 month"
    assert _horizon_from_plan("BUY", 100, 112, 0.5) == "3 months"
    assert _horizon_from_plan("WATCH", 100, 110, 3.0) == "monitor"
    assert _horizon_from_plan("SELL", 100, 90, 0) == "avoid"


def test_score_prefers_aligned_buy():
    buy = _score_plan("BUY", 60, True, 3.0)
    watch = _score_plan("WATCH", 60, False, 3.0)
    assert buy > watch
