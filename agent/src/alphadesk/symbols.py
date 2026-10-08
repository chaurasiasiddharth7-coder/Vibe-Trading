"""NSE/BSE symbol helpers and period → date window mapping."""

from __future__ import annotations

from datetime import date, timedelta

# Yahoo Finance renamed / demerged NSE tickers (bare NSE code -> Yahoo bare).
# Tata Motors Passenger Vehicles trades as TMPV after the demerger.
YAHOO_NSE_ALIASES: dict[str, str] = {
    "TATAMOTORS": "TMPV",
    "TATAMOTOR": "TMPV",
}


def normalize_india_symbol(symbol: str) -> str:
    """Normalize a ticker to Yahoo/project form (``.NS`` / ``.BO``).

    Bare names (e.g. ``RELIANCE``) become ``RELIANCE.NS``. Existing ``.NS`` /
    ``.BO`` suffixes are preserved (case-folded). Known Yahoo renames
    (e.g. ``TATAMOTORS`` → ``TMPV``) are applied.
    """
    raw = (symbol or "").strip().upper()
    if not raw:
        raise ValueError("symbol is required")
    suffix = ""
    if raw.endswith((".NS", ".BO")):
        suffix = raw[-3:]
        bare = raw[:-3]
    else:
        bare = raw.replace(" ", "").replace("/", "")
        suffix = ".NS"
    # Allow NSE-style tickers with & / - (M&M, BAJAJ-AUTO)
    if not bare.replace("&", "").replace("-", "").isalnum():
        raise ValueError(f"invalid symbol: {symbol!r}")
    bare = YAHOO_NSE_ALIASES.get(bare, bare)
    return f"{bare}{suffix}"


def india_fetch_candidates(symbol: str) -> list[str]:
    """Ordered Yahoo tickers to try for one user-facing India symbol."""
    primary = normalize_india_symbol(symbol)
    out = [primary]
    bare = primary[:-3] if primary.endswith((".NS", ".BO")) else primary
    if primary.endswith(".NS"):
        bo = f"{bare}.BO"
        if bo not in out:
            out.append(bo)
    return out


def period_to_dates(period: str) -> tuple[str, str]:
    """Map a Yahoo-style period string to inclusive ``YYYY-MM-DD`` dates.

    Supported: ``1mo``, ``3mo``, ``6mo``, ``1y``, ``2y``, ``5y``.
    """
    end = date.today()
    key = (period or "6mo").strip().lower()
    days = {
        "1mo": 31,
        "3mo": 93,
        "6mo": 186,
        "1y": 366,
        "2y": 731,
        "5y": 1826,
    }.get(key)
    if days is None:
        raise ValueError(f"unsupported period: {period!r}")
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def interval_to_loader(interval: str) -> str:
    """Map API interval (``1d``, ``1h``, ``5m``) to loader interval."""
    key = (interval or "1d").strip().lower()
    mapping = {
        "1d": "1D",
        "1h": "1H",
        "5m": "5m",
        "15m": "15m",
        "1w": "1W",
    }
    if key not in mapping:
        raise ValueError(f"unsupported interval: {interval!r}")
    return mapping[key]
