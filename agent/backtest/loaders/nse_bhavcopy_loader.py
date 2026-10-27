"""NSE bhavcopy loader: survivorship-free, corporate-action-adjusted daily bars from a local store.

Reads the store built by ``scripts/nse_pull.py`` (see :mod:`backtest.loaders.nse_store`): NSE's
official bhavcopy archive from 22 Jun 2011 (delisted stocks included) plus the NSE corporate-action
feed, with every factor confirmed against the stock's own ex-date opening gap.

Environment knobs:
    NSE_DATA_DIR      store location (default ~/.vibe-trading/nse)
    NSE_PRICE_BASIS   ``price`` (default: splits, bonuses, rights, demergers)
                      ``total_return`` (price factors plus cash dividends; use for signals)
                      ``raw`` (unadjusted; use when a rule reads an absolute rupee price)
    NSE_ASOF          YYYY-MM-DD: point-in-time replay, bars and factors known at that day's end

Opt-in: unavailable until the store exists. Answers only when it holds EVERY requested NSE symbol,
so the chain never mixes half-covered sources; BSE (.BO) symbols are left to other loaders.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from backtest.loaders import nse_store
from backtest.loaders.base import validate_date_range
from backtest.loaders.nse_corporate_actions import apply_factors, find_jumps
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_STALE_DAYS = 7


def _nse_symbol(code: str) -> str | None:
    """Bare NSE symbol, or None for anything that is not an NSE (.NS) code."""
    code = code.strip().upper()
    if code.endswith(".BO") or code.endswith((".US", ".HK", ".SZ", ".SH", ".SS")):
        return None
    return code[:-3] if code.endswith(".NS") else code


@register
class DataLoader:
    """Local NSE bhavcopy store for the ``india_equity`` market."""

    name = "nse_bhavcopy"
    markets = {"india_equity"}
    requires_auth = False

    def __init__(self, basis: str | None = None, asof: str | None = None) -> None:
        self.basis = basis or os.environ.get("NSE_PRICE_BASIS", "price")
        self.asof = asof or os.environ.get("NSE_ASOF") or None

    def is_available(self) -> bool:
        return (nse_store.store_dir() / "bars.parquet").exists()

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        del fields
        validate_date_range(start_date, end_date)
        if str(interval or "1D").strip() != "1D" or not codes:
            return {}
        root = nse_store.store_dir()
        symbols = {code: _nse_symbol(code) for code in codes}
        if not (root / "bars.parquet").exists() or None in symbols.values():
            return {}

        names = sorted({n for s in symbols.values() for n in nse_store.symbol_aliases(s, root)})
        everything = nse_store.read_bars(root, names)  # full history: later factors scale earlier bars
        if everything.empty:
            return {}

        lo = pd.Timestamp(start_date)
        hi = pd.Timestamp(end_date) + pd.Timedelta(days=1)
        asof_cut = pd.Timestamp(self.asof) + pd.Timedelta(days=1) if self.asof else None
        out: Dict[str, pd.DataFrame] = {}
        for code, symbol in symbols.items():
            hist = nse_store.symbol_history(root, symbol, everything)
            if hist is None:
                logger.info("nse_bhavcopy: %s not in store; deferring to next source", code)
                return {}  # all-or-nothing, see module docstring
            raw = hist[nse_store.BAR_COLUMNS].astype(float).rename_axis("trade_date")
            if asof_cut is not None:
                raw = raw[raw.index < asof_cut]
            factors, dividends = nse_store.load_factors(root, symbol, self.asof)
            adjusted = apply_factors(raw, factors, dividends, self.basis)
            if self.basis != "raw":
                window_jumps = [(t, r) for t, r in find_jumps(adjusted) if lo <= t < hi]
                for ts, ret in window_jumps:
                    logger.warning("nse_bhavcopy: %s %s close moved %+.0f%% after adjustment "
                                   "(unresolved action? see review.json)", code, ts.date(), ret * 100)
            window = adjusted[(adjusted.index >= lo) & (adjusted.index < hi)]
            if window.empty:
                return {}
            last = window.index[-1].date()
            wants_now = date.fromisoformat(end_date) >= date.today() - timedelta(days=2)
            if not self.asof and (min(date.fromisoformat(end_date), date.today()) - last).days > _STALE_DAYS:
                logger.warning("nse_bhavcopy: %s ends %s, store is stale (scripts/nse_pull.py "
                               "--update)%s", code, last, "; deferring to the next source" if wants_now else "")
                if wants_now:
                    return {}  # a request for current prices must not be served week-old bars
            out[code] = window
        return out
