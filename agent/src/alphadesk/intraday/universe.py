"""Which symbols a day's plan may trade.

``nifty50`` and ``niftybank`` are the index members. ``nse_liquid`` is every NSE equity liquid
enough for intraday (from the NSE store, ranked by traded value), capped because each symbol
costs a request; the plan always says how many it covered of how many it could have. A symbol's
``tags`` say which indices it belongs to so any day can be filtered to Nifty 50 or Nifty Bank.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.alphadesk.index_lists import members

KEYS = {
    "nifty50": "Nifty 50",
    "niftybank": "Nifty Bank",
    "nifty50_bank": "Nifty 50 + Nifty Bank",
    "nse_liquid": "All NSE (liquid names)",
}
MIN_TRADED_VALUE = 10e7  # Rs 10 crore a day
MIN_PRICE = 20.0


def _syms(key: str) -> list[str]:
    return [m["symbol"] for m in members(key)]


def tags_for(symbols: list[str]) -> dict[str, list[str]]:
    n50, bank = set(_syms("nifty50")), set(_syms("niftybank"))
    return {s: [t for t, grp in (("nifty50", n50), ("niftybank", bank)) if s in grp] for s in symbols}


def nse_liquid(limit: int, as_of: date | None = None) -> tuple[list[str], int]:
    """(top ``limit`` liquid NSE symbols by median traded value, how many qualified)."""
    import pandas as pd

    from backtest.loaders import nse_store

    root = nse_store.store_dir()
    if not (root / "bars.parquet").exists():
        raise RuntimeError("All-NSE needs the NSE store: run scripts/nse_pull.py first, or pick Nifty 50 / Nifty Bank")
    end = as_of or date.today()
    recent = pd.read_parquet(root / "bars.parquet",
                             filters=[("date", ">=", pd.Timestamp(end - timedelta(days=45))),
                                      ("date", "<", pd.Timestamp(end))],
                             columns=["symbol", "date", "close", "volume"])
    recent["tv"] = recent["close"] * recent["volume"]
    g = recent.groupby("symbol").agg(tv=("tv", "median"), price=("close", "last"))
    ok = g[(g.tv >= MIN_TRADED_VALUE) & (g.price >= MIN_PRICE)].sort_values("tv", ascending=False)
    return ok.index.tolist()[:limit], len(ok)


def resolve(key: str, limit: int = 60, as_of: date | None = None) -> dict:
    """``{key, label, symbols, tags, covered, eligible}``."""
    if key not in KEYS:
        raise ValueError(f"unknown universe {key!r}; choose from {sorted(KEYS)}")
    if key == "nifty50":
        syms = _syms("nifty50")
    elif key == "niftybank":
        syms = _syms("niftybank")
    elif key == "nifty50_bank":
        syms = list(dict.fromkeys(_syms("nifty50") + _syms("niftybank")))
    else:
        syms, eligible = nse_liquid(limit, as_of)
        return {"key": key, "label": KEYS[key], "symbols": syms, "tags": tags_for(syms),
                "covered": len(syms), "eligible": eligible}
    return {"key": key, "label": KEYS[key], "symbols": syms, "tags": tags_for(syms),
            "covered": len(syms), "eligible": len(syms)}
