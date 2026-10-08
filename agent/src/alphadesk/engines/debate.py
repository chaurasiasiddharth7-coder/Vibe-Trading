"""Debate engine wrapper — slim india_fund_committee, capped universe."""

from __future__ import annotations

from typing import Any

from src.alphadesk.cio_schema import Agreement, CioTradePlan, parse_cio_json
from src.alphadesk.survival import is_engine_tradable, load_engine

DEFAULT_WATCHLIST = (
    "RELIANCE",
    "HDFCBANK",
    "TCS",
    "INFY",
    "TMPV",
    "SBIN",
    "ICICIBANK",
    "LT",
    "SUNPHARMA",
    "BAJFINANCE",
)


def capped_universe(
    holdings: list[dict[str, Any]] | None = None,
    *,
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST,
    max_extra: int = 10,
) -> list[str]:
    """All holding symbols + up to ``max_extra`` watchlist names not already held."""
    result: list[str] = []
    seen: set[str] = set()
    for h in holdings or []:
        sym = str(h.get("symbol") or "").upper().replace(".NS", "").replace(".BO", "")
        if sym and sym not in seen:
            seen.add(sym)
            result.append(sym)
    added = 0
    for sym in watchlist:
        clean = sym.upper()
        if clean in seen:
            continue
        result.append(clean)
        seen.add(clean)
        added += 1
        if added >= max_extra:
            break
    return result


def build_swarm_payload(symbol: str) -> dict[str, Any]:
    """Variables for india_fund_committee swarm create."""
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    return {
        "preset_name": "india_fund_committee",
        "variables": {
            "symbol": f"{bare}.NS",
            "target": f"{bare}.NS",
            "market": "IN",
        },
    }


def plan_from_cio_text(raw: str, *, symbol: str) -> CioTradePlan:
    del symbol  # reserved for future symbol-specific post-process
    if not is_engine_tradable("debate"):
        raise RuntimeError("debate engine is DEAD or sleeve depleted")
    plan = parse_cio_json(raw)
    state = load_engine("debate")
    return plan.model_copy(
        update={
            "engine_id": "debate",
            "survival_status": state.get("status") or "ALIVE",
        }
    )


def heuristic_debate_plan(
    symbol: str,
    *,
    analysis: dict[str, Any] | None = None,
    kronos: dict[str, Any] | None = None,
) -> CioTradePlan:
    """Fail-soft local plan when swarm/LLM unavailable (never invents fills)."""
    del symbol
    state = load_engine("debate")
    price = float((analysis or {}).get("current_price") or 100)
    metrics = (analysis or {}).get("metrics") or {}
    rsi = float(metrics.get("rsi_14") or 50)
    st = str(metrics.get("supertrend") or "BEARISH")
    kbias = str(
        ((kronos or {}).get("kronos_projection") or {}).get("trajectory_bias") or "NEUTRAL"
    )
    ta = (
        "bull"
        if st == "BULLISH" and rsi < 70
        else ("bear" if st == "BEARISH" and rsi > 30 else "neutral")
    )
    kr = "bull" if kbias == "BULLISH" else ("bear" if kbias == "BEARISH" else "neutral")
    aligned = ta == kr or ta == "neutral" or kr == "neutral"
    action = "WATCH"
    if aligned and ta == "bull" and kr == "bull":
        action = "BUY"
    elif aligned and ta == "bear" and kr == "bear":
        action = "SELL"
    return CioTradePlan(
        action=action,  # type: ignore[arg-type]
        confidence_score=50 if action == "WATCH" else 62,
        entry_range=f"₹{price * 0.99:,.0f} - ₹{price * 1.01:,.0f}",
        target_price=round(price * (1.06 if action == "BUY" else 0.94), 2),
        stop_loss=round(price * (0.97 if action == "BUY" else 1.03), 2),
        risk_reward_ratio="1:2",
        position_size_percent=0.0 if action == "WATCH" else 1.5,
        holding_horizon="2-6 weeks",
        executive_summary=(
            "Debate heuristic (LLM unavailable): "
            f"TA={ta} Kronos={kr}; fail-soft unless aligned."
        ),
        conviction_drivers=["cached_analysis", "kronos_bias"],
        disconfirming_evidence=["heuristic_not_full_committee"],
        invalidation="Invalidate if structure break beyond stop",
        agreement=Agreement(
            ta=ta, kronos=kr, fundamental="ok", aligned=aligned  # type: ignore[arg-type]
        ),
        do_not_trade_if=["llm_unavailable_for_live", "r_less_than_2"],
        engine_id="debate",
        arena_score=0,
        survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
    )
