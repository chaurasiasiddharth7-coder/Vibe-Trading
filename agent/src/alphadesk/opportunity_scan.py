"""Mentor-style opportunity scan — horizon + asset-class buckets."""

from __future__ import annotations

import logging
from typing import Any

from src.alphadesk.quick_signal import build_quick_signal
from src.alphadesk.universes import (
    asset_class_for,
    default_allocation_template,
    mentor_scan_universe,
)

logger = logging.getLogger(__name__)

HORIZON_ORDER = ("1 week", "1 month", "3 months", "6 months+", "monitor", "avoid")
ASSET_ORDER = ("stock", "index_etf", "sector_etf", "gold", "liquid")


def _horizon_from_plan(
    action: str,
    price: float,
    target: float,
    kronos_chg: float,
) -> str:
    if action in {"SELL", "TRIM"}:
        return "avoid" if action == "SELL" else "monitor"
    if action in {"WATCH", "HOLD"}:
        return "monitor"
    dist = abs(target - price) / max(price, 1e-9) * 100.0
    if dist <= 3.5 or (kronos_chg >= 4.0 and dist <= 7.0):
        return "1 week"
    if dist <= 8.0 or kronos_chg >= 2.0:
        return "1 month"
    if dist <= 15.0:
        return "3 months"
    return "6 months+"


def _score_plan(action: str, confidence: int, aligned: bool, kronos_chg: float) -> float:
    base = float(confidence)
    if action == "BUY":
        base += 15.0
    elif action == "SELL":
        base -= 10.0
    elif action == "WATCH":
        base -= 5.0
    if aligned:
        base += 8.0
    base += min(abs(kronos_chg), 10.0) * 0.5
    return round(base, 2)


