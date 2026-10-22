"""Scan universes: NSE index constituents, fetched from NSE and cached.

NSE publishes each index's member list as a CSV (company, industry, symbol).
Lists are refreshed weekly; if NSE is unreachable the last saved copy is used,
and ``core`` (the original hand-picked list) always works offline.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from pathlib import Path
from typing import Any

from src.alphadesk.universes import (
    BLUE_CHIP_STOCKS,
    GOLD_ETFS,
    INDEX_ETFS,
    SECTOR_ETFS,
    asset_class_for,
)

logger = logging.getLogger(__name__)

NSE_CSV = "https://archives.nseindia.com/content/indices/{file}.csv"
MAX_AGE_S = 7 * 24 * 3600

UNIVERSES: dict[str, dict[str, str]] = {
    "core": {"label": "Original list (30 big companies)", "file": ""},
    "nifty50": {"label": "Nifty 50: India's 50 biggest companies", "file": "ind_nifty50list"},
    "nifty100": {"label": "Nifty 100: top 100 companies", "file": "ind_nifty100list"},
    "nifty200": {"label": "Nifty 200: top 200 companies", "file": "ind_nifty200list"},
    "nifty500": {"label": "Nifty 500: top 500 companies", "file": "ind_nifty500list"},
    "midcap150": {"label": "Midcap 150: mid-size companies", "file": "ind_niftymidcap150list"},
    "smallcap250": {"label": "Smallcap 250: smaller companies", "file": "ind_niftysmallcap250list"},
}

_ETF_INFO = {
    **{s: "Index fund (ETF)" for s in INDEX_ETFS},
    **{s: "Sector fund (ETF)" for s in SECTOR_ETFS},
    **{s: "Gold fund (ETF)" for s in GOLD_ETFS},
}


def _dir() -> Path:
    from src.alphadesk.store import alphadesk_root

    d = alphadesk_root() / "universes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("Symbol") or "").strip().upper()
        # NSE pads some lists with placeholder rows such as DUMMYHEG.
        if sym and not sym.startswith("DUMMY") and (row.get("Series") or "EQ").strip() == "EQ":
            out.append(
                {
                    "symbol": sym,
                    "name": (row.get("Company Name") or sym).strip(),
                    "industry": (row.get("Industry") or "Other").strip(),
                }
            )
    return out


def _fetch_csv(file: str) -> str:
    import requests

    resp = requests.get(
        NSE_CSV.format(file=file),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


def members(key: str) -> list[dict[str, str]]:
    """Stock members of a universe: ``[{symbol, name, industry}]``."""
    if key not in UNIVERSES:
        raise ValueError(f"unknown universe {key!r}")
    if key == "core":
        named = {m["symbol"]: m for m in _cached_members("nifty100")}
        return [
            named.get(s, {"symbol": s, "name": s, "industry": "Other"}) for s in BLUE_CHIP_STOCKS
        ]
    return _cached_members(key)


def _cached_members(key: str) -> list[dict[str, str]]:
    path = _dir() / f"{key}.csv"
    fresh = path.is_file() and time.time() - path.stat().st_mtime < MAX_AGE_S
    if not fresh:
        try:
            text = _fetch_csv(UNIVERSES[key]["file"])
            if _parse(text):
                path.write_text(text, encoding="utf-8")
        except Exception as exc:
            logger.warning("NSE list %s unavailable (%s); using saved copy", key, exc)
    if path.is_file():
        return _parse(path.read_text(encoding="utf-8"))
    return []


def scan_list(key: str, *, include_funds: bool) -> list[dict[str, str]]:
    """Members plus (optionally) the tradeable funds; liquid/cash funds excluded."""
    out = list(members(key))
    if include_funds:
        out += [{"symbol": s, "name": s, "industry": label} for s, label in _ETF_INFO.items()]
    seen: set[str] = set()
    clean = []
    for m in out:
        if m["symbol"] in seen or asset_class_for(m["symbol"]) == "liquid":
            continue
        seen.add(m["symbol"])
        clean.append(m)
    return clean


def catalogue() -> list[dict[str, Any]]:
    return [{"key": k, "label": v["label"]} for k, v in UNIVERSES.items()]
