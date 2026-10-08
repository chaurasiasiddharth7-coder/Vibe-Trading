"""Mentor universes — India blue-chip stocks + liquid ETF / fund proxies.

True open-ended mutual-fund NAVs are sparse on Yahoo; liquid NSE ETFs
(Nifty/Bank/IT/Gold/Liquid BeES) are the reliable stand-ins for
index / blue-chip / defensive sleeves in AlphaDesk.
"""

from __future__ import annotations

from typing import Literal

AssetClass = Literal["stock", "index_etf", "sector_etf", "gold", "liquid"]

# Expanded beyond the 5 Desk chips — liquid large-cap / quality names.
BLUE_CHIP_STOCKS: tuple[str, ...] = (
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "INFY",
    "ICICIBANK",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "LT",
    "HINDUNILVR",
    "BAJFINANCE",
    "AXISBANK",
    "KOTAKBANK",
    "ASIANPAINT",
    "MARUTI",
    "SUNPHARMA",
    "TITAN",
    "WIPRO",
    "ULTRACEMCO",
    "NESTLEIND",
    "POWERGRID",
    "NTPC",
    "ONGC",
    "TMPV",
    "M&M",
    "HCLTECH",
    "TECHM",
    "ADANIENT",
    "ADANIPORTS",
    "JSWSTEEL",
)

# Broad market / blue-chip index ETFs (SIP-friendly core)
INDEX_ETFS: tuple[str, ...] = (
    "NIFTYBEES",
    "JUNIORBEES",
    "HDFCSENSEX",
    "MON100",
)

# Sector / theme ETFs
SECTOR_ETFS: tuple[str, ...] = (
    "BANKBEES",
    "ITBEES",
)

# Defensive / ballast
GOLD_ETFS: tuple[str, ...] = ("GOLDBEES",)
LIQUID_ETFS: tuple[str, ...] = ("LIQUIDBEES",)

# Educational open-ended MF categories (no live NAV) — mentor allocation copy
MF_SIP_PLAYBOOK: tuple[dict[str, str], ...] = (
    {
        "category": "Large-cap / Nifty 50 index fund (direct)",
        "why": "Cheapest way to own India blue chips; pair with NIFTYBEES for liquidity.",
        "horizon": "3–5+ years",
        "sleeve": "core_equity",
    },
    {
        "category": "Flexi-cap active fund (direct, low expense)",
        "why": "Manager can tilt mid-cap; keep ≤20% of equity if you already own stock picks.",
        "horizon": "3–5+ years",
        "sleeve": "satellite_equity",
    },
    {
        "category": "Short-duration / liquid debt fund",
        "why": "Parking sleeve next to LIQUIDBEES for goals <12 months.",
        "horizon": "1–12 months",
        "sleeve": "cash_like",
    },
    {
        "category": "Gold / silver FoF or ETF",
        "why": "Crisis hedge; GOLDBEES is the exchange-traded expression we can score live.",
        "horizon": "1–3+ years",
        "sleeve": "hedge",
    },
)


def asset_class_for(symbol: str) -> AssetClass:
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    if bare in INDEX_ETFS:
        return "index_etf"
    if bare in SECTOR_ETFS:
        return "sector_etf"
    if bare in GOLD_ETFS:
        return "gold"
    if bare in LIQUID_ETFS:
        return "liquid"
    return "stock"


def mentor_scan_universe(*, include_etfs: bool = True) -> list[str]:
    """Default full mentor universe (stocks + ETFs)."""
    out: list[str] = list(BLUE_CHIP_STOCKS)
    if include_etfs:
        out.extend(INDEX_ETFS)
        out.extend(SECTOR_ETFS)
        out.extend(GOLD_ETFS)
        out.extend(LIQUID_ETFS)
    # de-dupe preserve order
    seen: set[str] = set()
    clean: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            clean.append(s)
    return clean


def default_allocation_template(capital_inr: float) -> dict[str, object]:
    """Suggested sleeve mix for the liquidity target (educational)."""
    cap = max(float(capital_inr or 0), 0.0)
    sleeves = [
        ("core_index_etf", 0.35, "NIFTYBEES / Sensex ETF SIP"),
        ("blue_chip_stocks", 0.30, "Aligned BUY stocks only"),
        ("satellite_sector", 0.10, "BANKBEES / ITBEES if scored BUY"),
        ("gold_hedge", 0.10, "GOLDBEES"),
        ("cash_liquid", 0.15, "LIQUIDBEES / liquid fund"),
    ]
    rows = []
    for key, pct, note in sleeves:
        rows.append(
            {
                "sleeve": key,
                "pct": int(pct * 100),
                "inr": round(cap * pct, 0) if cap else None,
                "note": note,
            }
        )
    return {
        "capital_inr": cap,
        "sleeves": rows,
        "mf_playbook": list(MF_SIP_PLAYBOOK),
        "disclaimer": (
            "Not SEBI-registered advice. ETFs are scored live from market data; "
            "open-ended MF categories are allocation guidance — pick direct plans "
            "on your AMC / Gro app after expense-ratio and risk review."
        ),
    }
