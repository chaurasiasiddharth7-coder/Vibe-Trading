"""Filed BTST days: the 15:20 scan, the next-day settle, the replay from stored prices, and stats.

Layout (shared by every desk profile: the market is the same for everyone)::

    ~/.vibe-trading/alphadesk/btst/live/YYYY-MM-DD.json     one file per scanned day
    ~/.vibe-trading/alphadesk/btst/replay/YYYY-MM-DD.json   rebuilt from daily bars, never mixed with live

A record holds the pick, every candidate with its warnings, and (after the next session) each one's
outcome under the same exit rules, so the warnings keep a record of their own.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.alphadesk import profiles
from src.alphadesk.btst import rules as R

logger = logging.getLogger(__name__)

KINDS = ("live", "replay")
HISTORY_DAYS = 420  # calendar days of daily bars needed for the 1-year high
REPLAY_LOCKED_NEAR_HIGH = 0.997  # closing price within 0.3% of the high, see replay()
_LOCK = threading.Lock()
RUNNING: dict[str, str] = {}  # job name -> what it is doing (one of each at a time)


class NotReady(Exception):
    """The prices needed are not there yet (a settle before the next session has closed)."""


def root() -> Path:
    path = profiles.root_for(None) / "btst"
    for k in KINDS:
        (path / k).mkdir(parents=True, exist_ok=True)
    return path


def day_path(d: date, kind: str = "live") -> Path:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    return root() / kind / f"{d.isoformat()}.json"


def load_day(d: date, kind: str = "live") -> dict | None:
    path = day_path(d, kind)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("unreadable btst day file %s", path)
        return None


def save_day(rec: dict, kind: str | None = None) -> None:
    kind = kind or rec.get("kind", "live")
    path = day_path(date.fromisoformat(rec["date"]), kind)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _json_default(x):
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if np.isnan(x) else float(x)
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return x.isoformat()
    raise TypeError(type(x).__name__)


def list_days(kind: str = "live", limit: int | None = None) -> list[dict]:
    files = sorted((root() / kind).glob("*.json"), reverse=True)
    files = [f for f in files if not f.name.startswith(".")]
    out = []
    for f in files[: limit or None]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out


def row_of(rec: dict) -> dict:
    """The one-line view of a day for lists."""
    p, o = rec.get("pick"), (rec.get("pick") or {}).get("outcome")
    return {"date": rec["date"], "kind": rec.get("kind", "live"), "status": rec.get("status"),
            "symbol": p["symbol"] if p else None, "gain_pct": p.get("gain_pct") if p else None,
            "entry": p.get("entry") if p else None, "net_pct": o.get("net_pct") if o else None,
            "reason": o.get("reason") if o else None, "ambiguous": bool(o and o.get("ambiguous")),
            "candidates": len(rec.get("candidates") or []), "skip_reason": rec.get("skip_reason"),
            "breadth_pct": (rec.get("market") or {}).get("breadth_pct")}


# --- prices from the NSE store ----------------------------------------------------------------


def _date_bound(path: Path, d: date):
    """A filter value matching the store's date column type (date32 in the NSE store, timestamps in some copies)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    typ = pq.read_schema(path).field("date").type
    return d if pa.types.is_date(typ) else pd.Timestamp(d)


def store_bars(start: date, end: date) -> pd.DataFrame:
    from backtest.loaders import nse_store

    path = nse_store.store_dir() / "bars.parquet"
    if not path.exists():
        raise RuntimeError("BTST needs the NSE store for history: run scripts/nse_pull.py --update first.")
    cols = ["symbol", "date", "open", "high", "low", "close", "prev_close", "volume"]
    bars = pd.read_parquet(path, columns=cols, filters=[("date", ">=", _date_bound(path, start)),
                                                         ("date", "<=", _date_bound(path, end))])
    bars["date"] = pd.to_datetime(bars["date"])
    # one row per symbol per session (a symbol can carry two ISINs across a change)
    return bars.sort_values(["symbol", "date", "volume"]).drop_duplicates(["symbol", "date"], keep="last")


