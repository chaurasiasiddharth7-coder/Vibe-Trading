"""Replay break-retest PDH/PDL backtest R-multiples into paper outcomes.

Honest expectancy farming (plan option C): uses the same simulator as the
Arena gate. Tagged ``source=backtest_paper`` — never invents wins.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any
from pathlib import Path

from src.alphadesk.cio_schema import CioTradePlan
from src.alphadesk.engines.break_retest_backtest import _sim_pdh_pdl
from src.alphadesk.store import record_outcome, save_signal
from src.alphadesk.survival import apply_outcome_to_engine, load_engine, scorecard_row

ENGINE_ID = "br_pdh_pdl"
DEFAULT_RISK_INR = 500.0  # sleeve risk unit for pnl_inr ≈ r * risk
REPLAY_LEDGER_PATH = Path.home() / ".vibe-trading" / "alphadesk" / "replay_ledger.json"


def _load_replay_ledger() -> dict[str, Any]:
    """Load the replay ledger from disk, creating it if it doesn't exist."""
    try:
        if REPLAY_LEDGER_PATH.is_file():
            return json.loads(REPLAY_LEDGER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {"replays": []}


def _save_replay_ledger(ledger: dict[str, Any]) -> None:
    """Save the replay ledger to disk."""
    REPLAY_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPLAY_LEDGER_PATH.write_text(
        json.dumps(ledger, indent=2), encoding="utf-8"
    )


def _get_replay_key(df: Any, symbol: str) -> str:
    """Generate a unique key for a (symbol, date range) tuple."""
    try:
        # Assuming df has a datetime index
        if hasattr(df, 'index') and len(df.index) >= 2:
            start_date = df.index[0].isoformat() if hasattr(df.index[0], 'isoformat') else str(df.index[0])
            end_date = df.index[-1].isoformat() if hasattr(df.index[-1], 'isoformat') else str(df.index[-1])
            key_data = f"{symbol}:{start_date}:{end_date}"
        else:
            # Fallback if we can't determine date range
            key_data = f"{symbol}:{id(df)}"
        return hashlib.sha256(key_data.encode('utf-8')).hexdigest()
    except Exception:
        # Fallback to a simple hash if anything goes wrong
        return hashlib.sha256(f"{symbol}:{id(df)}".encode('utf-8')).hexdigest()


def _has_been_replayed(df: Any, symbol: str) -> bool:
    """Check if this (symbol, date range) has already been replayed."""
    ledger = _load_replay_ledger()
    replay_key = _get_replay_key(df, symbol)
    return replay_key in ledger.get("replays", [])


def _mark_as_replayed(df: Any, symbol: str) -> None:
    """Mark this (symbol, date range) as having been replayed."""
    ledger = _load_replay_ledger()
    replay_key = _get_replay_key(df, symbol)
    if "replays" not in ledger:
        ledger["replays"] = []
    if replay_key not in ledger["replays"]:
        ledger["replays"].append(replay_key)
        _save_replay_ledger(ledger)


def replay_br_pdh_pdl(
    df: Any,
    *,
    symbol: str = "REPLAY",
    max_trades: int = 40,
    risk_inr: float = DEFAULT_RISK_INR,
    skip_if_n_ge: int | None = None,
) -> dict[str, Any]:
    """Apply up to ``max_trades`` simulated R outcomes to ``br_pdh_pdl``.

    If ``skip_if_n_ge`` is set and the engine already has that many closed
    trades, no-op (avoids double-farming).
    """
    # Prevent repeated in-sample simulations from adding duplicate paper outcomes
    if _has_been_replayed(df, symbol):
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_replayed",
            "engine": scorecard_row(load_engine(ENGINE_ID)),
            "applied": 0,
        }

    state = load_engine(ENGINE_ID)
    n_before = int(state.get("closed_trades") or 0)
    if skip_if_n_ge is not None and n_before >= skip_if_n_ge:
        return {
            "ok": True,
            "skipped": True,
            "reason": f"already N≥{skip_if_n_ge}",
            "engine": scorecard_row(state),
            "applied": 0,
        }

    rs = _sim_pdh_pdl(df)
    if not rs:
        return {
            "ok": False,
            "skipped": False,
            "reason": "no_trades_in_sim",
            "engine": scorecard_row(state),
            "applied": 0,
        }

    applied = 0
    room = max(0, max_trades)
    for r in rs[:room]:
        plan = CioTradePlan(
            action="BUY",
            confidence_score=55,
            entry_range="backtest_replay",
            target_price=0.0,
            stop_loss=0.0,
            risk_reward_ratio="2:1",
            position_size_percent=1.0,
            holding_horizon="swing",
            executive_summary=f"backtest_paper replay {symbol}",
            engine_id=ENGINE_ID,
            invalidation="n/a",
            conviction_drivers=["pdh_pdl backtest replay"],
        )
        signal_id = save_signal(
            symbol,
            plan,
            extras={
                "source": "backtest_paper",
                "replay_id": str(uuid.uuid4()),
            },
        )
        pnl = float(r) * float(risk_inr)
        record_outcome(
            signal_id,
            {
                "engine_id": ENGINE_ID,
                "r_multiple": float(r),
                "pnl_inr": pnl,
                "symbol": symbol,
                "source": "backtest_paper",
                "notes": "br_pdh_pdl sim replay",
            },
        )
        apply_outcome_to_engine(ENGINE_ID, r_multiple=float(r), pnl_inr=pnl)
        applied += 1

    # Mark this (symbol, date range) as having been replayed
    _mark_as_replayed(df, symbol)

    return {
        "ok": True,
        "skipped": False,
        "reason": "ok",
        "applied": applied,
        "sim_trades_available": len(rs),
        "engine": scorecard_row(load_engine(ENGINE_ID)),
        "n_before": n_before,
    }
