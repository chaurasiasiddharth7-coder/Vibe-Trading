"""Pure-pandas technical indicators for AlphaDesk (no pandas-ta required)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average."""
    return series.astype(float).ewm(span=length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder-style RSI."""
    close = series.astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    close = series.astype(float)
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return line, sig, hist


def bollinger(
    series: pd.Series, length: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger mid / upper / lower."""
    close = series.astype(float)
    mid = close.rolling(length).mean()
    rolling_std = close.rolling(length).std(ddof=0)
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std
    return mid, upper, lower


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14
) -> pd.Series:
    """Average True Range."""
    h = high.astype(float)
    l = low.astype(float)
    c = close.astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def supertrend_direction(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.Series:
    """Return +1 (bullish) / -1 (bearish) Supertrend direction."""
    h = high.astype(float)
    l = low.astype(float)
    c = close.astype(float)
    atr_s = atr(h, l, c, period)
    hl2 = (h + l) / 2
    upper = hl2 + multiplier * atr_s
    lower = hl2 - multiplier * atr_s

    direction = pd.Series(index=c.index, dtype="float64")
    final_upper = upper.copy()
    final_lower = lower.copy()

    for i in range(len(c)):
        if i == 0 or pd.isna(atr_s.iloc[i]):
            direction.iloc[i] = np.nan
            continue
        if i > 0 and not pd.isna(final_lower.iloc[i - 1]):
            if lower.iloc[i] > final_lower.iloc[i - 1] or c.iloc[i - 1] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i - 1]
            if upper.iloc[i] < final_upper.iloc[i - 1] or c.iloc[i - 1] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

        prev_dir = direction.iloc[i - 1] if i > 0 else np.nan
        if pd.isna(prev_dir):
            direction.iloc[i] = 1.0 if c.iloc[i] >= final_lower.iloc[i] else -1.0
        elif prev_dir == 1.0:
            direction.iloc[i] = -1.0 if c.iloc[i] < final_lower.iloc[i] else 1.0
        else:
            direction.iloc[i] = 1.0 if c.iloc[i] > final_upper.iloc[i] else -1.0

    return direction


def detect_candle_patterns(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> list[str]:
    """Return human-readable pattern labels for the latest bar."""
    o = open_.astype(float)
    h = high.astype(float)
    l = low.astype(float)
    c = close.astype(float)
    if len(c) < 2:
        return []

    body = (c - o).abs()
    total_range = (h - l).replace(0, np.nan)
    upper_shadow = h - pd.concat([o, c], axis=1).max(axis=1)
    lower_shadow = pd.concat([o, c], axis=1).min(axis=1) - l

    i = -1
    labels: list[str] = []
    if float(body.iloc[i] / total_range.iloc[i]) < 0.10:
        labels.append("DOJI (Neutral)")
    if (
        float(lower_shadow.iloc[i]) > 2 * float(body.iloc[i])
        and float(upper_shadow.iloc[i]) < float(body.iloc[i])
    ):
        labels.append("HAMMER (Bullish)")
    if (
        float(upper_shadow.iloc[i]) > 2 * float(body.iloc[i])
        and float(lower_shadow.iloc[i]) < float(body.iloc[i])
    ):
        labels.append("SHOOTINGSTAR (Bearish)")

    prev_bear = c.iloc[-2] < o.iloc[-2]
    curr_bull = c.iloc[-1] > o.iloc[-1]
    if (
        prev_bear
        and curr_bull
        and o.iloc[-1] <= c.iloc[-2]
        and c.iloc[-1] >= o.iloc[-2]
        and body.iloc[-1] > body.iloc[-2]
    ):
        labels.append("ENGULFING (Bullish)")

    prev_bull = c.iloc[-2] > o.iloc[-2]
    curr_bear = c.iloc[-1] < o.iloc[-1]
    if (
        prev_bull
        and curr_bear
        and o.iloc[-1] >= c.iloc[-2]
        and c.iloc[-1] <= o.iloc[-2]
        and body.iloc[-1] > body.iloc[-2]
    ):
        labels.append("ENGULFING (Bearish)")

    # Morning star: three-bar simplified
    if len(c) >= 3:
        b0, b1, b2 = body.iloc[-3], body.iloc[-2], body.iloc[-1]
        if (
            c.iloc[-3] < o.iloc[-3]
            and b1 < b0 * 0.5
            and c.iloc[-1] > o.iloc[-1]
            and c.iloc[-1] > (o.iloc[-3] + c.iloc[-3]) / 2
        ):
            labels.append("MORNINGSTAR (Bullish)")

    return labels
