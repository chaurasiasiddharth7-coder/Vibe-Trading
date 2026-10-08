"""Break-retest engine → CioTradePlan (Arena-facing)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.alphadesk.cio_schema import Agreement, CioTradePlan
from src.alphadesk.engines.break_retest import (
    LevelSetup,
    detect_orb,
    detect_order_block,
    detect_pdh_pdl,
    levels_payload,
    scan_all_variants,
)
from src.alphadesk.engines.break_retest_backtest import gate_all
from src.alphadesk.survival import is_engine_tradable, load_engine

ENGINE_BY_VARIANT = {
    "pdh_pdl": "br_pdh_pdl",
    "order_block": "br_order_block",
    "orb": "br_orb",
}


def setup_to_plan(setup: LevelSetup, *, gate_pass: bool) -> CioTradePlan:
    engine_id = ENGINE_BY_VARIANT[setup.variant]
    state = load_engine(engine_id)
    action = setup.side if gate_pass and setup.side != "WATCH" else "WATCH"
    if not gate_pass and setup.side != "WATCH":
        summary = (
            f"{setup.level_name} setup found but backtest gate failed — WATCH only. "
            f"{setup.notes}"
        )
        conf = 40
    elif setup.side == "WATCH":
        summary = setup.notes
        conf = 45
    else:
        summary = f"{setup.notes} · R:R≥{setup.risk_reward}"
        conf = 72
    return CioTradePlan(
        action=action,  # type: ignore[arg-type]
        confidence_score=conf,
        entry_range=f"₹{setup.entry:,.2f}",
        target_price=float(setup.target) or 1.0,
        stop_loss=float(setup.stop) or 1.0,
        risk_reward_ratio=f"1:{setup.risk_reward}" if setup.risk_reward else "1:0",
        position_size_percent=0.0 if action == "WATCH" else 1.5,
        holding_horizon="1-10 sessions",
        executive_summary=summary,
        conviction_drivers=[setup.variant, setup.level_name],
        disconfirming_evidence=[] if gate_pass else ["backtest_gate_fail"],
        invalidation=f"Stop beyond {setup.level_name} @ {setup.stop}",
        agreement=Agreement(
            ta="bull" if action == "BUY" else ("bear" if action == "SELL" else "neutral"),
            kronos="neutral",
            fundamental="ok",
            aligned=True,
        ),
        do_not_trade_if=["gate_fail", "r_less_than_2", "engine_dead"],
        engine_id=engine_id,
        arena_score=0,
        survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
    )


def proposals_from_frame(df: pd.DataFrame) -> tuple[list[CioTradePlan], dict[str, Any]]:
    """Build gated BR proposals + diagnostics."""
    gates = gate_all(df)
    setups = scan_all_variants(df)
    plans: list[CioTradePlan] = []
    for setup in setups:
        engine_id = ENGINE_BY_VARIANT[setup.variant]
        if not is_engine_tradable(engine_id):
            continue
        g = gates.get(setup.variant) or {}
        # ORB on daily: allow WATCH always; actionable only if gate pass
        gate_pass = bool(g.get("pass"))
        if setup.variant == "orb" and setup.side != "WATCH" and not gate_pass:
            gate_pass = False
        plans.append(setup_to_plan(setup, gate_pass=gate_pass))
    meta = {
        "gates": gates,
        "levels": levels_payload(setups),
    }
    return plans, meta


def best_actionable(df: pd.DataFrame) -> tuple[CioTradePlan | None, dict[str, Any]]:
    plans, meta = proposals_from_frame(df)
    actionable = [p for p in plans if p.action in {"BUY", "SELL", "TRIM"}]
    if not actionable:
        return (plans[0] if plans else None), meta
    actionable.sort(key=lambda p: p.confidence_score, reverse=True)
    return actionable[0], meta
