"""The day log: plan at the open, mark through the day, settle at the close, file every day.

One JSON record per trading day under ``~/.vibe-trading/alphadesk/intraday/days/YYYY-MM-DD.json``.

* ``plan_open``  - the opening job (09:15 IST): resolve the universe, scan yesterday's daily
  charts on adjusted data, write down which symbols the day may trade and why.
* ``evaluate``   - find each symbol's opening-range trigger on completed 5-minute bars, take
  triggers in the order they happened up to the day's trade cap, and trace every trade bar by
  bar (entry, stop, target, R at every close). Used live (``refresh``) and after the fact.
* ``settle``     - the closing job: evaluate the whole day, classify it from the Nifty 50, file
  the summary, and add the day's bars to the local store.
* ``replay``     - research any past day: plan it using only data from before it, then settle it.

Everything is derived from completed bars with fixed rules, so a trade's ``decided_at`` is the
close of the bar that triggered it and a replay of a past day is the same calculation as the live
run. Results are paper trades simulated from 5-minute prices net of NSE intraday costs: not fills,
not advice. No function here places an order.
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.alphadesk import profiles
from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday import calendar as cal
from src.alphadesk.intraday import daytype, scanner, universe
from src.alphadesk.intraday.costs import DEFAULT_COSTS, net_r
from src.alphadesk.intraday.rules import (
    BAR, DEFAULT_FLAT_BY, clock, day_from_frame, hhmm, make_plan, orb_trigger, trace, atr_at,
)

DEFAULTS = {
    "universe": "nifty50",  # nifty50 | niftybank | nifty50_bank | nse_liquid
    "sides": "both",  # long | short | both
    "max_trades": 5,
    "risk_inr": 1000.0,
    "or_minutes": 15,
    "stop": "or-mid",
    "target": "2R",
    "min_vol_x": None,
    "market_filter": "off",  # off | with_nifty: only trade the way the Nifty has broken its own range
    "max_candidates": 60,
    "flat_by": DEFAULT_FLAT_BY,
    "open_time": "09:15",
    "close_time": "15:40",
    "live_refresh_minutes": 0,
}
_STOP_KINDS = ("or-mid", "or-edge", "pct", "price", "atr")
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
_RUNNING: dict[str, str] = {}


class MarketClosed(Exception):
    """NSE had no session on the day (a holiday the server has no list for). Nothing is filed."""


def days_dir() -> Path:
    path = ibars.intraday_dir() / "days"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- settings ------------------------------------------------------------------------------------


def validate_settings(raw: dict) -> dict:
    """Merge ``raw`` over the defaults and reject anything outside the allowed values."""
    s = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    if s["universe"] not in universe.KEYS:
        raise ValueError(f"universe must be one of {sorted(universe.KEYS)}")
    if s["sides"] not in ("long", "short", "both"):
        raise ValueError("sides must be long, short or both")
    if s["market_filter"] not in ("off", "with_nifty"):
        raise ValueError("market_filter must be off or with_nifty")
    s["max_trades"] = int(s["max_trades"])
    s["max_candidates"] = int(s["max_candidates"])
    s["or_minutes"] = int(s["or_minutes"])
    s["live_refresh_minutes"] = int(s["live_refresh_minutes"])
    s["risk_inr"] = float(s["risk_inr"])
    if not 1 <= s["max_trades"] <= 20:
        raise ValueError("max_trades must be 1 to 20")
    if not 5 <= s["max_candidates"] <= 300:
        raise ValueError("max_candidates must be 5 to 300")
    if s["or_minutes"] not in (15, 30):
        raise ValueError("or_minutes must be 15 or 30")
    if not 100 <= s["risk_inr"] <= 1_000_000:
        raise ValueError("risk_inr must be Rs 100 to Rs 10,00,000")
    if not 0 <= s["live_refresh_minutes"] <= 60:
        raise ValueError("live_refresh_minutes must be 0 to 60")
    if s["stop"].partition(":")[0] not in _STOP_KINDS:
        raise ValueError(f"stop must start with one of {_STOP_KINDS}")
    if not (s["target"].upper().endswith("R") or s["target"].startswith("price:")):
        raise ValueError("target must look like 2R or price:123")
    if s["min_vol_x"] is not None:
        s["min_vol_x"] = float(s["min_vol_x"])
    for k in ("flat_by", "open_time", "close_time"):
        hhmm(s[k])  # raises on a bad clock string
    return s


RULE_KEYS = ("universe", "sides", "max_trades", "risk_inr", "or_minutes", "stop", "target", "min_vol_x",
             "market_filter", "max_candidates", "flat_by")


def rules_match(recorded: dict | None, current: dict) -> bool:
    """True when a filed day was run with the same trading rules as ``current`` (schedule times and
    refresh cadence do not change what a day trades, so they are not compared)."""
    return bool(recorded) and all(recorded.get(k) == current.get(k) for k in RULE_KEYS)


def load_settings() -> dict:
    path = ibars.intraday_dir() / "settings.json"
    if path.exists():
        try:
            return validate_settings(json.loads(path.read_text()))
        except (ValueError, json.JSONDecodeError):
            pass
    return dict(DEFAULTS)


def save_settings(raw: dict) -> dict:
    s = validate_settings({**load_settings(), **raw})
    (ibars.intraday_dir() / "settings.json").write_text(json.dumps(s, indent=2))
    return s


# --- records -------------------------------------------------------------------------------------


def load_day(d: date) -> dict | None:
    path = days_dir() / f"{d.isoformat()}.json"
    return json.loads(path.read_text()) if path.exists() else None


def save_day(rec: dict) -> None:
    path = days_dir() / f"{rec['date']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, path)


def new_record(d: date, settings: dict) -> dict:
    return {"date": d.isoformat(), "weekday": d.strftime("%A"), "status": "empty", "settings": settings,
            "universe": None, "planned": [], "trades": [], "skipped": [], "not_triggered": [],
            "data_gaps": [], "day": None, "summary": summarize([]), "as_of": None, "runs": [],
            "note": "Paper trades simulated from 5-minute prices, net of NSE intraday costs. Not fills, not advice."}


def is_running(d: date) -> str | None:
    return _RUNNING.get(profiles.scoped_key(d.isoformat()))


def _log(rec: dict, kind: str, ok: bool, note: str, source: str) -> None:
    rec["runs"].append({"kind": kind, "at": ibars.now_ist().isoformat(timespec="seconds"),
                        "ok": ok, "note": note, "source": source})
    rec["runs"] = rec["runs"][-30:]


def summarize(trades: list[dict]) -> dict:
    done = [t for t in trades if t["status"] != "open"]
    wins = [t for t in done if t["r_net"] > 0]
    losses = [t for t in done if t["r_net"] <= 0 and t["status"] in ("stop", "stop_gap", "flat") and t["r_net"] < -0.05]
    flat = [t for t in done if t not in wins and t not in losses]
    net_inr = sum(t["pnl_inr"] for t in trades)
    best = max(trades, key=lambda t: t["pnl_inr"], default=None)
    worst = min(trades, key=lambda t: t["pnl_inr"], default=None)
    return {"taken": len(trades), "open": len(trades) - len(done), "wins": len(wins),
            "losses": len(losses), "flat": len(flat),
            "win_rate": round(len(wins) / len(done), 4) if done else None,
            "net_r": round(sum(t["r_net"] for t in trades), 3),
            "gross_r": round(sum(t["r_gross"] for t in trades), 3),
            "net_inr": round(net_inr, 2),
            "best": {"symbol": best["symbol"], "pnl_inr": best["pnl_inr"]} if best else None,
            "worst": {"symbol": worst["symbol"], "pnl_inr": worst["pnl_inr"]} if worst else None}


# --- data ----------------------------------------------------------------------------------------


def _session_frame(symbol: str, d: date, live: bool) -> pd.DataFrame | None:
    """One symbol's 5-minute bars for date ``d``: live fetch today, local store or the 60-day
    window for a past day."""
    if live:
        _, f = ibars.fetch(symbol, range_="1d")
    else:
        stored = ibars.load_store(symbol)
        if not stored.empty and (stored.index.date == d).any():
            f = stored
        else:
            _, f = ibars.fetch(symbol, range_="60d", ttl=600)
    if f is None or f.empty:
        return None
    f = f[f.index.date == d]
    return f if not f.empty else None


def _index_day(ticker: str, d: date, live: bool):
    """(Day, previous close) of an index for date ``d``."""
    frame = ibars.fetch_ticker(ticker, "5d" if live else "60d", ttl=0 if live else 600)
    if frame.empty:
        return None, None
    sessions = ibars.split_sessions(frame)
    prev = [k for k in sorted(sessions) if k < d]
    prev_close = float(sessions[prev[-1]]["close"].iloc[-1]) if prev else None
    g = sessions.get(d)
    return (day_from_frame(d, g) if g is not None else None), prev_close


# --- the three steps -----------------------------------------------------------------------------


def plan_open(d: date, settings: dict, past: bool = False) -> dict:
    """Decide which symbols the day may trade. Past days use only data before ``d``."""
    if not cal.is_trading_day(d):
        raise ValueError(f"NSE is closed on {d.isoformat()} ({d.strftime('%A')}).")
    uni = universe.resolve(settings["universe"], settings["max_candidates"], as_of=d if past else None)
    scan = scanner.scan_daily(uni["symbols"], as_of=d)
    planned = []
    for r in scan["rows"][: settings["max_candidates"]]:
        planned.append({**r, "tags": uni["tags"].get(r["symbol"], [])})
    return {"universe": {"key": uni["key"], "label": uni["label"], "eligible": uni["eligible"],
                         "covered": uni["covered"], "charts_read": scan["fetched"],
                         "liquid": scan["liquid"], "data_source": scan["source"]},
            "planned": planned}


def market_state(idx_day, or_minutes: int, t_close: int) -> str:
    """The Nifty's own opening-range verdict as of the close of the bar ending ``t_close``:
    ``long`` above its range high, ``short`` below its low, else ``neutral`` (or ``unknown``)."""
    if idx_day is None:
        return "unknown"
    n = or_minutes // BAR
    if len(idx_day) <= n:
        return "unknown"
    rng_hi, rng_lo = float(idx_day.h[:n].max()), float(idx_day.l[:n].min())
    for i in range(len(idx_day)):
        if int(idx_day.t[i]) + BAR == t_close:
            c = float(idx_day.c[i])
            return "long" if c > rng_hi else "short" if c < rng_lo else "neutral"
    return "unknown"


def evaluate(d: date, rec: dict, settings: dict, live_now: datetime | None) -> dict:
    """Triggers, trades and their paths for the planned symbols, from completed bars only."""
    now_min = (live_now.hour * 60 + live_now.minute) if live_now else None
    sides = ("long", "short") if settings["sides"] == "both" else (settings["sides"],)
    flat_by = hhmm(settings["flat_by"])
    found, gaps, quiet, last_bar = [], [], [], 0
    days: dict[str, tuple] = {}
    for p in rec["planned"]:
        sym = p["symbol"]
        frame = _session_frame(sym, d, live=live_now is not None)
        day = day_from_frame(d, frame) if frame is not None else None
        if day is None:
            gaps.append(sym)
            continue
        days[sym] = (day, frame)
        last_bar = max(last_bar, int(day.t[-1]) + BAR)
        hit = False
        for side in sides:
            trig = orb_trigger(day, side, settings["or_minutes"], min_vol_x=settings["min_vol_x"], now_min=now_min)
            if trig["status"] == "triggered":
                found.append((trig["time"], -trig["vol_x"], sym, side, trig))
                hit = True
        if not hit:
            quiet.append(sym)
    found.sort(key=lambda x: (x[0], x[1], x[2]))
    trades, skipped, taken = [], [], set()
    idx_day = _index_day("^NSEI", d, live_now is not None)[0] if settings["market_filter"] == "with_nifty" else None
    if settings["market_filter"] == "with_nifty" and idx_day is None:
        gaps.append("^NSEI (market filter skipped: no index prices)")
    for t_close, _, sym, side, trig in found:
        if idx_day is not None:
            state = market_state(idx_day, settings["or_minutes"], t_close)
            if state != side:
                why = {"neutral": "the Nifty was still inside its opening range",
                       "long": "the Nifty was above its opening range", "short": "the Nifty was below its opening range",
                       "unknown": "no Nifty price for that moment"}[state]
                skipped.append({"symbol": sym, "side": side, "decided_at": clock(t_close),
                                "reason": f"against the market: {why}"})
                continue
        if sym in taken:
            skipped.append({"symbol": sym, "side": side, "decided_at": clock(t_close),
                            "reason": "already in a trade on this symbol"})
            continue
        if len(trades) >= settings["max_trades"]:
            skipped.append({"symbol": sym, "side": side, "decided_at": clock(t_close),
                            "reason": f"the day's cap of {settings['max_trades']} trades was already used"})
            continue
        day, _ = days[sym]
        rng = trig["range"]
        atr_v = atr_at(day, trig["idx"]) if settings["stop"].startswith("atr") else None
        try:
            plan = make_plan(side, trig["price"], rng["high"], rng["low"], settings["stop"],
                             settings["target"], atr_v, settings["flat_by"])
        except ValueError as exc:
            skipped.append({"symbol": sym, "side": side, "decided_at": clock(t_close), "reason": f"plan refused: {exc}"})
            continue
        tr = trace(day, trig["idx"], plan, flat_by)
        sign = 1 if side == "long" else -1
        qty = max(int(settings["risk_inr"] // plan.risk), 1)
        r_gross = sign * (tr.exit_price - plan.entry) / plan.risk
        r_net = float(net_r(side, plan.entry, tr.exit_price, plan.risk, settings["risk_inr"], DEFAULT_COSTS))
        status = tr.status
        last_t = tr.path[-1][0]
        tags = next((p["tags"] for p in rec["planned"] if p["symbol"] == sym), [])
        trades.append({
            "id": f"{d.isoformat()}-{sym}-{side}", "symbol": sym, "side": side, "tags": tags,
            "decided_at": clock(t_close), "entry": plan.entry, "stop": plan.stop, "target": plan.target,
            "risk_per_share": plan.risk, "risk_pct": plan.risk_pct, "reward_risk": plan.reward_risk,
            "qty": qty, "status": status, "exit_time": None if status == "open" else clock(last_t),
            "last_price": round(float(tr.exit_price), 2), "last_time": clock(last_t),
            "r_gross": round(r_gross, 3), "r_net": round(r_net, 3),
            "pnl_inr": round(r_net * qty * plan.risk, 2), "mfe_r": round(tr.mfe, 2), "mae_r": round(tr.mae, 2),
            "range": {"high": rng["high"], "low": rng["low"]}, "volume_x": trig["vol_x"],
            "path": [[m, round(r, 3)] for m, r in tr.path],
        })
        taken.add(sym)
    return {"trades": trades, "skipped": skipped, "data_gaps": gaps,
            "not_triggered": [s for s in quiet if s not in taken][:300],
            "as_of": clock(last_bar) if last_bar else None, "_frames": days}


def session_state(d: date, live: bool) -> str:
    """``open`` if the Nifty traded on ``d``, ``closed`` if prices exist for other days but not this
    one (a holiday), ``unknown`` if no prices came back at all (an outage must not look like a holiday)."""
    frame = ibars.fetch_ticker("^NSEI", "5d" if live else "60d", ttl=0 if live else 600)
    if frame.empty:
        return "unknown"
    return "open" if (frame.index.date == d).any() else "closed"


def classify_day(d: date, live: bool) -> dict | None:
    out = {}
    for key, ticker in (("nifty50", "^NSEI"), ("niftybank", "^NSEBANK")):
        day, prev = _index_day(ticker, d, live)
        if day is not None:
            out[key] = daytype.classify(day, prev)
    if "nifty50" not in out:
        return None
    return {"type": out["nifty50"]["type"], "label": out["nifty50"]["label"], "rule": daytype.RULES, **out}


# --- jobs ----------------------------------------------------------------------------------------


def _run(d: date, kind: str, source: str, fn) -> dict:
    key = d.isoformat()
    skey = profiles.scoped_key(key)  # the owner and each profile may run the same day at once
    if not _LOCKS[skey].acquire(blocking=False):
        raise RuntimeError(f"{key} is already being updated")
    _RUNNING[skey] = kind
    try:
        rec = load_day(d) or new_record(d, load_settings())
        try:
            note = fn(rec)
            _log(rec, kind, True, note, source)
        except MarketClosed:
            (days_dir() / f"{key}.json").unlink(missing_ok=True)  # a holiday leaves no record
            raise
        except Exception as exc:  # noqa: BLE001 - recorded on the day, then re-raised for the caller
            _log(rec, kind, False, f"{type(exc).__name__}: {exc}", source)
            save_day(rec)
            raise
        save_day(rec)
        return rec
    finally:
        _RUNNING.pop(skey, None)
        _LOCKS[skey].release()


def _apply(rec: dict, ev: dict, settings: dict) -> None:
    rec["trades"], rec["skipped"] = ev["trades"], ev["skipped"]
    rec["not_triggered"], rec["data_gaps"], rec["as_of"] = ev["not_triggered"], ev["data_gaps"], ev["as_of"]
    rec["summary"] = summarize(ev["trades"])


def run_open(d: date, source: str = "manual") -> dict:
    """The opening job: plan the day (09:15 IST). Does not look at today's prices yet."""
    def go(rec):
        rec["settings"] = load_settings()
        plan = plan_open(d, rec["settings"])
        rec.update(plan)
        rec["status"] = "planned"
        return f"{len(plan['planned'])} symbols planned from {plan['universe']['liquid']} liquid"
    return _run(d, "open", source, go)


