"""``python -m src.alphadesk.btst <command>``

    scan                 live scan now (prices from NSE/Yahoo, history from the NSE store)
    settle [--date D]    settle a live day (default: the previous trading day)
    replay [--years N]   rebuild past days from the NSE store (filed apart from live days)
    stats  [--kind K]    track record (live or replay)
    snapshot             fetch live prices only and print coverage (check the data source)
    cron                 print system-cron lines for the two daily jobs
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.alphadesk.btst")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    s = sub.add_parser("settle")
    s.add_argument("--date")
    r = sub.add_parser("replay")
    r.add_argument("--years", type=float, default=3.0)
    r.add_argument("--start")
    r.add_argument("--end")
    st = sub.add_parser("stats")
    st.add_argument("--kind", default="live", choices=["live", "replay"])
    sub.add_parser("snapshot")
    sub.add_parser("cron")
    a = ap.parse_args(argv)

    from src.alphadesk.btst import days, scheduler

    if a.cmd == "scan":
        rec = days.run_scan(source="cli")
        p = rec.get("pick")
        print(f"{rec['date']} {rec['scanned_at']}: " + (f"PICK {p['symbol']} buy ~{p['entry']} target {p['target']} stop {p['stop']}" if p else f"no pick ({rec['skip_reason']})"))
        for c in rec["candidates"]:
            print(f"  {c['rank']:>2} {c['symbol']:<12} {c['gain_pct']:>6.2f}%  {', '.join(w['label'] for w in c['warnings']) or 'clean'}")
        for n in rec.get("notes", []):
            print("  note:", n)
    elif a.cmd == "settle":
        d = date.fromisoformat(a.date) if a.date else scheduler._prev_session(date.today())
        rec = days.run_settle(d, "cli")
        o = (rec.get("pick") or {}).get("outcome")
        print(f"{d}: settled on {rec['settled_on']}" + (f", {rec['pick']['symbol']} {o['reason']} {o['net_pct']:+.2f}% net ({o['method']})" if o else ""))
    elif a.cmd == "replay":
        end = date.fromisoformat(a.end) if a.end else (days.store_last_date() or date.today()) - timedelta(days=1)
        start = date.fromisoformat(a.start) if a.start else end - timedelta(days=int(a.years * 365))
        print(json.dumps(days.replay(start, end, progress=lambda i, n: print(f"  {i}/{n}", flush=True))))
    elif a.cmd == "stats":
        print(json.dumps(days.stats(a.kind), indent=1))
    elif a.cmd == "snapshot":
        from src.alphadesk.btst import snapshot
        from src.alphadesk.btst.rules import DEFAULT_RULES, prior_stats, rolling_stats
        import pandas as pd
        today = date.today()
        prior = prior_stats(rolling_stats(days.store_bars(today - timedelta(days=60), today - timedelta(days=1))), pd.Timestamp(today))
        snap, info = snapshot.take(days.universe_from(prior, DEFAULT_RULES))
        print(json.dumps(info, indent=1))
        print(snap.sort_values("traded", ascending=False).head(10).to_string(index=False))
    elif a.cmd == "cron":
        print(scheduler.cron_lines(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
