"""Build the AlphaDesk structured analysis payload for one India symbol."""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.loaders.base import NoAvailableSourceError
from backtest.loaders.registry import fetch_chain
from src.alphadesk.indicators import (
    atr,
    bollinger,
    detect_candle_patterns,
    ema,
    macd,
    rsi,
    supertrend_direction,
)
from src.alphadesk.symbols import (
    india_fetch_candidates,
    interval_to_loader,
    normalize_india_symbol,
    period_to_dates,
)


def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    """Resolve an OHLCV column case-insensitively."""
    lower_map = {str(c).lower(): c for c in df.columns}
    for name in names:
        key = name.lower()
        if key in lower_map:
            return df[lower_map[key]].astype(float)
    raise KeyError(f"missing column among {names}; have {list(df.columns)}")


def fetch_india_ohlcv(
    symbol: str, *, period: str = "6mo", interval: str = "1d"
) -> tuple[str, pd.DataFrame]:
    """Fetch OHLCV for an India equity via the india_equity loader chain.

    Tries Yahoo renames (e.g. TATAMOTORS→TMPV) and walks the full fallback
    chain when the primary loader returns empty / 404.
    """
    start, end = period_to_dates(period)
    loader_interval = interval_to_loader(interval)
    last_ticker = normalize_india_symbol(symbol)
    for ticker in india_fetch_candidates(symbol):
        last_ticker = ticker
        frames, _src = fetch_chain(
            "india_equity",
            [ticker],
            start,
            end,
            interval=loader_interval,
        )
        frame = frames.get(ticker) if isinstance(frames, dict) else None
        if frame is not None and isinstance(frame, pd.DataFrame) and not frame.empty:
            return ticker, frame.copy()
    raise LookupError(f"no OHLCV data for {last_ticker}")


def analyze_symbol(
    symbol: str, *, period: str = "6mo", interval: str = "1d"
) -> dict[str, Any]:
    """Return Gemini/AlphaDesk-compatible analysis JSON for ``symbol``."""
    ticker, df = fetch_india_ohlcv(symbol, period=period, interval=interval)
    if len(df) < 50:
        raise LookupError(
            f"insufficient history for {ticker}: need >=50 bars, got {len(df)}"
        )

    open_ = _col(df, "open", "Open")
    high = _col(df, "high", "High")
    low = _col(df, "low", "Low")
    close = _col(df, "close", "Close")
    volume = _col(df, "volume", "Volume")

    df = df.copy()
    df["EMA_20"] = ema(close, 20)
    df["EMA_50"] = ema(close, 50)
    df["EMA_200"] = ema(close, 200)
    df["RSI_14"] = rsi(close, 14)
    macd_line, macd_sig, macd_hist = macd(close)
    df["MACD"] = macd_line
    df["MACD_SIGNAL"] = macd_sig
    df["MACD_HIST"] = macd_hist
    _, bb_upper, bb_lower = bollinger(close)
    df["BB_UPPER"] = bb_upper
    df["BB_LOWER"] = bb_lower
    df["ATR_14"] = atr(high, low, close, 14)
    df["SUPERTREND_DIR"] = supertrend_direction(high, low, close)

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    patterns = detect_candle_patterns(open_, high, low, close)

    loader_iv = interval_to_loader(interval)
    daily_like = loader_iv in {"1D", "1W", "1M"}
    candles: list[dict[str, Any]] = []
    for index in df.index:
        ts = pd.Timestamp(index)
        time_str = (
            ts.strftime("%Y-%m-%d")
            if daily_like
            else ts.strftime("%Y-%m-%dT%H:%M:%S")
        )
        candles.append(
            {
                "time": time_str,
                "open": round(float(open_.loc[index]), 2),
                "high": round(float(high.loc[index]), 2),
                "low": round(float(low.loc[index]), 2),
                "close": round(float(close.loc[index]), 2),
                "volume": int(float(volume.loc[index])),
            }
        )

    # Use series values from latest row for metrics
    ema_200 = latest["EMA_200"]
    current = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    change_pct = (current - prev_close) / prev_close * 100 if prev_close else 0.0

    macd_status = (
        "Bullish Crossover"
        if float(latest["MACD"]) > float(latest["MACD_SIGNAL"])
        else "Bearish Crossover"
    )
    st_dir = latest["SUPERTREND_DIR"]
    supertrend = (
        "BULLISH"
        if not pd.isna(st_dir) and float(st_dir) > 0
        else "BEARISH"
    )

    return {
        "symbol": ticker.replace(".NS", "").replace(".BO", ""),
        "ticker": ticker,
        "current_price": round(current, 2),
        "change_percent": round(float(change_pct), 2),
        "metrics": {
            "rsi_14": round(float(latest["RSI_14"]), 2)
            if not pd.isna(latest["RSI_14"])
            else None,
            "ema_20": round(float(latest["EMA_20"]), 2),
            "ema_50": round(float(latest["EMA_50"]), 2),
            "ema_200": round(float(ema_200), 2) if not pd.isna(ema_200) else None,
            "macd_status": macd_status,
            "supertrend": supertrend,
            "atr": round(float(latest["ATR_14"]), 2)
            if not pd.isna(latest["ATR_14"])
            else None,
            "bb_upper": round(float(latest["BB_UPPER"]), 2)
            if not pd.isna(latest["BB_UPPER"])
            else None,
            "bb_lower": round(float(latest["BB_LOWER"]), 2)
            if not pd.isna(latest["BB_LOWER"])
            else None,
            "active_candle_patterns": patterns,
        },
        "chart_data": candles,
    }


def analysis_error_payload(detail: str, *, code: int = 400) -> dict[str, Any]:
    """Fail-closed envelope for analysis failures."""
    return {"ok": False, "error": detail, "code": code}