def run_refresh(d: date, source: str = "manual", now: datetime | None = None) -> dict:
    """Update today's triggers and marks from completed bars (live)."""
    def go(rec):
        if not rec["planned"]:
            rec["settings"] = load_settings()
            rec.update(plan_open(d, rec["settings"]))
        ev = evaluate(d, rec, rec["settings"], now or ibars.now_ist())
        _apply(rec, ev, rec["settings"])
        rec["status"] = "live"
        rec["day"] = classify_day(d, live=True)
        return f"{len(ev['trades'])} trades, {len(ev['skipped'])} skipped, as of {ev['as_of']}"
    return _run(d, "refresh", source, go)


def _settle(rec: dict, d: date, live: bool) -> str:
    if session_state(d, live) == "closed":
        raise MarketClosed(f"NSE had no session on {d.isoformat()}")
    ev = evaluate(d, rec, rec["settings"], None)
    frames = ev.pop("_frames")
    _apply(rec, ev, rec["settings"])
    rec["day"] = classify_day(d, live=live)
    rec["status"] = "settled"
    if live:  # grow the local store with the day just finished, no extra requests
        for sym, (_, frame) in frames.items():
            try:
                ibars.merge_into_store(sym, frame)
            except Exception:  # noqa: BLE001 - the store is a bonus, never a reason to fail the close
                pass
    s = rec["summary"]
    return f"settled: {s['taken']} trades, {s['wins']} won, {s['net_r']:+.2f}R net, day {rec['day']['label'] if rec['day'] else 'unclassified'}"


