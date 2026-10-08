"""Tax-aware sell ladder helpers for AlphaDesk portfolio mode."""

from __future__ import annotations

from typing import Any


STCG_RATE = 0.20
LTCG_RATE = 0.125


def estimate_tax(gain: float, *, long_term_frac: float = 0.0) -> float:
    """Estimate capital gains tax on ``gain`` (INR). Loss → 0."""
    if gain <= 0:
        return 0.0
    lt = max(0.0, min(1.0, long_term_frac))
    st = 1.0 - lt
    return gain * (st * STCG_RATE + lt * LTCG_RATE)


def net_proceeds(
    qty: float,
    price: float,
    avg_cost: float,
    *,
    long_term_frac: float = 0.0,
) -> dict[str, float]:
    """Gross / tax / net for selling ``qty`` at ``price``."""
    gross = qty * price
    cost = qty * avg_cost
    gain = gross - cost
    tax = estimate_tax(gain, long_term_frac=long_term_frac)
    return {
        "gross": round(gross, 2),
        "cost": round(cost, 2),
        "gain": round(gain, 2),
        "tax": round(tax, 2),
        "net": round(gross - tax, 2),
    }


def prioritize_exits(
    holdings: list[dict[str, Any]],
    *,
    target_net: float,
) -> list[dict[str, Any]]:
    """Order holdings for liquidation: junk/stubs first, then non-core, then trims.

    Each holding dict should include:
    ``symbol``, ``qty``, ``price``, ``avg_cost``, ``current_value``,
    ``bucket`` in {junk, stub, noncore, trim, core}, optional ``lt_frac``.
    """
    order = {"junk": 0, "stub": 1, "noncore": 2, "trim": 3, "core": 4}
    ranked = sorted(
        holdings,
        key=lambda h: (
            order.get(str(h.get("bucket", "trim")).lower(), 3),
            float(h.get("current_value") or 0),
        ),
    )
    plan: list[dict[str, Any]] = []
    raised = 0.0
    for h in ranked:
        if raised >= target_net:
            break
        qty = float(h.get("qty") or 0)
        price = float(h.get("price") or 0)
        avg = float(h.get("avg_cost") or 0)
        if qty <= 0 or price <= 0:
            continue
        bucket = str(h.get("bucket", "trim")).lower()
        sell_qty = qty if bucket in {"junk", "stub", "noncore"} else max(1.0, qty * 0.25)
        sell_qty = min(sell_qty, qty)
        proceeds = net_proceeds(
            sell_qty, price, avg, long_term_frac=float(h.get("lt_frac") or 0)
        )
        if raised + proceeds["net"] > target_net and bucket == "trim":
            # Scale trim to remaining need (approx)
            need = max(0.0, target_net - raised)
            if proceeds["net"] > 0:
                sell_qty = min(qty, max(1.0, sell_qty * (need / proceeds["net"])))
                proceeds = net_proceeds(
                    sell_qty, price, avg, long_term_frac=float(h.get("lt_frac") or 0)
                )
        plan.append(
            {
                "symbol": h.get("symbol"),
                "bucket": bucket,
                "qty": round(sell_qty, 4),
                **proceeds,
            }
        )
        raised += proceeds["net"]
    return plan