def store_last_date() -> date | None:
    from backtest.loaders import nse_store

    path = nse_store.store_dir() / "bars.parquet"
    if not path.exists():
        return None
    return pd.Timestamp(pd.read_parquet(path, columns=["date"])["date"].max()).date()


# --- building a day ----------------------------------------------------------------------------


def build(d: date, snap: pd.DataFrame, prior: pd.DataFrame, rules: R.BtstRules, kind: str,
          info: dict | None = None) -> dict:
    """A day record from a snapshot (today's prices) and the prior stats (yesterday and before)."""
    br, n_liq = R.breadth(snap, rules)
    feat = R.features(snap, prior, br)
    cands, left_out = R.candidates(feat, rules)
    rows, pick, reason = R.choose(cands, rules)
    return {
        "date": d.isoformat(), "kind": kind, "rules_version": R.RULES_VERSION, "rules": rules.as_dict(),
        "status": "picked" if pick else "skipped",
        "market": {"breadth_pct": None if br is None else round(br * 100, 1), "liquid": n_liq},
        "coverage": info or {}, "left_out": left_out,
        "candidates": rows, "pick": pick, "skip_reason": reason, "runs": [],
    }


def universe_from(prior: pd.DataFrame, rules: R.BtstRules) -> list[str]:
    ok = prior[(prior["turn20"] >= rules.min_avg_traded_cr * 1e7) & (prior["sessions"] >= 10)]
    return ok.sort_values("turn20", ascending=False).index.tolist()


def run_scan(d: date | None = None, source: str = "manual", rules: R.BtstRules = R.DEFAULT_RULES,
             now: datetime | None = None) -> dict:
    """The live scan: prices now, history to yesterday, filed as today's record."""
    from src.alphadesk.intraday import bars as ibars
    from src.alphadesk.intraday import calendar as cal
    from src.alphadesk.btst import snapshot

    now = now or ibars.now_ist()
    d = d or now.date()
    if d != now.date():
        raise ValueError("A live scan can only be run for today; use the replay for past days.")
    if not cal.is_trading_day(d):
        raise ValueError("The market is closed today.")
    stats = R.rolling_stats(store_bars(d - timedelta(days=HISTORY_DAYS), d - timedelta(days=1)))
    prior = R.prior_stats(stats, pd.Timestamp(d))
    last_hist = prior["last_date"].max() if not prior.empty else None
    uni = universe_from(prior, rules)
    snap, info = snapshot.take(uni)
    if snap.empty:
        raise RuntimeError("No live prices: " + "; ".join(info.get("notes") or ["every source failed"]))
    rec = build(d, snap, prior, rules, "live", info)
    rec["scanned_at"] = now.strftime("%H:%M")
    rec["history_to"] = None if last_hist is None else pd.Timestamp(last_hist).date().isoformat()
    notes = []
    if rec["history_to"] and (d - date.fromisoformat(rec["history_to"])).days > 5:
        notes.append(f"History ends {rec['history_to']}: run scripts/nse_pull.py --update so the 20-day and 1-year figures are current.")
    if now.strftime("%H:%M") < "15:00":
        notes.append(f"Scanned at {rec['scanned_at']}; the rules were tested on prices near 15:20.")
    if info.get("universe") and info.get("covered", 0) < 0.8 * info["universe"]:
        notes.append(f"Live prices covered {info['covered']} of {info['universe']} stocks in the scan list.")
    rec["notes"] = notes
    old = load_day(d, "live")
    if old and old.get("status") == "settled":
        raise ValueError("Today's record is already settled.")
    rec["runs"] = (old or {}).get("runs", []) + [{"kind": "scan", "at": now.isoformat(timespec="seconds"), "source": source}]
    save_day(rec, "live")
    return rec


# --- settling ------------------------------------------------------------------------------------


def _next_session(d: date) -> date:
    from src.alphadesk.intraday import calendar as cal

    n = d + timedelta(days=1)
    for _ in range(10):
        if cal.is_trading_day(n):
            return n
        n += timedelta(days=1)
    raise ValueError(f"no trading day within 10 days after {d}")


