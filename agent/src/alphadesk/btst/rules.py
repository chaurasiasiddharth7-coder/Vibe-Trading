"""The BTST rules: which gainers qualify, the seven warnings, the pick, and how a trade exits.

Everything here is pure (no files, no network) so the live scan, the replay and the tests share it.

Where the numbers come from (research, Nov 2026, NSE bhavcopies 2021-01 .. 2026-11): the plain
"buy the #1 gainer, sell at +1%" rule lost 0.9% a trade after costs (hit rate 73%, misses averaged
-5.4%). Thresholds below were set on 2021-22 only and checked on 2023-26: the top gainer with no
warning made +0.2% to +0.3% a trade after 0.25% costs (about 80% hit), and lost in 2025. Each
warning roughly doubled the chance of a 5%+ loss on the candidates it flagged.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

RULES_VERSION = "btst-v1"
TICK = 0.05  # NSE equity tick size


@dataclass(frozen=True)
class BtstRules:
    min_traded_cr: float = 10.0  # today's traded value (Rs crore) at the scan
    min_avg_traded_cr: float = 2.0  # 20-day average traded value: who is scanned at all
    min_price: float = 50.0
    min_gain: float = 0.02  # at least +2% on the day
    candidates: int = 20  # top N qualifying gainers are shown and settled
    target_pct: float = 0.01  # GTT take-profit
    stop_pct: float = 0.02  # GTT safety stop (OCO with the target)
    use_stop: bool = True
    cost_pct: float = 0.0025  # delivery round trip: STT both sides, stamp, exchange, GST, DP charge
    scan_time: str = "15:20"
    # warning thresholds (set on 2021-22 data)
    blowoff_x: float = 10.0
    too_hot: float = 0.12
    extended_5d: float = 0.09
    extended_20d: float = 0.19
    weak_breadth: float = 0.36
    downtrend_20d: float = -0.05
    far_below_high: float = -0.27
    big_gap: float = 0.02
    volatile: float = 0.04
    # stuck at the upper price band: no sellers, so it cannot really be bought
    locked_gain: float = 0.0475
    locked_near_high: float = 0.999

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_RULES = BtstRules()

# key -> (short label, sentence, out-of-sample 5%+ loss rate when flagged / when not, 2023-26)
WARNINGS: dict[str, tuple[str, str, float, float]] = {
    "volume_blowoff": ("Volume blow-off", "Traded {turn_x:.0f}x its usual value today. Spikes this big often fade next morning.", 8.1, 4.1),
    "too_hot": ("Up too much today", "Up {gain_pct:.1f}% today. Moves over 12% give back more often.", 11.0, 4.1),
    "overextended": ("Already ran up", "Up {ret5_pct:.0f}% in 5 days / {ret20_pct:.0f}% in 20 days before today.", 7.1, 4.7),
    "weak_market": ("Weak market", "Only {breadth_pct:.0f}% of liquid stocks are up today.", 7.6, 4.8),
    "downtrend": ("In a downtrend", "Down {ret20_neg:.0f}% over 20 days or {below_high:.0f}% below its 1-year high.", 5.7, 5.1),
    "big_gap": ("Big gap-up open", "Opened {gap_pct:.1f}% above yesterday's close.", 8.3, 4.7),
    "very_volatile": ("Very jumpy stock", "Moves {vol_pct:.1f}% a day on a normal day.", 8.3, 4.6),
}


def round_tick(price: float, up: bool) -> float:
    steps = price / TICK
    steps = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(steps * TICK, 2)


def plan_prices(entry: float, rules: BtstRules = DEFAULT_RULES) -> dict:
    """GTT levels for a buy at ``entry``: target rounded up to the tick, stop rounded down."""
    target = round_tick(entry * (1 + rules.target_pct), up=True)
    stop = round_tick(entry * (1 - rules.stop_pct), up=False) if rules.use_stop else None
    return {"entry": round(entry, 2), "target": target, "stop": stop}


# --- history: rolling stats per symbol per session ------------------------------------------------


def rolling_stats(bars: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, date) stats from that session's own bar and the ones before it.

    ``bars``: symbol, date, open, high, low, close, prev_close, volume (the NSE store layout).
    Daily returns use the exchange's own previous close, so a split or bonus (where NSE adjusts
    the previous close on the ex-date) does not look like a crash. Highs are put on the same
    adjusted basis before the 1-year high is taken.
    """
    b = bars.sort_values(["symbol", "date"]).reset_index(drop=True)
    b["date"] = pd.to_datetime(b["date"])
    pc = b["prev_close"].where(b["prev_close"] > 0)
    b["ret"] = b["close"] / pc - 1
    b["traded"] = b["close"] * b["volume"]
    g = b.groupby("symbol", sort=False)
    b["n"] = g.cumcount() + 1
    logret = np.log1p(b["ret"].clip(lower=-0.95).fillna(0.0))
    b["adj_close"] = np.exp(logret.groupby(b["symbol"]).cumsum())
    b["adj_high"] = b["high"] * b["adj_close"] / b["close"]
    def roll(col: str, n: int, mp: int, how: str) -> pd.Series:
        r = getattr(b.groupby("symbol", sort=False)[col].rolling(n, min_periods=mp), how)()
        return r.reset_index(level=0, drop=True)

    b["turn20"] = roll("traded", 20, 10, "mean")
    b["vol20"] = roll("ret", 20, 10, "std")
    b["hi250"] = roll("adj_high", 250, 60, "max")
    b["ret5"] = b["adj_close"] / g["adj_close"].shift(5) - 1
    b["ret20"] = b["adj_close"] / g["adj_close"].shift(20) - 1
    return b


