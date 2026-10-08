"""Personalized Portfolio Manager — hygiene-first proposals (deterministic)."""

from __future__ import annotations

from typing import Any

from src.alphadesk.cio_schema import Agreement, CioTradePlan, PortfolioFit
from src.alphadesk.portfolio import net_proceeds, prioritize_exits
from src.alphadesk.survival import load_engine

JUNK_KEYWORDS = (
    "DHFL",
    "RCOM",
    "BAJAJHIND",
    "EVEREADY",
    "FIRSTCRY",
    "SAMMAAN",
    "ABAN",
    "AJRINFRA",
    "JISLJALEQS",
    "EASEMYTRIP",
    "JHS",
    "AUSTRAL",
    "BIRLA POWER",
    "RETAIL",
    "KHADIM",
    "RBA",
)

STUB_VALUE_INR = 10_000.0
CORE_HINTS = (
    "LT",
    "LARSEN",
    "SUNPHARMA",
    "TCS",
    "HINDUNILVR",
    "HUL",
    "BAJFINANCE",
    "ICICIGI",
    "SBILIFE",
    "RELIANCE",
    "HDFCBANK",
    "ASIANPAINT",
)


def classify_bucket(row: dict[str, Any]) -> str:
    sym = str(row.get("symbol") or "").upper()
    value = float(row.get("current_value") or row.get("value") or 0)
    qty = float(row.get("qty") or row.get("quantity") or 0)
    if any(k in sym for k in JUNK_KEYWORDS):
        return "junk"
    if value > 0 and value < STUB_VALUE_INR:
        return "stub"
    if qty > 0 and qty <= 10 and value < 25_000:
        return "stub"
    if any(k in sym for k in CORE_HINTS):
        return "core"
    return str(row.get("bucket") or "noncore").lower()


def normalize_holding(row: dict[str, Any]) -> dict[str, Any]:
    qty = float(row.get("qty") or row.get("quantity") or 0)
    price = float(row.get("price") or row.get("ltp") or row.get("last_price") or 0)
    avg = float(row.get("avg_cost") or row.get("avg") or row.get("average_price") or price)
    value = float(row.get("current_value") or row.get("value") or (qty * price))
    out = {
        **row,
        "symbol": str(row.get("symbol") or "").upper(),
        "qty": qty,
        "price": price,
        "avg_cost": avg,
        "current_value": value,
        "bucket": classify_bucket({**row, "current_value": value, "qty": qty}),
        "lt_frac": float(row.get("lt_frac") or 0),
    }
    return out


def build_action_plan(
    holdings: list[dict[str, Any]],
    *,
    target_net: float = 0.0,
    max_names: int = 18,
) -> dict[str, Any]:
    """Hygiene-first plan: exits/trims, then underweight ADDs as WATCH list."""
    normed = [normalize_holding(h) for h in holdings if h]
    total = sum(float(h["current_value"]) for h in normed) or 1.0
    for h in normed:
        h["weight_pct"] = round(100.0 * float(h["current_value"]) / total, 2)

    ladder: list[dict[str, Any]] = []
    if target_net > 0:
        ladder = prioritize_exits(normed, target_net=target_net)

    exits = [h for h in normed if h["bucket"] in {"junk", "stub"}]
    noncore = [h for h in normed if h["bucket"] == "noncore"]
    core = [h for h in normed if h["bucket"] == "core"]
    name_count = len(normed)
    overflow = max(0, name_count - max_names)

    proposals: list[dict[str, Any]] = []
    for h in exits:
        pr = net_proceeds(h["qty"], h["price"], h["avg_cost"], long_term_frac=h["lt_frac"])
        proposals.append(
            {
                "symbol": h["symbol"],
                "action": "SELL",
                "reason": f"hygiene exit ({h['bucket']})",
                "qty": h["qty"],
                **pr,
            }
        )
    for row in ladder:
        if any(p["symbol"] == row.get("symbol") and p["action"] == "SELL" for p in proposals):
            continue
        proposals.append(
            {
                "symbol": row.get("symbol"),
                "action": "TRIM" if row.get("bucket") == "trim" else "SELL",
                "reason": "liquidity ladder",
                "qty": row.get("qty"),
                "gross": row.get("gross"),
                "tax": row.get("tax"),
                "net": row.get("net"),
            }
        )

    add_watch = [
        {"symbol": "HDFCBANK", "action": "WATCH", "reason": "underweight quality — confirm before ADD"},
        {"symbol": "RELIANCE", "action": "WATCH", "reason": "underweight quality — confirm before ADD"},
        {"symbol": "ASIANPAINT", "action": "WATCH", "reason": "underweight quality — confirm before ADD"},
    ]

    return {
        "engine_id": "pm",
        "total_value": round(total, 2),
        "name_count": name_count,
        "overflow_names": overflow,
        "core_count": len(core),
        "noncore_count": len(noncore),
        "hygiene_exits": len(exits),
        "ladder": ladder,
        "proposals": proposals,
        "add_candidates": add_watch,
        "max_names": max_names,
        "raised_net": round(sum(float(p.get("net") or 0) for p in proposals), 2),
    }


def proposal_for_symbol(
    symbol: str,
    holdings: list[dict[str, Any]],
    *,
    target_net: float = 0.0,
) -> CioTradePlan:
    """Emit a CioTradePlan for one symbol from PM hygiene logic."""
    plan = build_action_plan(holdings, target_net=target_net)
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    match = next(
        (p for p in plan["proposals"] if str(p.get("symbol") or "").upper().replace(".NS", "") == sym),
        None,
    )
    state = load_engine("pm")
    price = 0.0
    for h in holdings:
        if str(h.get("symbol") or "").upper().replace(".NS", "").replace(".BO", "") == sym:
            price = float(h.get("price") or h.get("ltp") or 0)
            break

    if match:
        action = "TRIM" if match.get("action") == "TRIM" else "SELL"
        net = float(match.get("net") or 0)
        summary = f"PM hygiene: {match.get('reason')} · est net ₹{net:,.0f}"
        conf = 80 if action == "SELL" else 70
    else:
        action = "HOLD"
        summary = "PM: no hygiene/liquidity action — hold / monitor weight"
        conf = 55

    stop = round(price * 0.92, 2) if price else 0.0
    target = round(price * 1.08, 2) if price else 0.0
    return CioTradePlan(
        action=action,  # type: ignore[arg-type]
        confidence_score=conf,
        entry_range=f"₹{price:,.2f}" if price else "",
        target_price=target or 1.0,
        stop_loss=stop or 1.0,
        risk_reward_ratio="1:2",
        position_size_percent=0.0 if action in {"HOLD", "WATCH"} else 2.0,
        holding_horizon="portfolio",
        executive_summary=summary,
        conviction_drivers=["portfolio_hygiene", "tax_aware_ladder"],
        disconfirming_evidence=[],
        invalidation="Skip if name is locked core without override",
        agreement=Agreement(ta="neutral", kronos="neutral", fundamental="ok", aligned=True),
        portfolio_fit=PortfolioFit(
            cash_impact_inr=float(match.get("gross") or 0) if match else 0.0,
            est_tax_inr=float(match.get("tax") or 0) if match else 0.0,
            net_proceeds_inr=float(match.get("net") or 0) if match else 0.0,
        ),
        do_not_trade_if=["live_without_approve", "core_override_missing"],
        engine_id="pm",
        arena_score=0,
        survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
    )
