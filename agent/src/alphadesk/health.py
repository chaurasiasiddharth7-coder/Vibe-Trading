"""The desk's one kill switch, its live-vs-replay records, the Kronos split and the weekly audit.

Every paper strategy reports here: the signal board, every lab strategy, the intraday breakout and
the overnight (BTST) pick. Each is judged on its **live** record only (trades the desk took going
forward); replays and backfills are kept apart as tests, never mixed into the score.

The rule (audit of 21 Nov 2026, rule 3): after ``MIN_TRADES`` live trades, if the average result is
below zero with 95% confidence (mean + 1.96 * stdev / sqrt(n) < 0), paper trading for that strategy
is switched off, with the reason written on the switch. It stays off until a new past-prices test,
run after the pause, passes the gate. Under ``MIN_TRADES`` nothing is decided: too few trades can't
tell a bad strategy from bad luck (the old survival rule killed the signal board on 30 Oct at
30 trades and -2.6R; it has made +11R since).

Run by the strategy scheduler after the close on trading days, and on demand from
``GET /api/desk/health`` and ``python -m src.alphadesk.health check|weekly``. Paper only: nothing
here places an order.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
MIN_TRADES = 30
Z95 = 1.96
RULE_TEXT = (f"Paused when, after {MIN_TRADES}+ live trades, the average result is below zero with 95% confidence "
             "(a losing live record may lean on replayed days of the same rules to prove it). "
             "Restarts only after a new past-prices test passes the gate.")

# Where an intraday day came from. "scheduler" and "manual" ran on the day; "scheduler-catch-up" was
# filed from stored prices after a missed day (same rules, no hindsight). "cli", "backfill", "test" are
# research replays of past days.
LIVE_SOURCES = {"scheduler", "manual", "scheduler-catch-up"}

# One-off pauses from the 21 Nov 2026 audit, applied once per profile.
AUDIT_KEY = "audit_2026_11_21"
AUDIT_PAUSES = {
    "intraday": "Audit 21 Nov 2026: 365 trades, -84R net (-₹83,754), lost every month. "
                "Kept off until a new version passes the full test.",
    "btst": "Audit 21 Nov 2026: 539 past picks lose 0.34% a trade after costs. "
            "Kept off until a version passes the full test on days it wasn't tuned on.",
}


def _now() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# --- the rule ------------------------------------------------------------------------------------------


def judge(values: Iterable[float], min_trades: int = MIN_TRADES) -> dict[str, Any]:
    """``collecting`` under ``min_trades``; ``pause`` when the 95% upper bound of the mean is below 0; else ``ok``."""
    xs = [x for x in (_f(v) for v in values) if x is not None]
    n = len(xs)
    out: dict[str, Any] = {"n": n, "total": round(sum(xs), 3), "mean": round(statistics.fmean(xs), 4) if xs else None}
    if n < min_trades:
        out.update(status="collecting", reason=f"{n} of {min_trades} live trades so far: too few to judge")
        return out
    sd = statistics.stdev(xs) if n > 1 else 0.0
    upper = statistics.fmean(xs) + Z95 * sd / math.sqrt(n)
    out["upper95"] = round(upper, 4)
    if upper < 0:
        out.update(status="pause", reason=f"{n} live trades, average {out['mean']:+.3f} a trade; "
                                          f"even the best case (95%) is below zero ({upper:+.3f})")
    else:
        out.update(status="ok", reason=f"{n} live trades, average {out['mean']:+.3f}: no proof it loses")
    return out


def first_trip(values: list[float], min_trades: int = MIN_TRADES) -> int | None:
    """The trade number at which the rule would first have paused this sequence (for the audit)."""
    for i in range(min_trades, len(values) + 1):
        if judge(values[:i], min_trades)["status"] == "pause":
            return i
    return None


# --- the records ---------------------------------------------------------------------------------------


def _board(strategy_id: str | None) -> list[dict[str, Any]]:
    from src.alphadesk.board_paper import list_trades

    rows = []
    for t in list_trades():
        if t.get("status") != "CLOSED" or (t.get("strategy_id") or None) != strategy_id:
            continue
        r = _f(t.get("r_multiple"))
        if r is None:
            continue
        entry, qty = _f(t.get("entry")), _f(t.get("qty"))
        rows.append({"date": str(t.get("exit_date") or t.get("closed_at") or "")[:10], "r": r,
                     "pct": (_f(t.get("pnl_inr")) or 0) / (entry * qty) * 100 if entry and qty else None,
                     "pnl_inr": _f(t.get("pnl_inr")) or 0.0, "side": t.get("side"),
                     "source": "hand-closed" if t.get("exit_reason") == "manual" else (t.get("source") or "live"),
                     "kronos": kronos_vote(t), "exit_reason": t.get("exit_reason"),
                     "rule_version": t.get("rule_version")})
    rows.sort(key=lambda x: x["date"])
    return rows


def day_source(rec: dict[str, Any]) -> str:
    """``live`` if the day was opened or settled on the day; ``replay`` if it was only ever replayed."""
    srcs = {str(r.get("source") or "") for r in rec.get("runs") or []}
    return "live" if srcs & LIVE_SOURCES else "replay"


def _intraday() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from src.alphadesk.intraday import days

    live, replay = [], []
    for rec in days._records(newest_first=False):
        if rec.get("status") != "settled":
            continue
        where = day_source(rec)
        for t in rec.get("trades") or []:
            if t.get("status") == "open":
                continue
            r = _f(t.get("r_net"))
            if r is None:
                continue
            entry, qty = _f(t.get("entry")), _f(t.get("qty"))
            row = {"date": rec["date"], "r": r, "pnl_inr": _f(t.get("pnl_inr")) or 0.0, "side": t.get("side"),
                   "pct": (_f(t.get("pnl_inr")) or 0) / (entry * qty) * 100 if entry and qty else None,
                   "source": where}
            (live if where == "live" else replay).append(row)
    return live, replay


def _btst() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from src.alphadesk.btst import days

    def rows(kind: str) -> list[dict[str, Any]]:
        out = []
        for rec in days.list_days(kind):
            o = (rec.get("pick") or {}).get("outcome") or {}
            if rec.get("status") != "settled" or not o or o.get("data_check") or _f(o.get("net_pct")) is None:
                continue
            out.append({"date": rec["date"], "r": None, "pct": _f(o["net_pct"]), "pnl_inr": None, "side": "LONG",
                        "source": "live" if kind == "live" else "replay"})
        return sorted(out, key=lambda x: x["date"])

    return rows("live"), rows("replay")


def records(strategy_id: str) -> dict[str, list[dict[str, Any]]]:
    """``{"live": [...], "replay": [...]}`` for one strategy in the current profile."""
    try:
        if strategy_id == "signal_board":
            return {"live": _board(None), "replay": []}
        if strategy_id.startswith("lab_"):
            return {"live": _board(strategy_id), "replay": []}
        if strategy_id == "intraday":
            live, replay = _intraday()
            return {"live": live, "replay": replay}
        if strategy_id == "btst":
            live, replay = _btst()
            return {"live": live, "replay": replay}
    except Exception:  # noqa: BLE001 - a broken store must not take the page down
        logger.warning("health: records for %s unreadable", strategy_id, exc_info=True)
    return {"live": [], "replay": []}


def _unit(rows: list[dict[str, Any]]) -> tuple[str, list[float]]:
    """Judge in R where every trade has one (risk-sized trades), else in % of the position."""
    if rows and all(r.get("r") is not None for r in rows):
        return "R", [r["r"] for r in rows]
    return "%", [r["pct"] for r in rows if r.get("pct") is not None]


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unit, xs = _unit(rows)
    wins = sum(1 for x in xs if x > 0)
    out = {"trades": len(xs), "unit": unit, "total": round(sum(xs), 2) if xs else 0.0,
           "avg": round(statistics.fmean(xs), 3) if xs else None, "win_rate": round(wins / len(xs), 3) if xs else None,
           "pnl_inr": round(sum(r.get("pnl_inr") or 0 for r in rows), 2) if any(r.get("pnl_inr") is not None for r in rows) else None,
           "first": rows[0]["date"] if rows else None, "last": rows[-1]["date"] if rows else None}
    return out


def strategy_health(strategy_id: str) -> dict[str, Any]:
    recs = records(strategy_id)
    unit, xs = _unit(recs["live"])
    verdict = {**judge(xs), "unit": unit, "basis": "live"}
    if verdict["status"] != "pause" and xs and statistics.fmean(xs) < 0 and recs["replay"]:
        # Live is losing but too short to prove it alone: replayed days ran the same rules with no
        # hindsight, so together they are fair evidence (intraday: 80 live + 285 replayed trades).
        u2, both = _unit(recs["replay"] + recs["live"])
        comb = judge(both)
        if comb["status"] == "pause":
            verdict = {**comb, "unit": u2, "basis": "live+replay",
                       "reason": f"losing live ({verdict['mean']:+.3f} a trade on {verdict['n']}) and, with the "
                                 f"replayed days, {comb['n']} trades prove it: best case (95%) {comb['upper95']:+.3f}"}
    return {"id": strategy_id, "live": summary(recs["live"]), "replay": summary(recs["replay"]),
            "verdict": verdict, "rule": RULE_TEXT}


# --- the Kronos split ----------------------------------------------------------------------------------


def kronos_vote(trade: dict[str, Any]) -> str:
    """``agrees`` / ``against`` / ``neutral`` / ``unknown``: the forecast vote relative to the trade's side."""
    v = (trade.get("votes") or {}).get("forecast")
    if v is None:
        return "unknown"
    sign = 1 if trade.get("side") == "LONG" else -1
    v = float(v) * sign
    return "agrees" if v > 0 else ("against" if v < 0 else "neutral")


