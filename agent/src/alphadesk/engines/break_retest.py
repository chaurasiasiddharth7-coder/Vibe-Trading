"""Break-and-retest detectors: PDH/PDL, order-block, NSE opening range."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

Variant = Literal["pdh_pdl", "order_block", "orb"]
Side = Literal["BUY", "SELL", "WATCH"]


@dataclass(frozen=True)
class LevelSetup:
    variant: Variant
    side: Side
    entry: float
    stop: float
    target: float
    level: float
    level_name: str
    risk_reward: float
    notes: str


def _ohlc(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    lower = {str(c).lower(): c for c in df.columns}

    def col(*names: str) -> pd.Series:
        for n in names:
            if n.lower() in lower:
                return df[lower[n.lower()]].astype(float)
        raise KeyError(names)

    return col("open"), col("high"), col("low"), col("close")


def detect_pdh_pdl(df: pd.DataFrame) -> LevelSetup | None:
    """Previous day high/low break-and-retest on daily bars.

    Long: prior day high broken, then retest holds as support.
    Short: prior day low broken, then retest holds as resistance.
    """
    if len(df) < 5:
        return None
    _o, high, low, close = _ohlc(df)
    # Prior completed day = iloc[-2]; today = iloc[-1]
    pdh = float(high.iloc[-2])
    pdl = float(low.iloc[-2])
    prev_close = float(close.iloc[-2])
    cur_open = float(_o.iloc[-1])
    cur_high = float(high.iloc[-1])
    cur_low = float(low.iloc[-1])
    cur_close = float(close.iloc[-1])

    # Break above PDH earlier in window then retest today
    broke_high = bool((high.iloc[:-1] > pdh).any()) or cur_high > pdh
    broke_low = bool((low.iloc[:-1] < pdl).any()) or cur_low < pdl

    # Retest long: price traded back to PDH zone and closed back above
    if broke_high and cur_low <= pdh * 1.002 and cur_close >= pdh and cur_close > prev_close:
        stop = min(cur_low, pdh) * 0.995
        risk = cur_close - stop
        if risk <= 0:
            return None
        target = cur_close + 2.0 * risk
        rr = (target - cur_close) / risk
        if rr < 2.0:
            target = cur_close + 2.0 * risk
            rr = 2.0
        return LevelSetup(
            variant="pdh_pdl",
            side="BUY",
            entry=cur_close,
            stop=round(stop, 2),
            target=round(target, 2),
            level=pdh,
            level_name="PDH",
            risk_reward=round(rr, 2),
            notes="Break above prior day high, retest held as support",
        )

    if broke_low and cur_high >= pdl * 0.998 and cur_close <= pdl and cur_close < prev_close:
        stop = max(cur_high, pdl) * 1.005
        risk = stop - cur_close
        if risk <= 0:
            return None
        target = cur_close - 2.0 * risk
        rr = (cur_close - target) / risk
        if rr < 2.0:
            target = cur_close - 2.0 * risk
            rr = 2.0
        return LevelSetup(
            variant="pdh_pdl",
            side="SELL",
            entry=cur_close,
            stop=round(stop, 2),
            target=round(target, 2),
            level=pdl,
            level_name="PDL",
            risk_reward=round(rr, 2),
            notes="Break below prior day low, retest held as resistance",
        )

    # No active retest — still surface levels as WATCH
    mid = (pdh + pdl) / 2
    return LevelSetup(
        variant="pdh_pdl",
        side="WATCH",
        entry=cur_close,
        stop=round(pdl, 2),
        target=round(pdh, 2),
        level=mid,
        level_name="PDH/PDL",
        risk_reward=0.0,
        notes=f"No retest trigger · PDH={pdh:.2f} PDL={pdl:.2f}",
    )


def detect_order_block(df: pd.DataFrame) -> LevelSetup | None:
    """Last opposing impulse candle used as support/resistance (body zone)."""
    if len(df) < 8:
        return None
    o, high, low, close = _ohlc(df)
    # Find last down-close candle before an up move (bullish OB) or reverse
    setup: LevelSetup | None = None
    for i in range(len(df) - 3, 2, -1):
        body_top = max(float(o.iloc[i]), float(close.iloc[i]))
        body_bot = min(float(o.iloc[i]), float(close.iloc[i]))
        bullish_candle = float(close.iloc[i]) > float(o.iloc[i])
        # Bullish order block = last down candle before impulse up
        if not bullish_candle and float(close.iloc[i + 1]) > float(close.iloc[i]):
            # Price retesting zone now?
            cur = float(close.iloc[-1])
            cur_low = float(low.iloc[-1])
            if cur_low <= body_top and cur >= body_bot:
                stop = body_bot * 0.995
                risk = cur - stop
                if risk <= 0:
                    continue
                target = cur + 2.0 * risk
                setup = LevelSetup(
                    variant="order_block",
                    side="BUY",
                    entry=cur,
                    stop=round(stop, 2),
                    target=round(target, 2),
                    level=body_top,
                    level_name="Bullish OB",
                    risk_reward=2.0,
                    notes="Retest of last down-closed impulse candle",
                )
                break
        # Bearish OB = last up candle before impulse down
        if bullish_candle and float(close.iloc[i + 1]) < float(close.iloc[i]):
            cur = float(close.iloc[-1])
            cur_high = float(high.iloc[-1])
            if cur_high >= body_bot and cur <= body_top:
                stop = body_top * 1.005
                risk = stop - cur
                if risk <= 0:
                    continue
                target = cur - 2.0 * risk
                setup = LevelSetup(
                    variant="order_block",
                    side="SELL",
                    entry=cur,
                    stop=round(stop, 2),
                    target=round(target, 2),
                    level=body_bot,
                    level_name="Bearish OB",
                    risk_reward=2.0,
                    notes="Retest of last up-closed impulse candle",
                )
                break
    return setup


def detect_orb(df: pd.DataFrame, *, session_open_hour: int = 9) -> LevelSetup | None:
    """Opening-range break/retest using first bar of the latest session.

    Works on intraday frames. On daily data, treats the latest bar's open range
    as a weak proxy and returns WATCH-only (true ORB needs 1m/5m/15m).
    """
    if len(df) < 3:
        return None
    o, high, low, close = _ohlc(df)
    idx = df.index
    # Prefer bars matching session open hour if datetime index
    orb_high = float(high.iloc[0])
    orb_low = float(low.iloc[0])
    intraday = False
    if hasattr(idx, "hour"):
        for i, ts in enumerate(idx):
            try:
                if int(getattr(ts, "hour", -1)) == session_open_hour:
                    orb_high = float(high.iloc[i])
                    orb_low = float(low.iloc[i])
                    intraday = True
                    # use first matching open bar of last day
                    last_day = ts.date() if hasattr(ts, "date") else None
                    for j in range(i, len(df)):
                        tj = idx[j]
                        if last_day and hasattr(tj, "date") and tj.date() != last_day:
                            break
                        if int(getattr(tj, "hour", -1)) == session_open_hour:
                            orb_high = max(orb_high, float(high.iloc[j]))
                            orb_low = min(orb_low, float(low.iloc[j]))
                    break
            except Exception:
                continue

    cur = float(close.iloc[-1])
    cur_high = float(high.iloc[-1])
    cur_low = float(low.iloc[-1])

    if not intraday:
        return LevelSetup(
            variant="orb",
            side="WATCH",
            entry=cur,
            stop=round(orb_low, 2),
            target=round(orb_high, 2),
            level=(orb_high + orb_low) / 2,
            level_name="ORB(proxy)",
            risk_reward=0.0,
            notes="Intraday bars required for live ORB; daily proxy levels only",
        )

    if cur_low <= orb_high and cur > orb_high and cur_high > orb_high:
        stop = orb_high * 0.998
        risk = cur - stop
        if risk <= 0:
            return None
        target = cur + 2.0 * risk
        return LevelSetup(
            variant="orb",
            side="BUY",
            entry=cur,
            stop=round(stop, 2),
            target=round(target, 2),
            level=orb_high,
            level_name="ORB high",
            risk_reward=2.0,
            notes="NSE session ORB break + retest (long)",
        )
    if cur_high >= orb_low and cur < orb_low and cur_low < orb_low:
        stop = orb_low * 1.002
        risk = stop - cur
        if risk <= 0:
            return None
        target = cur - 2.0 * risk
        return LevelSetup(
            variant="orb",
            side="SELL",
            entry=cur,
            stop=round(stop, 2),
            target=round(target, 2),
            level=orb_low,
            level_name="ORB low",
            risk_reward=2.0,
            notes="NSE session ORB break + retest (short)",
        )
    return LevelSetup(
        variant="orb",
        side="WATCH",
        entry=cur,
        stop=round(orb_low, 2),
        target=round(orb_high, 2),
        level=(orb_high + orb_low) / 2,
        level_name="ORB",
        risk_reward=0.0,
        notes=f"No ORB retest · high={orb_high:.2f} low={orb_low:.2f}",
    )


def levels_payload(setups: list[LevelSetup]) -> list[dict[str, Any]]:
    out = []
    for s in setups:
        out.append(
            {
                "variant": s.variant,
                "side": s.side,
                "entry": s.entry,
                "stop": s.stop,
                "target": s.target,
                "level": s.level,
                "level_name": s.level_name,
                "risk_reward": s.risk_reward,
                "notes": s.notes,
            }
        )
    return out


def scan_all_variants(df: pd.DataFrame) -> list[LevelSetup]:
    found: list[LevelSetup] = []
    for fn in (detect_pdh_pdl, detect_order_block, detect_orb):
        s = fn(df)
        if s is not None:
            found.append(s)
    return found