def run_close(d: date, source: str = "manual") -> dict:
    """The closing job: evaluate the whole day, classify it, file the summary."""
    def go(rec):
        note = ""
        if not rec["planned"]:
            rec["settings"] = load_settings()
            rec.update(plan_open(d, rec["settings"], past=d < ibars.now_ist().date()))
            note = "opening scan was missed, so the day was planned late from prices. "
        return note + _settle(rec, d, live=d >= ibars.now_ist().date())
    return _run(d, "close", source, go)


def run_replay(d: date, source: str = "manual") -> dict:
    """Research a past day: plan it from data before it, then settle it."""
    if d >= ibars.now_ist().date():
        raise ValueError("Replay is for past days. Use Plan open / Refresh / Close for today.")

    def go(rec):
        rec["settings"] = load_settings()
        rec.update(plan_open(d, rec["settings"], past=True))
        return "replayed from stored prices; " + _settle(rec, d, live=False)
    return _run(d, "replay", source, go)


def backfill(start: date, end: date, source: str = "backfill", force: bool = False) -> dict:
    """File every NSE trading day in [start, end] by replaying it from stored prices, oldest first.
    Settled days are kept unless ``force``; a day that cannot be built is reported, not fatal, and
    days the market was closed are listed separately and filed as nothing."""
    done, skipped, failed, closed = [], [], [], []
    d = start
    today = ibars.now_ist().date()
    while d <= min(end, today - timedelta(days=1)):
        if cal.is_trading_day(d):
            rec = load_day(d)
            if rec and rec.get("status") == "settled" and not force:
                skipped.append(d.isoformat())
            else:
                try:
                    run_replay(d, source)
                    done.append(d.isoformat())
                except MarketClosed:
                    closed.append(d.isoformat())  # a holiday: nothing to file
                except Exception as exc:  # noqa: BLE001 - one bad day must not stop the rest
                    failed.append({"date": d.isoformat(), "error": f"{type(exc).__name__}: {exc}"})
        d += timedelta(days=1)
    return {"filed": done, "kept": skipped, "failed": failed, "closed": closed}