def kronos_split(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = records("signal_board")["live"] if rows is None else rows
    """Results by the Kronos vote (relative to the trade's side), overall and for longs and shorts apart.
    The board needs 3 of 4 votes, so Kronos is rarely "against"; the split that matters is agrees vs neutral."""
    out = []
    for key, label in (("agrees", "Kronos agrees"), ("neutral", "Kronos neutral"), ("against", "Kronos against"),
                       ("unknown", "No forecast")):
        sub = [r for r in rows if r.get("kronos") == key]
        if sub or key not in ("unknown", "against"):
            out.append({"vote": key, "label": label, **summary(sub),
                        "by_side": {side: summary([r for r in sub if r.get("side") == side]) for side in ("LONG", "SHORT")}})
    return out


# --- the switch ----------------------------------------------------------------------------------------


def apply_audit_pauses() -> list[str]:
    """Once per profile: switch off the strategies the 21 Nov audit found losing, and move the signal
    board's open practice trades to fixed exits (the 5-year replay's best: +392R fixed vs +91R trailing)."""
    from src.alphadesk import strategies as S

    done: list[str] = []
    with S._LOCK:
        data = S._load()
        if data.get(AUDIT_KEY):
            return done
        for sid, why in AUDIT_PAUSES.items():
            sw = data["switches"].setdefault(sid, {})
            sw.update(paper=False, changed_at=_now(), auto_paused={"at": _now(), "reason": why, "by": "audit"})
            done.append(f"paused {sid}")
        data[AUDIT_KEY] = _now()
        S._save(data)
    try:
        from src.alphadesk.survival import split_replays_from_outcomes

        done += split_replays_from_outcomes()
    except Exception:  # noqa: BLE001
        logger.warning("health: could not split replay outcomes", exc_info=True)
    try:
        from src.alphadesk.board_paper import force_fixed_exits

        moved = force_fixed_exits()
        if moved:
            done.append(f"{moved} open signal-board trades moved to fixed exits")
    except Exception:  # noqa: BLE001
        logger.warning("health: could not move open trades to fixed exits", exc_info=True)
    return done


def check() -> list[dict[str, Any]]:
    """Judge every strategy whose paper switch is on in the current profile; pause the ones the rule catches."""
    from src.alphadesk import strategies as S

    apply_audit_pauses()
    data = S._load()
    ids = ["signal_board", "intraday", "btst"] + [c["id"] for c in data["custom"] if not c.get("archived")]
    report = []
    for sid in ids:
        h = strategy_health(sid)
        on = S.paper_on(sid)
        action = None
        if on and h["verdict"]["status"] == "pause":
            with S._LOCK:
                d = S._load()
                sw = d["switches"].setdefault(sid, {})
                sw.update(paper=False, changed_at=_now(),
                          auto_paused={"at": _now(), "reason": h["verdict"]["reason"], "by": "kill switch"})
                S._save(d)
            action = "paused"
            logger.warning("kill switch paused %s: %s", sid, h["verdict"]["reason"])
        report.append({"id": sid, "paper": on and action is None, "action": action,
                       "auto_paused": (S._load()["switches"].get(sid) or {}).get("auto_paused"), **h})
    return report


def check_all_profiles() -> dict[str, list[dict[str, Any]]]:
    from src.alphadesk import profiles

    out = {}
    for pid in profiles.all_ids():
        try:
            with profiles.use(pid):
                out[pid or "owner"] = check()
        except Exception:  # noqa: BLE001 - one profile's failure must not stop the others
            logger.exception("health check failed for %s", pid or "owner")
    return out


# --- the weekly audit ----------------------------------------------------------------------------------


def _week(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    return [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]


def weekly(today: date | None = None) -> dict[str, Any]:
    """The Saturday audit for the current profile: last week per strategy, per side, Kronos split, and red flags."""
    today = today or datetime.now(IST).date()
    end = today - timedelta(days=(today.weekday() - 4) % 7)  # last Friday (today if Friday)
    start = end - timedelta(days=4)
    flags, rows_out = [], []
    for h in check():
        recs = records(h["id"])
        live = recs["live"]
        wk = _week(live, start, end)
        weeks_lost = 0
        for back in range(3):
            s, e = start - timedelta(days=7 * back), end - timedelta(days=7 * back)
            w = summary(_week(live, s, e))
            if w["trades"] and (w["total"] or 0) < 0:
                weeks_lost += 1
            else:
                break
        sides = {side: summary([r for r in live if r.get("side") == side]) for side in ("LONG", "SHORT")}
        row = {"id": h["id"], "paper": h["paper"], "week": summary(wk), "all_live": h["live"],
               "replay": h["replay"], "verdict": h["verdict"], "sides": sides, "weeks_lost_in_a_row": weeks_lost,
               "auto_paused": h.get("auto_paused")}
        if weeks_lost >= 3:
            flags.append(f"{h['id']}: lost three weeks running")
        if h["action"] == "paused":
            flags.append(f"{h['id']}: paused by the kill switch ({h['verdict']['reason']})")
        for side, s in sides.items():
            if s["trades"] >= MIN_TRADES and (s["total"] or 0) < 0:
                flags.append(f"{h['id']}: {side.lower()} trades losing overall ({s['total']:+}{s['unit']} on {s['trades']})")
        rows_out.append(row)
    rep = {"week": [start.isoformat(), end.isoformat()], "made_at": _now(), "strategies": rows_out,
           "kronos": kronos_split(), "flags": flags, "rule": RULE_TEXT}
    try:
        from src.alphadesk.store import alphadesk_root

        d = alphadesk_root() / "reports" / "weekly"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{end.isoformat()}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("weekly audit not saved", exc_info=True)
    return rep


def last_weekly() -> dict[str, Any] | None:
    from src.alphadesk.store import alphadesk_root

    d = alphadesk_root() / "reports" / "weekly"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
    except (OSError, ValueError):
        return None


def main(argv: list[str]) -> int:
    from src.alphadesk import profiles

    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        print(json.dumps({k: [{x: r[x] for x in ("id", "paper", "action", "verdict")} for r in v]
                          for k, v in check_all_profiles().items()}, indent=2, default=str))
    elif cmd == "weekly":
        out = {}
        for pid in profiles.all_ids():
            with profiles.use(pid):
                out[pid or "owner"] = weekly()["flags"]
        print(json.dumps(out, indent=2))
    else:
        print("usage: python -m src.alphadesk.health [check|weekly]")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
