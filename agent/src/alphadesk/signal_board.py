"""Signal board — one ranked long / short / steer-clear view of the universe.

Each symbol gets four transparent votes (+1 bull, -1 bear, 0 none):

* trend      — SuperTrend direction
* structure  — price vs EMA50 vs EMA200 stacking
* momentum   — MACD vs its signal line
* forecast   — Kronos 15-bar expected change (±1.5% threshold)

RSI is a guard, not a vote: longs are blocked above 70, shorts below 30.
Stop / target are ATR-based (1.5 ATR / 3 ATR), so R:R is 2 by construction.

This engine has no expectancy until paper outcomes are recorded against
``ENGINE_ID``; the board reports that track record instead of a fake
confidence number.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from src.alphadesk.universes import asset_class_for, mentor_scan_universe

logger = logging.getLogger(__name__)

ENGINE_ID = "signal_board"
Side = Literal["LONG", "SHORT", "STEER_CLEAR", "NO_EDGE"]

KRONOS_THRESHOLD_PCT = 1.5
STOP_ATR = 1.5
TARGET_ATR = 3.0
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
MIN_VOTES = 3
# Fewer sessions than this in the 5-year past test means a recent listing.
SHORT_HISTORY_BARS = 500

SHORT_NOTE = (
    "NSE cash shorts must be squared off the same day. Holding a short "
    "overnight needs stock futures / options."
)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _votes(
    price: float, metrics: dict[str, Any], kronos_chg: float | None
) -> tuple[dict[str, int], list[str]]:
    votes: dict[str, int] = {}
    reasons: list[str] = []

    st = str(metrics.get("supertrend") or "").upper()
    votes["trend"] = 1 if st == "BULLISH" else (-1 if st == "BEARISH" else 0)
    if votes["trend"]:
        reasons.append(f"SuperTrend {st.lower()}")

    ema50 = _num(metrics.get("ema_50"))
    ema200 = _num(metrics.get("ema_200"))
    structure = 0
    if ema50:
        if price > ema50 and (ema200 is None or ema50 > ema200):
            structure = 1
            reasons.append("price > EMA50" + (" > EMA200" if ema200 else ""))
        elif price < ema50 and (ema200 is None or ema50 < ema200):
            structure = -1
            reasons.append("price < EMA50" + (" < EMA200" if ema200 else ""))
    votes["structure"] = structure

    macd = str(metrics.get("macd_status") or "").lower()
    votes["momentum"] = 1 if macd.startswith("bullish") else (-1 if macd.startswith("bearish") else 0)
    if votes["momentum"]:
        reasons.append("MACD " + ("above" if votes["momentum"] > 0 else "below") + " signal")

    if kronos_chg is None:
        votes["forecast"] = 0
    elif kronos_chg >= KRONOS_THRESHOLD_PCT:
        votes["forecast"] = 1
        reasons.append(f"Kronos {kronos_chg:+.1f}%")
    elif kronos_chg <= -KRONOS_THRESHOLD_PCT:
        votes["forecast"] = -1
        reasons.append(f"Kronos {kronos_chg:+.1f}%")
    else:
        votes["forecast"] = 0
    return votes, reasons


def classify(
    symbol: str,
    analysis: dict[str, Any],
    kronos: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pure classification for one symbol (no I/O)."""
    aclass = asset_class_for(symbol)
    price = float(analysis.get("current_price") or 0)
    metrics = analysis.get("metrics") or {}
    proj = (kronos or {}).get("kronos_projection") or {}
    kronos_chg = _num(proj.get("expected_change_percent"))
    rsi = _num(metrics.get("rsi_14"))
    atr = _num(metrics.get("atr"))

    votes, reasons = _votes(price, metrics, kronos_chg)
    score = sum(votes.values())
    cautions: list[str] = []
    if kronos_chg is None:
        cautions.append("no Kronos forecast")

    side: Side = "NO_EDGE"
    if score >= MIN_VOTES:
        if rsi is not None and rsi >= RSI_OVERBOUGHT:
            side = "NO_EDGE"
            cautions.append(f"RSI {rsi:.0f} — stretched, don't chase")
        else:
            side = "LONG"
    elif score <= -MIN_VOTES:
        if aclass != "stock":
            side = "STEER_CLEAR"
            cautions.append("ETF — no short, stay out")
        elif rsi is not None and rsi <= RSI_OVERSOLD:
            side = "STEER_CLEAR"
            cautions.append(f"RSI {rsi:.0f} — oversold, short is late")
        else:
            side = "SHORT"
    elif score <= -2:
        side = "STEER_CLEAR"

    entry = stop = target = None
    if side in {"LONG", "SHORT"}:
        if not atr or atr <= 0 or price <= 0:
            cautions.append("no ATR — levels unavailable")
            side = "NO_EDGE"
        else:
            sign = 1 if side == "LONG" else -1
            entry = round(price, 2)
            stop = round(price - sign * STOP_ATR * atr, 2)
            target = round(price + sign * TARGET_ATR * atr, 2)

    candles = analysis.get("chart_data") or []
    return {
        "symbol": symbol,
        "asset_class": aclass,
        "as_of": candles[-1].get("time") if candles else None,
        "side": side,
        "score": score,
        "votes": votes,
        "reasons": reasons,
        "cautions": cautions,
        "price": round(price, 2),
        "change_percent": analysis.get("change_percent"),
        "rsi": rsi,
        "atr": atr,
        "kronos_change_pct": kronos_chg,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_pct": round(STOP_ATR * atr / price * 100, 2) if entry and atr else None,
        "short_note": SHORT_NOTE if side == "SHORT" else None,
    }


