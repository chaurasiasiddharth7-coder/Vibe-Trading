"""Where 5-minute bars come from. Yahoo today; Zerodha Kite later without touching the rules.

A source turns a Yahoo-style ticker (``RELIANCE.NS``, ``^NSEI``) into a frame of OHLCV bars on the
5-minute grid, indexed by naive IST bar start. Everything downstream (day engine, scanner,
replay, calibration) reads bars only through ``bars.fetch*``, so swapping the source is one
environment variable: ``INTRADAY_DATA_SOURCE=yahoo`` (default) or ``kite``.

Read-only by design: a source supplies prices. Nothing in this package places orders; wiring a
broker for orders would be a separate, explicit step.
"""

from __future__ import annotations

import os
from typing import Protocol

import pandas as pd

EMPTY = ["open", "high", "low", "close", "volume"]


class BarSource(Protocol):
    name: str

    def bars(self, ticker: str, range_: str, interval: str = "5m") -> pd.DataFrame:
        """OHLCV frame for the last ``range_`` (``1d``, ``5d``, ``60d``) on the 5-minute grid."""
        ...


class YahooSource:
    """Free, delayed, unofficial. 60 days of 5-minute bars, one request per symbol."""

    name = "yahoo"

    def bars(self, ticker: str, range_: str, interval: str = "5m") -> pd.DataFrame:
        from src.alphadesk.intraday import bars as ibars

        payload = ibars._get(ticker, range_, interval)
        return ibars.parse_chart(payload) if payload else pd.DataFrame(columns=EMPTY)


class KiteSource:
    """Zerodha Kite Connect market data. NOT IMPLEMENTED YET: this documents the wiring.

    To finish it you need a Kite Connect app (api_key, api_secret), a daily access token from the
    login flow, and the NSE instrument list. The calls are:

    * instruments:  ``kite.instruments("NSE")`` -> map ``tradingsymbol`` to ``instrument_token``
      (index tokens come from the same list: "NIFTY 50", "NIFTY BANK").
    * bars:        ``kite.historical_data(token, from_dt, to_dt, "5minute")`` -> candles
      ``{date, open, high, low, close, volume}`` with tz-aware IST dates; drop the tz and keep the
      bar START time (Kite already stamps candles at the start). ``range_`` becomes
      ``from_dt = now - N days``; Kite allows 100 days of 5-minute history per request.
    * live:        ``KiteTicker`` websocket for streaming ticks if you later want sub-minute
      updates; the 5-minute rules only need completed candles, so polling ``historical_data``
      every 5 minutes is enough.

    Rate limit: historical_data is limited to about 3 requests a second; the day engine already
    fetches one symbol at a time. Tokens expire daily (~06:00 IST), so refresh the access token
    before the 09:15 job.
    """

    name = "kite"

    def bars(self, ticker: str, range_: str, interval: str = "5m") -> pd.DataFrame:
        raise NotImplementedError(
            "The Zerodha Kite source is not wired yet. See providers.KiteSource for the exact "
            "calls, or set INTRADAY_DATA_SOURCE=yahoo."
        )


_SOURCES = {"yahoo": YahooSource, "kite": KiteSource}


def get_source() -> BarSource:
    name = os.environ.get("INTRADAY_DATA_SOURCE", "yahoo").strip().lower()
    if name not in _SOURCES:
        raise ValueError(f"unknown INTRADAY_DATA_SOURCE {name!r}; choose from {sorted(_SOURCES)}")
    return _SOURCES[name]()