def _daily_from_store(symbols: list[str], d1: date) -> dict[str, dict]:
    try:
        bars = store_bars(d1, d1)
    except RuntimeError:
        return {}
    bars = bars[bars["symbol"].isin(symbols)]
    return {r.symbol: {"open": r.open, "high": r.high, "low": r.low, "close": r.close, "prev_close": r.prev_close}
            for r in bars.itertuples()}


def _intraday_from_yahoo(symbol: str, d1: date) -> pd.DataFrame:
    from src.alphadesk.intraday import bars as ibars

    try:
        _, frame = ibars.fetch(symbol, range_="5d")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return frame[frame.index.date == d1] if not frame.empty else frame


def settle_record(rec: dict, d1: date, daily: dict[str, dict], path_for=None,
                  rules: R.BtstRules | None = None, strict: bool = True) -> dict:
    """Outcome for the pick and every candidate. ``path_for(symbol)`` may return intraday bars for
    an exact settle of the pick; otherwise the daily bar is used (stop assumed first if both hit)."""
    rules = R.BtstRules(**rec["rules"]) if rules is None else rules
    no_stop = replace(rules, use_stop=False)
    missing = []
    for c in rec.get("candidates") or []:
        bar = daily.get(c["symbol"])
        if not bar:
            missing.append(c["symbol"])
            c["outcome"] = None
            continue
        if _corporate_action(c["last"], bar):
            c["outcome"] = {"data_check": True, "note": "Next-day prices jump like a split or bonus: left out of the record."}
            continue
        c["outcome"] = R.exit_daily(c["last"], bar["open"], bar["high"], bar["low"], bar["close"], rules)
        c["outcome_no_stop"] = R.exit_daily(c["last"], bar["open"], bar["high"], bar["low"], bar["close"], no_stop)
    p = rec.get("pick")
    if p:
        out = None
        if path_for is not None:
            path = path_for(p["symbol"])
            if path is not None and not path.empty:
                try:
                    out = R.exit_path(p["entry"], path, rules, exit_time="15:25")
                except ValueError:
                    out = None
        chosen = next((c for c in rec["candidates"] if c["chosen"]), None)
        if chosen:
            p["outcome_no_stop"] = chosen.get("outcome_no_stop")
        if out is None:
            out = chosen.get("outcome") if chosen else None
        if out is None and p["symbol"] in missing:
            if strict:
                raise NotReady(f"No prices for {p['symbol']} on {d1.isoformat()} yet.")
            out = {"data_check": True, "note": f"No {d1.isoformat()} prices (suspended or renamed?): left out of the record."}
        p["outcome"] = out
    rec["settled_on"] = d1.isoformat()
    rec["status"] = "settled"
    rec["missing"] = missing
    return rec


def _corporate_action(entry: float, bar: dict) -> bool:
    pc = bar.get("prev_close")
    if pc and entry and abs(pc / entry - 1) > 0.005:
        return True
    r = bar["open"] / entry if entry else 1.0
    return r < 0.6 or r > 1.6


def run_settle(d: date, source: str = "manual", now: datetime | None = None) -> dict:
    """Settle the live record of ``d`` from the next session's prices (store first, then Yahoo)."""
    from src.alphadesk.intraday import bars as ibars

    now = now or ibars.now_ist()
    rec = load_day(d, "live")
    if not rec:
        raise ValueError(f"No live record for {d.isoformat()}.")
    d1 = _next_session(d)
    if d1 > now.date() or (d1 == now.date() and now.strftime("%H:%M") < "15:31"):
        raise NotReady(f"{d.isoformat()} settles after the {d1.isoformat()} close.")
    syms = [c["symbol"] for c in rec.get("candidates") or []]
    daily = _daily_from_store(syms, d1)
    paths: dict[str, pd.DataFrame] = {}
    if len(daily) < len(syms):
        for s in syms:
            if s in daily:
                continue
            fr = _intraday_from_yahoo(s, d1)
            if not fr.empty:
                paths[s] = fr
                daily[s] = {"open": float(fr["open"].iloc[0]), "high": float(fr["high"].max()),
                            "low": float(fr["low"].min()), "close": float(fr["close"].iloc[-1]), "prev_close": None}
    pick = (rec.get("pick") or {}).get("symbol")
    if pick and pick not in paths:
        fr = _intraday_from_yahoo(pick, d1)
        if not fr.empty:
            paths[pick] = fr
    rec = settle_record(rec, d1, daily, path_for=paths.get)
    rec.setdefault("runs", []).append({"kind": "settle", "at": now.isoformat(timespec="seconds"), "source": source})
    save_day(rec, "live")
    return rec