def _load_analysis(symbol: str) -> dict[str, Any]:
    from src.alphadesk.analysis import analyze_symbol
    from src.alphadesk.cache import PRICE_MAX_AGE_S, get_cached, set_cached

    params = {"interval": "1d", "period": "6mo"}
    analysis = get_cached("analysis", symbol, max_age_s=PRICE_MAX_AGE_S, **params)
    if analysis is None:
        analysis = analyze_symbol(symbol, period="6mo", interval="1d")
        set_cached("analysis", symbol, analysis, **params)
    return analysis


def _load_kronos(symbol: str) -> dict[str, Any] | None:
    from src.alphadesk.cache import get_cached, set_cached
    from src.alphadesk.kronos_service import forecast_symbol

    kparams = {"lookback": 400, "pred_len": 15, "interval": "1d", "period": "2y"}
    kronos = get_cached("kronos", symbol, **kparams)
    if kronos is None:
        try:
            kronos = forecast_symbol(symbol, **kparams)
            set_cached("kronos", symbol, kronos, **kparams)
        except Exception as exc:  # Kronos is optional evidence
            logger.info("kronos skip %s: %s", symbol, exc)
            kronos = None
    return kronos


def track_record() -> dict[str, Any]:
    from src.alphadesk.survival import load_engine, scorecard_row

    row = scorecard_row(load_engine(ENGINE_ID))
    n = int(row.get("closed_trades") or 0)
    exp = row.get("expectancy_r")
    if n == 0:
        verdict = "untested"
    elif n < 30:
        verdict = "too_few_trades"
    elif exp is not None and exp > 0:
        verdict = "positive"
    else:
        verdict = "negative"
    return {**row, "verdict": verdict}


def _kronos_available() -> bool:
    from src.alphadesk.kronos_service import get_predictor

    try:
        get_predictor()
    except Exception:
        return False
    return True


IST = timezone(timedelta(hours=5, minutes=30))


def expected_session(now: datetime | None = None) -> str:
    """Latest NSE session that should have a bar (ignores exchange holidays)."""
    now = (now or datetime.now(IST)).astimezone(IST)
    day = now.date()
    if day.weekday() < 5 and (now.hour, now.minute) >= (9, 15):
        return day.isoformat()
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def _history_for(row: dict[str, Any], replay: dict[str, Any] | None) -> dict[str, Any] | None:
    if not replay:
        return None
    sym = (replay.get("by_symbol") or {}).get(row["symbol"]) or {}
    side = row["side"] if row["side"] in {"LONG", "SHORT"} else None
    stats = sym.get(side) if side else None
    return {
        "side": (replay.get(side.lower()) if side else None),
        "symbol_side": stats,
        "symbol": {k: sym.get(k) for k in ("trades", "win_rate", "avg_r")} if sym else None,
    }