_CACHE: dict[str, tuple[int, int, dict]] = {}


def _records(newest_first: bool = True) -> list[dict]:
    """Every filed day, parsed once per file version (the page asks several times per load)."""
    out = []
    for path in sorted(days_dir().glob("[!.]*.json"), reverse=newest_first):
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        hit = _CACHE.get(str(path))
        if not hit or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
            try:
                hit = (st.st_mtime_ns, st.st_size, json.loads(path.read_text(encoding="utf-8")))
            except ValueError:  # bad JSON or bytes that are not UTF-8
                continue
            _CACHE[str(path)] = hit
        out.append(hit[2])
    return out


# --- reading back --------------------------------------------------------------------------------


def list_days(limit: int = 400, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    rows = []
    current = load_settings()
    for r in _records():
        if (date_from and r["date"] < date_from) or (date_to and r["date"] > date_to):
            continue
        if len(rows) >= limit:
            break
        s = r.get("summary") or {}
        rows.append({"date": r["date"], "weekday": r.get("weekday"), "status": r["status"],
                     "type": (r.get("day") or {}).get("type"), "label": (r.get("day") or {}).get("label"),
                     "planned": len(r.get("planned") or []), "taken": s.get("taken", 0),
                     "wins": s.get("wins", 0), "losses": s.get("losses", 0), "flat": s.get("flat", 0),
                     "win_rate": s.get("win_rate"), "net_r": s.get("net_r", 0.0), "net_inr": s.get("net_inr", 0.0),
                     "rules_match": rules_match(r.get("settings"), current)})
    return rows


def _outcome(t: dict) -> str:
    if t["status"] == "open":
        return "open"
    return "won" if t["r_net"] > 0 else "lost" if t["r_net"] < -0.05 else "flat"


_SLIM = ("id", "symbol", "side", "tags", "decided_at", "entry", "stop", "target", "qty", "status",
         "exit_time", "last_price", "last_time", "r_net", "pnl_inr", "mfe_r", "mae_r")


def search(date_from: str | None = None, date_to: str | None = None, symbol: str | None = None,
           side: str | None = None, outcome: str | None = None, kind: str | None = None,
           index: str | None = None, limit_days: int = 400) -> dict:
    """Trades across filed days, listed date by date, newest first.

    ``date_from`` / ``date_to`` are the dates the trades were taken (inclusive, YYYY-MM-DD).
    ``symbol`` matches part of a name, ``side`` is long/short, ``outcome`` is won/lost/flat/open,
    ``kind`` is a day type or its label (e.g. ``trend_up`` / "Trend up"), ``index`` is nifty50 or
    niftybank. Days with no matching trade are left out. Paths are dropped: open the day for them.
    """
    sym = (symbol or "").strip().upper()
    kind_l = (kind or "").strip().lower()
    out_days, tot = [], {"trades": 0, "wins": 0, "losses": 0, "flat": 0, "open": 0, "net_inr": 0.0, "net_r": 0.0}
    truncated = False
    current = load_settings()
    for r in _records():
        d = r["date"]
        if (date_from and d < date_from) or (date_to and d > date_to):
            continue
        day = r.get("day") or {}
        if kind_l and kind_l not in (str(day.get("type", "")).lower(), str(day.get("label", "")).lower()):
            continue
        hits = []
        for t in r.get("trades") or []:
            if sym and sym not in t["symbol"]:
                continue
            if side in ("long", "short") and t["side"] != side:
                continue
            if outcome in ("won", "lost", "flat", "open") and _outcome(t) != outcome:
                continue
            if index and index not in (t.get("tags") or []):
                continue
            hits.append({k: t.get(k) for k in _SLIM} | {"outcome": _outcome(t)})
        if not hits:
            continue
        if len(out_days) >= limit_days:
            truncated = True
            break
        cnt = {k: sum(1 for h in hits if h["outcome"] == o) for k, o in (("wins", "won"), ("losses", "lost"), ("flat", "flat"), ("open", "open"))}
        net_inr = round(sum(h["pnl_inr"] for h in hits), 2)
        net_r = round(sum(h["r_net"] for h in hits), 3)
        out_days.append({"date": d, "weekday": r.get("weekday"), "status": r["status"], "label": day.get("label"),
                         "type": day.get("type"), "rules_match": rules_match(r.get("settings"), current),
                         "summary": {"trades": len(hits), **cnt, "net_inr": net_inr, "net_r": net_r}, "trades": hits})
        tot["trades"] += len(hits)
        for k in ("wins", "losses", "flat", "open"):
            tot[k] += cnt[k]
        tot["net_inr"] = round(tot["net_inr"] + net_inr, 2)
        tot["net_r"] = round(tot["net_r"] + net_r, 3)
    done = tot["wins"] + tot["losses"] + tot["flat"]
    tot["win_rate"] = round(tot["wins"] / done, 4) if done else None
    tot["days"] = len(out_days)
    return {"days": out_days, "totals": tot, "truncated": truncated}


def stats() -> dict:
    """Cumulative win ratio over settled days, and how each kind of day did."""
    recs = [r for r in _records(newest_first=False) if r.get("status") == "settled"]
    current = load_settings()
    mixed = sum(1 for r in recs if not rules_match(r.get("settings"), current))
    series, by_type, by_side, by_tag = [], defaultdict(list), defaultdict(list), defaultdict(list)
    cum_n = cum_w = 0
    cum_r = cum_inr = 0.0
    for r in recs:
        trades = r.get("trades") or []
        done = [t for t in trades if t["status"] != "open"]
        w = sum(1 for t in done if t["r_net"] > 0)
        cum_n, cum_w = cum_n + len(done), cum_w + w
        cum_r += sum(t["r_net"] for t in trades)
        cum_inr += sum(t["pnl_inr"] for t in trades)
        series.append({"date": r["date"], "trades": len(done), "wins": w,
                       "cum_trades": cum_n, "cum_wins": cum_w,
                       "cum_win_rate": round(cum_w / cum_n, 4) if cum_n else None,
                       "cum_net_r": round(cum_r, 3), "cum_net_inr": round(cum_inr, 2)})
        typ = (r.get("day") or {}).get("label") or "Unclassified"
        for t in done:
            by_type[typ].append(t)
            by_side[t["side"]].append(t)
            for tag in t.get("tags") or ["other"]:
                by_tag[tag].append(t)

    def agg(groups, days_by=None):
        out = []
        for k, ts in groups.items():
            n = len(ts)
            out.append({"key": k, "trades": n, "wins": sum(1 for t in ts if t["r_net"] > 0),
                        "win_rate": round(sum(1 for t in ts if t["r_net"] > 0) / n, 4),
                        "avg_r": round(sum(t["r_net"] for t in ts) / n, 3),
                        "net_inr": round(sum(t["pnl_inr"] for t in ts), 2),
                        "days": (days_by or {}).get(k)})
        return sorted(out, key=lambda x: -x["trades"])

    days_by_type = defaultdict(int)
    for r in recs:
        days_by_type[(r.get("day") or {}).get("label") or "Unclassified"] += 1
    return {"days": len(recs), "mixed_rules": mixed, "series": series, "by_type": agg(by_type, days_by_type),
            "by_side": agg(by_side), "by_index": agg(by_tag)}