def prior_stats(stats: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """The last row before ``day`` for each symbol, as the features the scan on ``day`` may know."""
    before = stats[stats["date"] < day]
    last = before.groupby("symbol", sort=False).tail(1).set_index("symbol")
    out = pd.DataFrame(index=last.index)
    out["turn20"] = last["turn20"]
    out["vol20"] = last["vol20"]
    out["ret5"] = last["ret5"]
    out["ret20"] = last["ret20"]
    out["prev_ret"] = last["ret"]
    out["hi250_rel"] = last["hi250"] / last["adj_close"]  # the 1-year high as a multiple of yesterday's close
    out["sessions"] = last["n"]
    out["last_date"] = last["date"]
    return out


# --- the scan ----------------------------------------------------------------------------------


def breadth(snap: pd.DataFrame, rules: BtstRules = DEFAULT_RULES) -> tuple[float | None, int]:
    """Share of liquid stocks (traded value >= the scan minimum) trading above yesterday's close."""
    liq = snap[snap["traded"] >= rules.min_traded_cr * 1e7]
    if liq.empty:
        return None, 0
    return float((liq["last"] > liq["prev_close"]).mean()), int(len(liq))


def features(snap: pd.DataFrame, prior: pd.DataFrame, mkt_breadth: float | None) -> pd.DataFrame:
    """Join today's snapshot (symbol, open, high, low, last, prev_close, traded) with prior stats."""
    f = snap.set_index("symbol").join(prior, how="left")
    f["gain"] = f["last"] / f["prev_close"] - 1
    f["gap"] = f["open"] / f["prev_close"] - 1
    f["turn_x"] = f["traded"] / f["turn20"]
    f["off_high"] = f["last"] / f["high"] - 1
    # 1-year high relative to today's price: yesterday's high-multiple scaled by today's move
    f["dist_hi250"] = 1 / (f["hi250_rel"] / (1 + f["gain"])) - 1
    f["breadth"] = mkt_breadth
    return f.reset_index()


def is_locked(row, rules: BtstRules = DEFAULT_RULES) -> bool:
    return bool(row["gain"] >= rules.locked_gain and row["last"] >= row["high"] * rules.locked_near_high)


def warnings_for(row, rules: BtstRules = DEFAULT_RULES) -> list[dict]:
    """The warnings that apply to one candidate, each with a plain sentence."""
    def v(k):
        x = row.get(k) if hasattr(row, "get") else row[k]
        return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)

    hits: list[tuple[str, dict]] = []
    if (tx := v("turn_x")) is not None and tx > rules.blowoff_x:
        hits.append(("volume_blowoff", {"turn_x": tx}))
    if (gn := v("gain")) is not None and gn > rules.too_hot:
        hits.append(("too_hot", {"gain_pct": gn * 100}))
    r5, r20 = v("ret5"), v("ret20")
    if (r5 is not None and r5 > rules.extended_5d) or (r20 is not None and r20 > rules.extended_20d):
        hits.append(("overextended", {"ret5_pct": (r5 or 0) * 100, "ret20_pct": (r20 or 0) * 100}))
    if (br := v("breadth")) is not None and br < rules.weak_breadth:
        hits.append(("weak_market", {"breadth_pct": br * 100}))
    dh = v("dist_hi250")
    if (r20 is not None and r20 < rules.downtrend_20d) or (dh is not None and dh < rules.far_below_high):
        hits.append(("downtrend", {"ret20_neg": -(r20 or 0) * 100, "below_high": -(dh or 0) * 100}))
    if (gp := v("gap")) is not None and gp > rules.big_gap:
        hits.append(("big_gap", {"gap_pct": gp * 100}))
    if (vl := v("vol20")) is not None and vl > rules.volatile:
        hits.append(("very_volatile", {"vol_pct": vl * 100}))
    out = []
    for key, vals in hits:
        label, sentence, flagged, clean = WARNINGS[key]
        out.append({"key": key, "label": label, "detail": sentence.format(**vals),
                    "big_loss_flagged_pct": flagged, "big_loss_clean_pct": clean})
    return out