# --- replay from stored daily bars ------------------------------------------------------------------


def replay_snapshot(bars_day: pd.DataFrame) -> pd.DataFrame:
    """A 15:20-like snapshot from a day's bhavcopy row: the closing price stands in for 15:20."""
    s = bars_day.rename(columns={"close": "last"})[["symbol", "open", "high", "low", "last", "prev_close"]].copy()
    s["traded"] = bars_day["close"].values * bars_day["volume"].values
    s["series"] = "EQ"
    s["source"] = "replay"
    return s


def replay(start: date, end: date, rules: R.BtstRules = R.DEFAULT_RULES, progress=None) -> dict:
    """Rebuild every session from ``start`` to ``end`` from the store and settle it on the next one.

    The bhavcopy has no 15:20 price, so the closing price (an average of the last 30 minutes)
    stands in for it, and "stuck at the upper band" is read as a close within 0.3% of the high.
    """
    from dataclasses import replace as _replace

    rules = _replace(rules, locked_near_high=REPLAY_LOCKED_NEAR_HIGH)
    bars = store_bars(start - timedelta(days=HISTORY_DAYS), end + timedelta(days=14))
    stats = R.rolling_stats(bars)
    sessions = sorted(bars["date"].dt.date.unique())
    by_day = {d: g for d, g in bars.groupby(bars["date"].dt.date)}
    # prior stats per day: the stats row of the previous session for each symbol
    stats = stats.sort_values(["symbol", "date"])
    stats["next_date"] = stats.groupby("symbol")["date"].shift(-1)
    filed, skipped = 0, 0
    todo = [d for d in sessions if start <= d <= end]
    for i, d in enumerate(todo):
        k = sessions.index(d)
        if k + 1 >= len(sessions):
            break
        d1 = sessions[k + 1]
        prev = stats[stats["next_date"] == pd.Timestamp(d)].set_index("symbol")
        prior = pd.DataFrame({"turn20": prev["turn20"], "vol20": prev["vol20"], "ret5": prev["ret5"],
                              "ret20": prev["ret20"], "prev_ret": prev["ret"],
                              "hi250_rel": prev["hi250"] / prev["adj_close"], "sessions": prev["n"],
                              "last_date": prev["date"]})
        rec = build(d, replay_snapshot(by_day[d]), prior, rules, "replay",
                    {"sources": ["replay"], "note": "Built from the day's closing prices (they stand in for 15:20)."})
        nxt = by_day.get(d1, pd.DataFrame())
        daily = {r.symbol: {"open": r.open, "high": r.high, "low": r.low, "close": r.close, "prev_close": r.prev_close}
                 for r in nxt.itertuples()}
        rec = settle_record(rec, d1, daily, rules=rules, strict=False)
        rec["runs"] = [{"kind": "replay", "at": datetime.now().isoformat(timespec="seconds"), "source": "store"}]
        save_day(rec, "replay")
        filed += 1
        if rec.get("pick") is None:
            skipped += 1
        if progress and i % 20 == 0:
            progress(i + 1, len(todo))
    return {"filed": filed, "skipped_days": skipped, "from": start.isoformat(), "to": end.isoformat()}


# --- statistics --------------------------------------------------------------------------------------


