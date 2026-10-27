"""Classify a trading day from the Nifty 50 (or Nifty Bank) 5-minute prices.

The label is a plain description of how the day moved, decided by fixed rules so it is never a
black box. First match wins:

1. Trend up / Trend down: open-to-close move of at least 0.5% and the close in the top (or bottom)
   quarter of the day's range.
2. Reversal day: the first hour moved at least 0.25% one way and the day then finished at least
   0.15% the other way.
3. Gap day: the open was at least 0.6% away from the previous close.
4. Range day: everything else.
"""

from __future__ import annotations

from src.alphadesk.intraday.rules import BAR, OPEN_MIN, Day

TREND_MOVE = 0.5
TREND_CLOSE_POS = 0.75
REVERSAL_FIRST_HOUR = 0.25
REVERSAL_FINAL = 0.15
GAP_PCT = 0.6
FIRST_HOUR_BARS = 12

RULES = (
    "Trend up/down: open-to-close ≥ 0.5% and the close in the top/bottom quarter of the range. "
    "Reversal: first hour ≥ 0.25% one way, day ends ≥ 0.15% the other way. "
    "Gap: the open ≥ 0.6% from the previous close. Range: anything else."
)


def classify(day: Day, prev_close: float | None) -> dict:
    """Metrics and label for one session. Percentages are in percent, not fractions."""
    if len(day) < FIRST_HOUR_BARS + 2:
        return {"type": "unknown", "label": "Not enough prices", "metrics": {}}
    open_, close = float(day.o[0]), float(day.c[-1])
    high, low = float(day.h.max()), float(day.l.min())
    ret = (close / open_ - 1) * 100
    rng = (high / low - 1) * 100
    close_pos = (close - low) / (high - low) if high > low else 0.5
    first_hour = (float(day.c[FIRST_HOUR_BARS - 1]) / open_ - 1) * 100
    gap = (open_ / prev_close - 1) * 100 if prev_close else 0.0
    metrics = {"gap_pct": round(gap, 2), "open_to_close_pct": round(ret, 2),
               "range_pct": round(rng, 2), "close_in_range": round(close_pos, 2),
               "first_hour_pct": round(first_hour, 2), "open": open_, "close": close,
               "bars": len(day), "last_bar": int(day.t[-1]) + BAR}
    if ret >= TREND_MOVE and close_pos >= TREND_CLOSE_POS:
        kind, label = "trend_up", "Trend up"
    elif ret <= -TREND_MOVE and close_pos <= 1 - TREND_CLOSE_POS:
        kind, label = "trend_down", "Trend down"
    elif (first_hour >= REVERSAL_FIRST_HOUR and ret <= -REVERSAL_FINAL) or \
            (first_hour <= -REVERSAL_FIRST_HOUR and ret >= REVERSAL_FINAL):
        kind, label = "reversal", "Reversal day"
    elif abs(gap) >= GAP_PCT:
        kind, label = "gap", "Gap up day" if gap > 0 else "Gap down day"
    else:
        kind, label = "range", "Range day"
    return {"type": kind, "label": label, "metrics": metrics}
