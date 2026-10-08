"""Phase 4: manual tiny live unlock, tickets, paper replay."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.alphadesk.cio_schema import CioTradePlan
from src.alphadesk import live_unlock as lu
from src.alphadesk import paper_replay as replay
from src.alphadesk import survival as surv
from src.alphadesk.constitution import assert_live_allowed
from src.alphadesk.store import save_signal


@pytest.fixture()
def alphadesk_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "alphadesk"
    root.mkdir()
    import src.alphadesk.store as store
    import src.alphadesk.constitution as constitution

    monkeypatch.setattr(surv, "alphadesk_root", lambda: root)
    monkeypatch.setattr(store, "alphadesk_root", lambda: root)
    monkeypatch.setattr(constitution, "alphadesk_root", lambda: root)
    monkeypatch.setattr(lu, "alphadesk_root", lambda: root)
    for sub in ("signals", "outcomes", "portfolio", "survival", "cache", "live"):
        (root / sub).mkdir(exist_ok=True)
    return root


def _seed_eligible(engine_id: str = "br_pdh_pdl") -> None:
    surv.reset_engine(engine_id, sleeve_inr=1_000_000)
    for _ in range(30):
        surv.apply_outcome_to_engine(engine_id, r_multiple=0.2, pnl_inr=100)


def test_unlock_requires_expectancy(alphadesk_tmp: Path):
    surv.reset_engine("br_pdh_pdl", sleeve_inr=100_000)
    with pytest.raises(PermissionError):
        lu.grant_unlock("br_pdh_pdl")
    assert not lu.is_expectancy_eligible("br_pdh_pdl")


def test_grant_unlock_and_manual_ticket_gate(alphadesk_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    _seed_eligible("br_pdh_pdl")
    assert lu.is_expectancy_eligible("br_pdh_pdl")
    rec = lu.grant_unlock("br_pdh_pdl", approved_by="test")
    assert rec["unlocked"] is True
    assert lu.is_human_unlocked("br_pdh_pdl")

    plan = CioTradePlan(
        action="BUY",
        confidence_score=60,
        entry_range="100-101",
        target_price=110.0,
        stop_loss=95.0,
        risk_reward_ratio="2:1",
        position_size_percent=1.0,
        holding_horizon="swing",
        executive_summary="test",
        engine_id="br_pdh_pdl",
        invalidation="stop",
    )
    signal_id = save_signal("RELIANCE", plan)

    # No approve + no mandate → blocked
    monkeypatch.setattr(lu, "mandate_ok", lambda broker="dhan": False)
    with pytest.raises(PermissionError):
        lu.issue_manual_ticket(
            engine_id="br_pdh_pdl",
            signal_id=signal_id,
            symbol="RELIANCE",
            side="buy",
            quantity=1,
            notional_inr=1000,
            limit_price=100.0,
        )

    lu.approve_signal(signal_id)
    monkeypatch.setattr(lu, "mandate_ok", lambda broker="dhan": True)
    monkeypatch.setattr(lu, "is_halted", lambda: False)

    ticket = lu.issue_manual_ticket(
        engine_id="br_pdh_pdl",
        signal_id=signal_id,
        symbol="RELIANCE",
        side="buy",
        quantity=2,
        notional_inr=2000,
        limit_price=100.0,
    )
    assert ticket["status"] == "PENDING_MANUAL"
    assert "RELIANCE" in ticket["copyable_order"]
    assert "MANUAL TINY LIVE" in ticket["copyable_order"]

    filled = lu.mark_ticket_filled(ticket["id"], fill_price=100.5, broker_order_id="DHAN-1")
    assert filled["status"] == "FILLED_MANUAL"


def test_tiny_size_blocks(alphadesk_tmp: Path):
    assert not lu.tiny_size_ok(quantity=1, notional_inr=10_000)
    assert not lu.tiny_size_ok(quantity=50, notional_inr=1000)
    assert lu.tiny_size_ok(quantity=2, notional_inr=2000)


def test_constitution_gate():
    with pytest.raises(PermissionError):
        assert_live_allowed(unlocked=True, approved=False, mandate_ok=True)


def test_recovery_blocked(alphadesk_tmp: Path):
    surv.reset_engine("br_pdh_pdl", sleeve_inr=50_000)
    state = surv.load_engine("br_pdh_pdl")
    state["initial_sleeve_inr"] = 100_000
    state["sleeve_inr"] = 50_000
    surv.save_engine(state)
    assert lu.is_recovery_trading("br_pdh_pdl")


def test_paper_replay_br_pdh_pdl(alphadesk_tmp: Path):
    surv.reset_engine("br_pdh_pdl", sleeve_inr=1_000_000)
    # Synthetic trending series with PDH retests
    rows = []
    price = 100.0
    for i in range(80):
        o = price
        h = price + 2 + (i % 3)
        l = price - 1.5
        c = price + (0.5 if i % 2 == 0 else -0.3)
        rows.append({"open": o, "high": h, "low": l, "close": c})
        price = c
    df = pd.DataFrame(rows)
    result = replay.replay_br_pdh_pdl(df, symbol="TEST", max_trades=15, risk_inr=100)
    assert result["ok"] or result.get("reason") == "no_trades_in_sim"
    if result.get("applied", 0) > 0:
        state = surv.load_engine("br_pdh_pdl")
        assert int(state["closed_trades"]) == result["applied"]