def candidates(feat: pd.DataFrame, rules: BtstRules = DEFAULT_RULES) -> tuple[pd.DataFrame, dict]:
    """Top ``rules.candidates`` qualifying gainers, best first, and why others were left out."""
    f = feat.copy()
    left_out = {"illiquid_today": 0, "thin_history": 0, "cheap": 0, "upper_band": 0, "small_move": 0, "not_eq": 0}
    ok = pd.Series(True, index=f.index)
    if "series" in f:
        m = f["series"].fillna("EQ").str.upper().ne("EQ")
        left_out["not_eq"] = int((m & ok).sum())
        ok &= ~m
    m = f["gain"].isna() | (f["gain"] < rules.min_gain)
    left_out["small_move"] = int((m & ok).sum())
    ok &= ~m
    m = f["traded"] < rules.min_traded_cr * 1e7
    left_out["illiquid_today"] = int((m & ok).sum())
    ok &= ~m
    m = f["last"] < rules.min_price
    left_out["cheap"] = int((m & ok).sum())
    ok &= ~m
    m = f["sessions"].isna() | (f["sessions"] < 10) | f["turn20"].isna() | (f["turn20"] < rules.min_avg_traded_cr * 1e7)
    left_out["thin_history"] = int((m & ok).sum())
    ok &= ~m
    m = (f["gain"] >= rules.locked_gain) & (f["last"] >= f["high"] * rules.locked_near_high)
    left_out["upper_band"] = int((m & ok).sum())
    ok &= ~m
    c = f[ok].sort_values("gain", ascending=False).head(rules.candidates).reset_index(drop=True)
    c["rank"] = np.arange(1, len(c) + 1)
    return c, left_out