# A row needs |score| >= MIN_VOTES; the forecast adds at most one vote, so
# names whose price votes alone are below MIN_VOTES - 1 can never qualify.
KRONOS_PREFILTER = MIN_VOTES - 1


def build_board(
    symbols: list[str] | None = None,
    *,
    with_kronos: bool = True,
    members: list[dict[str, str]] | None = None,
    progress: Callable[[int, int], None] | None = None,
    universe_key: str = "core",
) -> dict[str, Any]:
    """Classify each symbol. ``members`` carries name/industry for wider universes.

    The AI forecast runs only where the three price votes already lean one
    way by ``KRONOS_PREFILTER``; elsewhere it cannot change the side.
    """
    # Probe once: a missing model would otherwise cost a 2y fetch per symbol.
    with_kronos = with_kronos and _kronos_available()
    if members is None:
        universe = symbols or [
            s for s in mentor_scan_universe(include_etfs=True) if asset_class_for(s) != "liquid"
        ]
        members = [{"symbol": s, "name": s, "industry": ""} for s in universe]
    seen: dict[str, dict[str, str]] = {}
    for m in members:
        seen.setdefault(m["symbol"].upper().replace(".NS", "").replace(".BO", ""), m)

    rows: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    total = len(seen)
    for i, (raw, meta) in enumerate(seen.items()):
        try:
            analysis = _load_analysis(raw)
            sym = analysis.get("symbol") or raw
            row = classify(sym, analysis, None)
            if with_kronos and abs(row["score"]) >= KRONOS_PREFILTER:
                kronos = _load_kronos(raw)
                if kronos is not None:
                    row = classify(sym, analysis, kronos)
            row["name"] = meta.get("name") or sym
            row["industry"] = meta.get("industry") or ""
            rows.append(row)
        except Exception as exc:
            logger.warning("signal board skip %s: %s", raw, exc)
            failed.append({"symbol": raw, "error": str(exc)})
        if progress:
            progress(i + 1, total)

    from src.alphadesk.board_replay import load_replay

    replay = load_replay(universe_key)
    session = expected_session()
    bars_by_symbol = (replay or {}).get("bars") or {}
    for r in rows:
        r["history"] = _history_for(r, replay)
        bars = bars_by_symbol.get(r["symbol"])
        if bars is not None and bars < SHORT_HISTORY_BARS and r["asset_class"] == "stock":
            r["cautions"].append(
                f"listed recently: about {max(1, round(bars / 21))} months of prices, so signals and past results are thin"
            )
        r["stale"] = bool(r.get("as_of")) and str(r["as_of"]) < session

    def pick(side: str, reverse: bool) -> list[dict[str, Any]]:
        out = [r for r in rows if r["side"] == side]
        out.sort(key=lambda r: (r["score"], abs(r.get("kronos_change_pct") or 0)), reverse=reverse)
        return out

    return {
        "engine_id": ENGINE_ID,
        "universe": universe_key,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "long": pick("LONG", True),
        "short": pick("SHORT", False),
        "steer_clear": pick("STEER_CLEAR", False),
        "no_edge": [r["symbol"] for r in rows if r["side"] == "NO_EDGE"],
        "rows": rows,
        "scanned": len(rows),
        "kronos_live": with_kronos,
        "as_of": max((r["as_of"] for r in rows if r.get("as_of")), default=None),
        "failed": failed,
        "track_record": track_record(),
        "expected_session": session,
        "stale": [r["symbol"] for r in rows if r.get("stale")],
        "history": (
            {k: replay.get(k) for k in ("ran_at", "period", "rules", "overall", "long", "short", "plans")}
            if replay
            else None
        ),
        "method": (
            f"{MIN_VOTES}+ of 4 votes (trend, structure, momentum, Kronos) agree; "
            f"RSI guard {RSI_OVERSOLD:.0f}/{RSI_OVERBOUGHT:.0f}; "
            f"stop {STOP_ATR} ATR, target {TARGET_ATR} ATR."
        ),
    }