def stats(kind: str = "live", records: list[dict] | None = None) -> dict:
    """Track record of the picks plus how each warning did on the candidates it flagged."""
    recs = records if records is not None else list_days(kind)
    settled = [r for r in recs if r.get("status") == "settled"]
    picks = [(r["date"], r["pick"]["outcome"]) for r in settled
             if r.get("pick") and r["pick"].get("outcome") and not r["pick"]["outcome"].get("data_check")]
    net = np.array([o["net_pct"] for _, o in picks], dtype=float)
    years: dict[str, list[float]] = {}
    years_tf: dict[str, list[float]] = {}
    years_ns: dict[str, list[float]] = {}
    for d, o in picks:
        years.setdefault(d[:4], []).append(o["net_pct"])
        years_tf.setdefault(d[:4], []).append(o.get("alt_net_pct", o["net_pct"]))
    for r in settled:
        ns = (r.get("pick") or {}).get("outcome_no_stop") or {}
        if ns.get("net_pct") is not None:
            years_ns.setdefault(r["date"][:4], []).append(ns["net_pct"])

    def summary(x: np.ndarray, hits: int | None = None) -> dict:
        if x.size == 0:
            return {"trades": 0}
        return {"trades": int(x.size), "hit_rate": None if hits is None else round(hits / x.size, 3),
                "win_rate": round(float((x > 0).mean()), 3), "avg_net_pct": round(float(x.mean()), 3),
                "median_net_pct": round(float(np.median(x)), 3), "big_loss_rate": round(float((x < -5).mean()), 3),
                "worst_pct": round(float(x.min()), 2), "best_pct": round(float(x.max()), 2),
                "total_pct": round(float(x.sum()), 2)}

    hits = sum(1 for _, o in picks if o.get("hit"))
    alt = np.array([o.get("alt_net_pct", o["net_pct"]) for _, o in picks], dtype=float)
    out = {"kind": kind, "days": len(recs), "settled_days": len(settled),
           "skipped_days": sum(1 for r in settled if not r.get("pick")),
           "picks": summary(net, hits),
           "ambiguous": sum(1 for _, o in picks if o.get("ambiguous")),
           # days where the daily bar touched both levels: the same trades if the take-profit came first
           "picks_target_first": summary(alt, hits + sum(1 for _, o in picks if o.get("ambiguous"))),
           "exact": sum(1 for _, o in picks if o.get("method") == "intraday"),
           "picks_no_stop": summary(np.array([r["pick"]["outcome_no_stop"]["net_pct"] for r in settled
                                              if r.get("pick") and (r["pick"].get("outcome_no_stop") or {}).get("net_pct") is not None]),
                                    sum(1 for r in settled if r.get("pick") and (r["pick"].get("outcome_no_stop") or {}).get("hit"))),
           "by_year": [{"year": y, **summary(np.array(v)),
                        "avg_target_first_pct": round(float(np.mean(years_tf[y])), 3),
                        "avg_no_stop_pct": round(float(np.mean(years_ns[y])), 3) if years_ns.get(y) else None}
                       for y, v in sorted(years.items())],
           "first": recs[-1]["date"] if recs else None, "last": recs[0]["date"] if recs else None}
    # candidates: by warning count and per warning, judged without the stop (the stop hides the
    # very losses a warning is meant to predict)
    rows = []
    for r in settled:
        for c in r.get("candidates") or []:
            o = c.get("outcome_no_stop") or c.get("outcome")
            if o and not o.get("data_check"):
                rows.append((len(c["warnings"]), {w["key"] for w in c["warnings"]}, o["net_pct"]))
    by_count: dict[int, list[float]] = {}
    for n, _, v in rows:
        by_count.setdefault(min(n, 4), []).append(v)
    out["candidates"] = {"settled": len(rows),
                         "by_warning_count": [{"warnings": k, "label": f"{k}+" if k == 4 else str(k), **summary(np.array(v))}
                                              for k, v in sorted(by_count.items())]}
    per = []
    for key, (label, *_rest) in R.WARNINGS.items():
        f = np.array([v for _, ks, v in rows if key in ks])
        cl = np.array([v for _, ks, v in rows if key not in ks])
        per.append({"key": key, "label": label, "flagged": summary(f), "clean": summary(cl)})
    out["candidates"]["by_warning"] = per
    return out


def guard(job: str):
    """Context manager: one job of each name at a time."""
    class _G:
        def __enter__(self):
            with _LOCK:
                if job in RUNNING:
                    raise RuntimeError(f"{job} is already running")
                RUNNING[job] = job
        def __exit__(self, *a):
            with _LOCK:
                RUNNING.pop(job, None)
    return _G()