def scan_opportunities(
    *,
    symbols: list[str] | None = None,
    capital_inr: float = 0.0,
    max_per_bucket: int = 5,
    include_etfs: bool = True,
    full_universe: bool = True,
) -> dict[str, Any]:
    """Scan stocks + ETFs; bucket by horizon and asset class.

    Deterministic mentor brief — not a guarantee of profit. Prefer BUY when
    TA+Kronos align; otherwise WATCH/avoid.
    """
    from src.alphadesk.analysis import analyze_symbol
    from src.alphadesk.cache import get_cached
    from src.alphadesk.kronos_service import forecast_symbol

    if symbols:
        universe = [s.upper().replace(".NS", "").replace(".BO", "") for s in symbols]
    elif full_universe:
        universe = mentor_scan_universe(include_etfs=include_etfs)
    else:
        from src.alphadesk.engines.debate import DEFAULT_WATCHLIST

        universe = list(DEFAULT_WATCHLIST)

    seen: set[str] = set()
    clean: list[str] = []
    for s in universe:
        if s and s not in seen:
            seen.add(s)
            clean.append(s)

    rows: list[dict[str, Any]] = []
    for sym in clean:
        try:
            analysis = get_cached("analysis", sym, interval="1d", period="6mo")
            if analysis is None:
                analysis = analyze_symbol(sym, period="6mo", interval="1d")
            kronos = get_cached(
                "kronos",
                sym,
                lookback=400,
                pred_len=15,
                interval="1d",
                period="2y",
            )
            # Skip Kronos for cash-like ETFs (flat NAV noise); optional elsewhere
            aclass = asset_class_for(sym)
            if kronos is None and aclass != "liquid":
                try:
                    kronos = forecast_symbol(
                        sym, lookback=400, pred_len=15, interval="1d", period="2y"
                    )
                except Exception as exc:
                    logger.info("kronos skip %s: %s", sym, exc)
                    kronos = None

            plan, _ = build_quick_signal(
                sym, analysis=analysis, kronos=kronos, persist=False
            )
            price = float((analysis or {}).get("current_price") or 0)
            proj = (kronos or {}).get("kronos_projection") or {}
            try:
                k_chg = float(proj.get("expected_change_percent") or 0)
            except (TypeError, ValueError):
                k_chg = 0.0
            # Liquid BeES: treat as hold/cash sleeve, not directional BUY chase
            if aclass == "liquid":
                action = "HOLD"
                horizon = "cash sleeve"
                conf = 70
                score = 40.0
                summary = "Cash parking ETF — use for dry powder, not growth."
                mentor = "Keep 10–20% here while waiting for aligned equity BUYs."
                target = price
                stop = price
                entry = f"₹{price:,.2f}"
                size_pct = 0.0
            else:
                action = plan.action
                horizon = _horizon_from_plan(
                    plan.action, price, float(plan.target_price), k_chg
                )
                conf = plan.confidence_score
                score = _score_plan(plan.action, conf, bool(plan.agreement.aligned), k_chg)
                # Soft boost for core index ETFs when not SELL
                if aclass == "index_etf" and action in {"BUY", "WATCH", "HOLD"}:
                    score += 5.0
                    mentor = (
                        "Blue-chip index ETF — prefer SIP / DCA for core wealth; "
                        f"tactical tilt {action} @ {horizon}."
                    )
                elif aclass == "gold":
                    score += 2.0
                    mentor = "Gold hedge sleeve — size small; diversifies equity drawdowns."
                else:
                    mentor = _mentor_note(action, bool(plan.agreement.aligned), horizon, k_chg)
                summary = plan.executive_summary
                target = plan.target_price
                stop = plan.stop_loss
                entry = plan.entry_range
                size_pct = float(plan.position_size_percent or 0)

            suggested_inr = (
                round(capital_inr * size_pct / 100.0, 0)
                if capital_inr > 0 and size_pct > 0
                else None
            )
            rows.append(
                {
                    "symbol": sym,
                    "asset_class": aclass,
                    "action": action,
                    "confidence": conf,
                    "score": round(score, 2),
                    "horizon": horizon,
                    "entry_range": entry,
                    "target_price": target,
                    "stop_loss": stop,
                    "risk_reward_ratio": getattr(plan, "risk_reward_ratio", "1:2"),
                    "position_size_percent": size_pct,
                    "suggested_inr": suggested_inr,
                    "price": price,
                    "kronos_bias": proj.get("trajectory_bias"),
                    "kronos_change_pct": k_chg,
                    "summary": summary,
                    "mentor_note": mentor,
                }
            )
        except Exception as exc:
            logger.warning("opportunity scan failed for %s: %s", sym, exc)
            rows.append(
                {
                    "symbol": sym,
                    "asset_class": asset_class_for(sym),
                    "action": "WATCH",
                    "confidence": 0,
                    "score": 0,
                    "horizon": "monitor",
                    "summary": f"Scan skipped: {exc}",
                    "mentor_note": "Data unavailable — skip until analysis loads.",
                }
            )

    buckets: dict[str, list[dict[str, Any]]] = {h: [] for h in HORIZON_ORDER}
    by_asset: dict[str, list[dict[str, Any]]] = {a: [] for a in ASSET_ORDER}

    for row in sorted(rows, key=lambda r: float(r.get("score") or 0), reverse=True):
        aclass = str(row.get("asset_class") or "stock")
        if aclass in by_asset and len(by_asset[aclass]) < max_per_bucket + 3:
            by_asset[aclass].append(row)

        h = str(row.get("horizon") or "monitor")
        if h == "cash sleeve":
            continue
        if h not in buckets:
            h = "monitor"
        if len(buckets[h]) >= max_per_bucket:
            continue
        if h in {"1 week", "1 month", "3 months", "6 months+"} and row.get("action") != "BUY":
            if row.get("action") == "WATCH" and len(buckets["monitor"]) < max_per_bucket:
                buckets["monitor"].append(row)
            continue
        buckets[h].append(row)

    top_buys = [r for r in rows if r.get("action") == "BUY"]
    top_buys.sort(key=lambda r: float(r.get("score") or 0), reverse=True)

    stock_buys = [r for r in top_buys if r.get("asset_class") == "stock"]

    # Always surface core blue-chip / index / gold / liquid sleeves for the mentor
    # UI — even when TA has no tactical BUY (SIP / HOLD is the wealth path).
    fund_classes = {"index_etf", "sector_etf", "gold", "liquid"}
    etf_tactical = [r for r in top_buys if r.get("asset_class") in fund_classes]
    etf_core = [
        r
        for r in sorted(rows, key=lambda x: float(x.get("score") or 0), reverse=True)
        if r.get("asset_class") in fund_classes and r.get("action") != "SELL"
    ]
    seen_funds: set[str] = set()
    etf_buys: list[dict[str, Any]] = []
    for r in etf_tactical + etf_core:
        sym = str(r.get("symbol") or "")
        if not sym or sym in seen_funds:
            continue
        seen_funds.add(sym)
        etf_buys.append(r)
        if len(etf_buys) >= 8:
            break

    allocation = default_allocation_template(capital_inr)

    return {
        "engine_id": "mentor_scan",
        "universe": clean,
        "universe_n": len(clean),
        "capital_inr": capital_inr,
        "buckets": buckets,
        "by_asset_class": by_asset,
        "top_buys": top_buys[:12],
        "top_stock_buys": stock_buys[:8],
        "top_fund_etf_buys": etf_buys[:8],
        "candidates": rows,
        "allocation": allocation,
        "mentor_brief": _brief(stock_buys, etf_buys, capital_inr),
    }


def _mentor_note(action: str, aligned: bool, horizon: str, k_chg: float) -> str:
    if action == "BUY" and aligned:
        return f"Aligned setup · aim for {horizon} path (Kronos ~{k_chg:+.1f}%). Size small; respect stop."
    if action == "BUY" and not aligned:
        return "BUY only if you accept TA/Kronos tension — prefer WATCH until alignment."
    if action == "SELL":
        return "Structure is weak — do not add; trim if overweight."
    return "No edge yet — wait for alignment or a cleaner entry."


def _brief(
    stock_buys: list[dict[str, Any]],
    etf_buys: list[dict[str, Any]],
    capital_inr: float,
) -> str:
    cap = f" Against ₹{capital_inr:,.0f}, " if capital_inr > 0 else " "
    stocks = ", ".join(f"{r['symbol']} ({r['horizon']})" for r in stock_buys[:4]) or "none aligned"
    funds = ", ".join(f"{r['symbol']}" for r in etf_buys[:4]) or "hold index SIP"
    return (
        f"Priority:{cap}stocks → {stocks}; blue-chip/index ETFs → {funds}. "
        "Split across horizons and sleeves (see allocation). "
        "Open-ended MFs: use the SIP playbook (direct plans). Paper-size first; "
        "live only behind expectancy unlock."
    )
