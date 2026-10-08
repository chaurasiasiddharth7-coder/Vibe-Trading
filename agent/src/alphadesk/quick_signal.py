"""Instant Desk signal from analysis + Kronos (no swarm, no Arena thrash)."""

from __future__ import annotations

from typing import Any

from src.alphadesk.cio_schema import CioTradePlan
from src.alphadesk.engines.debate import heuristic_debate_plan
from src.alphadesk.store import save_signal


def build_quick_signal(
    symbol: str,
    *,
    analysis: dict[str, Any] | None = None,
    kronos: dict[str, Any] | None = None,
    persist: bool = True,
) -> tuple[CioTradePlan, str | None]:
    """Return a mentor-ready plan with entry / target / stop always filled."""
    plan = heuristic_debate_plan(symbol, analysis=analysis, kronos=kronos)
    price = float((analysis or {}).get("current_price") or 0)
    if price <= 0:
        price = 100.0
    # Harden: never emit null/zero levels for chart + paper sizing
    target = float(plan.target_price or 0)
    stop = float(plan.stop_loss or 0)
    if target <= 0:
        target = round(price * (1.06 if plan.action == "BUY" else 0.94), 2)
    if stop <= 0:
        stop = round(price * (0.97 if plan.action == "BUY" else 1.03), 2)
    entry = plan.entry_range or f"₹{price * 0.995:,.0f} - ₹{price * 1.005:,.0f}"
    horizon = _default_horizon(plan.action, price, target, kronos)
    plan = plan.model_copy(
        update={
            "target_price": target,
            "stop_loss": stop,
            "entry_range": entry,
            "holding_horizon": horizon,
            "engine_id": "quick_signal",
            "executive_summary": (
                f"Auto signal · entry {entry} · exit ₹{target:,.2f} / stop ₹{stop:,.2f} · {horizon}. "
                f"{plan.executive_summary}"
            ),
        }
    )
    signal_id: str | None = None
    if persist:
        signal_id = save_signal(
            symbol,
            plan,
            extras={"source": "quick_signal", "analysis": analysis, "kronos": kronos},
        )
    return plan, signal_id


def _default_horizon(
    action: str,
    price: float,
    target: float,
    kronos: dict[str, Any] | None,
) -> str:
    if action in {"WATCH", "HOLD"}:
        return "monitor"
    dist = abs(target - price) / max(price, 1e-9) * 100.0
    k = ((kronos or {}).get("kronos_projection") or {}).get("expected_change_percent")
    try:
        k_chg = abs(float(k)) if k is not None else 0.0
    except (TypeError, ValueError):
        k_chg = 0.0
    if dist <= 3.5 or (k_chg >= 4.0 and dist <= 7.0):
        return "1 week"
    if dist <= 8.0 or k_chg >= 2.0:
        return "1 month"
    if dist <= 15.0:
        return "3 months"
    return "6 months+"