def choose(cands: pd.DataFrame, rules: BtstRules = DEFAULT_RULES) -> tuple[list[dict], dict | None, str | None]:
    """(candidate rows with warnings, the pick or None, why nothing was picked)."""
    rows, pick = [], None
    for _, r in cands.iterrows():
        w = warnings_for(r, rules)
        row = {
            "symbol": r["symbol"], "rank": int(r["rank"]), "gain_pct": _pct(r["gain"]),
            "last": _num(r["last"]), "prev_close": _num(r["prev_close"]), "open": _num(r["open"]),
            "high": _num(r["high"]), "low": _num(r["low"]), "traded_cr": _num(r["traded"] / 1e7, 1),
            "turn_x": _num(r.get("turn_x"), 1), "ret5_pct": _pct(r.get("ret5")), "ret20_pct": _pct(r.get("ret20")),
            "from_high_pct": _pct(r.get("dist_hi250")), "vol20_pct": _pct(r.get("vol20")),
            "gap_pct": _pct(r.get("gap")), "off_high_pct": _pct(r.get("off_high")),
            "warnings": w, "chosen": False,
        }
        if pick is None and not w:
            row["chosen"] = True
            pick = {"symbol": r["symbol"], **plan_prices(float(r["last"]), rules), "gain_pct": row["gain_pct"]}
        rows.append(row)
    reason = None
    if pick is None:
        reason = ("No stock qualified today." if not rows else
                  "Every candidate has at least one warning. The rules skip days like this.")
    return rows, pick, reason


def _num(x, nd: int = 2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _pct(x, nd: int = 2):
    v = _num(x, 6)
    return None if v is None else round(v * 100, nd)


# --- exits -------------------------------------------------------------------------------------


def exit_daily(entry: float, o: float, h: float, lo: float, c: float, rules: BtstRules = DEFAULT_RULES) -> dict:
    """Settle on the next day's daily bar. When the stop and the target both sit inside the bar the
    order is unknown: the stop is assumed first (conservative) and ``ambiguous`` is set."""
    lv = plan_prices(entry, rules)
    T, S = lv["target"], lv["stop"]
    amb = False
    if o >= T:
        px, why = o, "gap_target"
    elif S is not None and o <= S:
        px, why = o, "gap_stop"
    elif S is not None and lo <= S:
        px, why, amb = S, "stop", h >= T
    elif h >= T:
        px, why = T, "target"
    else:
        px, why = c, "time_exit"
    out = _settled(entry, px, why, "daily", amb, rules)
    if amb:  # the other order: take-profit first
        out["alt_net_pct"] = round((T / entry - 1 - rules.cost_pct) * 100, 3)
    return out


def exit_path(entry: float, path: pd.DataFrame, rules: BtstRules = DEFAULT_RULES,
              exit_time: str = "15:20") -> dict:
    """Settle on the next day's intraday bars (open/high/low/close, time-indexed): exact order.
    No fill by ``exit_time`` means a sell at that bar's close."""
    lv = plan_prices(entry, rules)
    T, S = lv["target"], lv["stop"]
    hh, mm = (int(x) for x in exit_time.split(":"))
    last_px = None
    for ts, bar in path.sort_index().iterrows():
        o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        if S is not None and o <= S:
            return _settled(entry, o, "gap_stop" if last_px is None else "stop", "intraday", False, rules)
        if o >= T:
            return _settled(entry, o, "gap_target" if last_px is None else "target", "intraday", False, rules)
        if S is not None and lo <= S:
            return _settled(entry, S, "stop", "intraday", h >= T, rules)
        if h >= T:
            return _settled(entry, T, "target", "intraday", False, rules)
        last_px = c
        if (ts.hour, ts.minute) >= (hh, mm):
            break
    if last_px is None:
        raise ValueError("no intraday bars to settle on")
    return _settled(entry, last_px, "time_exit", "intraday", False, rules)


def _settled(entry: float, px: float, why: str, method: str, amb: bool, rules: BtstRules) -> dict:
    gross = px / entry - 1
    return {"exit": round(float(px), 2), "reason": why, "method": method, "ambiguous": bool(amb),
            "gross_pct": round(gross * 100, 3), "net_pct": round((gross - rules.cost_pct) * 100, 3),
            "hit": why in ("target", "gap_target")}


REASON_TEXT = {
    "target": "Take-profit filled", "gap_target": "Opened above take-profit",
    "stop": "Safety stop hit", "gap_stop": "Opened below the stop",
    "time_exit": "Sold near the close",
}
