"""What each strategy predicted on one stock, for the chart's dotted projection lines.

For a symbol and a set of strategies, returns every past signal with the levels the strategy expected
(entry, stop, target), and what actually happened (exit date, price, reason, result). Kronos returns its
saved forecast paths instead (``kronos_log``), each made with only the data up to its ``as_of`` day.

Every signal is computed from bars up to and including its signal day only: no hindsight. Replays are
labelled ``replay``; the desk's real paper trades on that stock are labelled ``live``.

Paper only: nothing here places an order.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BUILTIN = {
    "signal_board": "Signal board (swing, 4-vote)",
    "kronos": "Kronos forecast",
    "btst": "Overnight (BTST) pick",
}
COLORS = ["#2563eb", "#f59e0b", "#a855f7", "#14b8a6", "#ec4899", "#84cc16", "#f97316", "#06b6d4"]
MAX_STRATEGIES = 8


def options() -> list[dict[str, Any]]:
    """The strategies the chart can overlay, for the current profile."""
    from src.alphadesk import strategies as S

    out = [{"id": sid, "label": label} for sid, label in BUILTIN.items()]
    for c in S._load()["custom"]:
        if not c.get("archived"):
            out.append({"id": c["id"], "label": c.get("name") or c["id"]})
    for i, o in enumerate(out):
        o["color"] = COLORS[i % len(COLORS)]
        o["kind"] = "forecast" if o["id"] == "kronos" else "trades"
    return out


def _bars(symbol: str, years: int) -> pd.DataFrame:
    from src.alphadesk.analysis import _col, fetch_india_ohlcv

    _, df = fetch_india_ohlcv(symbol, period=f"{max(1, min(years, 10))}y", interval="1d")
    out = pd.DataFrame({k: _col(df, k) for k in ("open", "high", "low", "close", "volume")})
    out.index = pd.to_datetime(out.index)
    return out.dropna(subset=["open", "high", "low", "close"])


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 2) if math.isfinite(x) else None


def _outcome(reason: str | None) -> str:
    r = str(reason or "")
    if r.startswith("target"):
        return "target"
    if r.startswith("stop") or r == "trail":
        return "stop"
    if r in ("time", "max_bars", "close"):
        return "time"
    if r == "manual":
        return "hand-closed"
    return r or "open"


def _board(symbol: str, df: pd.DataFrame) -> list[dict[str, Any]]:
    from src.alphadesk.board_paper import list_trades
    from src.alphadesk.board_replay import replay_symbol

    items = []
    for t in replay_symbol(symbol, df[["open", "high", "low", "close"]], plan="fixed"):
        entry = float(t["entry"])
        items.append({
            "source": "replay", "side": t["side"], "signal_date": t["entry_date"], "entry_date": t["entry_date"],
            "entry": _f(entry), "stop": _f(t["stop"]), "target": _f(t["target"]),
            "exit_date": t.get("exit_date"), "exit": _f(t.get("exit_price")), "outcome": _outcome(t.get("exit_reason")),
            "r": _f(t.get("r_multiple")),
            "pct": _f(((t.get("exit_price") or entry) / entry - 1) * 100 * (1 if t["side"] == "LONG" else -1)),
            "off": t["side"] != "LONG",  # shorts are switched off on the board since 21 Nov 2026
        })
    for t in list_trades():
        if t.get("strategy_id") or str(t.get("symbol", "")).upper() != symbol:
            continue
        entry = _f(t.get("entry"))
        closed = t.get("status") == "CLOSED"
        items.append({
            "source": "live", "side": t.get("side"), "signal_date": str(t.get("opened_bar") or t.get("opened_at") or "")[:10],
            "entry_date": str(t.get("opened_bar") or t.get("opened_at") or "")[:10], "entry": entry,
            "stop": _f(t.get("stop")), "target": _f(t.get("target")),
            "exit_date": str(t.get("exit_date") or "")[:10] or None, "exit": _f(t.get("exit_price")),
            "outcome": _outcome(t.get("exit_reason")) if closed else "open", "r": _f(t.get("r_multiple")),
            "pct": _f(((t.get("exit_price") or entry) / entry - 1) * 100 * (1 if t.get("side") == "LONG" else -1))
            if closed and entry else None,
            "off": False,
        })
    return items


def _lab(symbol: str, df: pd.DataFrame, rules: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    from src.alphadesk import strategies as S
    from src.alphadesk.board_paper import list_trades

    a = S.atr(df).to_numpy()
    idx = {str(d)[:10]: i for i, d in enumerate(df.index)}
    items = []
    for t in S.simulate_symbol(symbol, df, rules):
        s = idx.get(t.signal_date)
        av = a[s] if s is not None and not np.isnan(a[s]) else None
        stop, target = S._levels(t.side, t.entry, av, rules["exit"])
        items.append({
            "source": "replay", "side": t.side, "signal_date": t.signal_date, "entry_date": t.entry_date,
            "entry": _f(t.entry), "stop": _f(stop), "target": _f(target), "exit_date": t.exit_date, "exit": _f(t.exit),
            "outcome": _outcome(t.reason), "r": None, "pct": _f(t.net_pct), "off": False,
        })
    for t in list_trades():
        if t.get("strategy_id") != sid or str(t.get("symbol", "")).upper() != symbol:
            continue
        entry = _f(t.get("entry"))
        closed = t.get("status") == "CLOSED"
        items.append({
            "source": "live", "side": t.get("side"), "signal_date": str(t.get("opened_bar") or t.get("opened_at") or "")[:10],
            "entry_date": str(t.get("opened_bar") or t.get("opened_at") or "")[:10], "entry": entry,
            "stop": _f(t.get("stop")), "target": _f(t.get("target")),
            "exit_date": str(t.get("exit_date") or "")[:10] or None, "exit": _f(t.get("exit_price")),
            "outcome": _outcome(t.get("exit_reason")) if closed else "open", "r": _f(t.get("r_multiple")),
            "pct": None, "off": False,
        })
    return items


def _btst(symbol: str) -> list[dict[str, Any]]:
    from src.alphadesk.btst import days

    items = []
    for kind in ("replay", "live"):
        for rec in days.list_days(kind):
            p = rec.get("pick") or {}
            if str(p.get("symbol", "")).upper() != symbol:
                continue
            o = p.get("outcome") or {}
            items.append({
                "source": kind, "side": "LONG", "signal_date": rec["date"], "entry_date": rec["date"],
                "entry": _f(p.get("entry")), "stop": _f(p.get("stop")), "target": _f(p.get("target")),
                "exit_date": rec.get("settled_on") or None, "exit": _f(o.get("exit")),
                "outcome": _outcome(o.get("reason")) if o else "open", "r": None, "pct": _f(o.get("net_pct")),
                "off": True,  # BTST paper is off since the 21 Nov 2026 audit
            })
    return items


def _kronos(symbol: str, df: pd.DataFrame) -> list[dict[str, Any]]:
    """Saved forecasts. Step k of a path is matched to the k-th real trading day after ``as_of`` (Kronos
    steps over weekdays and doesn't know NSE holidays), and drawn on that day."""
    from src.alphadesk import kronos_log

    days = [str(d)[:10] for d in df.index]
    closes = [float(c) for c in df["close"]]
    items = []
    for f in kronos_log.forecasts(symbol):
        path = f.get("path") or []
        start = _f(f.get("last_close"))
        if not path or not start:
            continue
        after = [i for i, d in enumerate(days) if d > f["as_of"]][: len(path)]
        drawn = [{"time": days[i] if k < len(after) else p["time"], "close": p["close"]}
                 for k, (p, i) in enumerate(zip(path, after + [None] * (len(path) - len(after))))]
        done = len(after) == len(path)
        end_actual = closes[after[-1]] if done else None
        end_pred = path[-1]["close"]
        items.append({
            "source": f.get("source", "live"), "as_of": f["as_of"], "start": start, "path": drawn,
            "predicted_pct": _f((end_pred / start - 1) * 100),
            "actual_pct": _f((end_actual / start - 1) * 100) if done else None,
            "direction_right": ((end_pred - start) * (end_actual - start) > 0) if done else None,
        })
    return items


def _summary(kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    if kind == "forecast":
        judged = [i for i in items if i.get("direction_right") is not None]
        right = sum(1 for i in judged if i["direction_right"])
        return {"forecasts": len(items), "judged": len(judged),
                "direction_right_pct": round(right / len(judged) * 100, 1) if judged else None}
    done = [i for i in items if i.get("outcome") not in ("open", None)]
    pcts = [i["pct"] for i in done if i.get("pct") is not None]
    return {"signals": len(items), "closed": len(done),
            "target_hit_pct": round(sum(1 for i in done if i["outcome"] == "target") / len(done) * 100, 1) if done else None,
            "avg_pct": round(float(np.mean(pcts)), 2) if pcts else None,
            "net_pct": round(float(np.sum(pcts)), 2) if pcts else None}


def signals(symbol: str, ids: list[str], years: int = 3) -> dict[str, Any]:
    """Every past signal of each strategy in ``ids`` on ``symbol``. Unknown ids are skipped."""
    from src.alphadesk import strategies as S
    from src.alphadesk.watchlist import clean_symbol

    symbol = clean_symbol(symbol)
    opts = {o["id"]: o for o in options()}
    ids = [i for i in dict.fromkeys(ids) if i in opts][:MAX_STRATEGIES]
    df = _bars(symbol, years) if ids else pd.DataFrame()
    customs = {c["id"]: c for c in S._load()["custom"]}
    out = []
    for sid in ids:
        o = opts[sid]
        try:
            if sid == "signal_board":
                items = _board(symbol, df)
            elif sid == "btst":
                items = _btst(symbol)
            elif sid == "kronos":
                items = _kronos(symbol, df)
            else:
                items = _lab(symbol, df, customs[sid]["rules"], sid)
            err = None
        except Exception as exc:  # noqa: BLE001 - one strategy's failure must not blank the chart
            logger.warning("chart signals: %s on %s failed", sid, symbol, exc_info=True)
            items, err = [], "Couldn't replay this strategy on this stock."
        items.sort(key=lambda i: str(i.get("signal_date") or i.get("as_of") or ""))
        out.append({**o, "items": items, "summary": _summary(o["kind"], items), "error": err})
    return {"symbol": symbol, "years": years, "strategies": out}
