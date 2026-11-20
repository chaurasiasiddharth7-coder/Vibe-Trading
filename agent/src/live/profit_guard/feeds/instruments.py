"""Symbol <-> instrument-token lookup from Kite's daily instrument dump, cached once per day."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any


def cache_dir() -> Path:
    from src.config.paths import get_runtime_root

    return get_runtime_root() / "data" / "kite_instruments"


def _cache_file(exchange: str, day: date) -> Path:
    return cache_dir() / f"{exchange.upper()}_{day.isoformat()}.csv"


def load_instruments(kite: Any, exchange: str, day: date) -> list[dict[str, Any]]:
    """Today's instruments for ``exchange``; one API call per exchange per day at most."""
    path = _cache_file(exchange, day)
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    rows = kite.instruments(exchange.upper())
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        keys = list(rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in keys})
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in rows]


def token_map(rows: list[dict[str, Any]], symbols: list[str]) -> dict[str, int]:
    """{tradingsymbol: instrument_token} for the symbols asked; unknown symbols are left out."""
    want = {s.upper() for s in symbols}
    out: dict[str, int] = {}
    for r in rows:
        sym = str(r.get("tradingsymbol", "")).upper()
        if sym in want:
            out[sym] = int(r["instrument_token"])
    return out
