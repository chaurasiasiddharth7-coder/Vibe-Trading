"""Command line for the Indian intraday pack.

    python -m src.alphadesk.intraday daily --source nifty200        # pre-market scan -> watchlist
    python -m src.alphadesk.intraday orb --at 09:45                 # opening-range breakouts/breakdowns
    python -m src.alphadesk.intraday entry RELIANCE --side long     # has it triggered?
    python -m src.alphadesk.intraday plan --stop or-mid --target 2R --write
    python -m src.alphadesk.intraday replay --write                 # 10,000 replays + real history
    python -m src.alphadesk.intraday train --source nifty50 --refresh --record
    python -m src.alphadesk.intraday day replay --date 2026-10-22   # research a past day
    python -m src.alphadesk.intraday day open|close                 # what the two daily jobs run
    python -m src.alphadesk.intraday day backfill ...  ->  python -m src.alphadesk.intraday backfill --days 60
    python -m src.alphadesk.intraday days                           # every filed day
    python -m src.alphadesk.intraday cron                           # the two crontab lines
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday import calendar as cal
from src.alphadesk.intraday import calibrate as cb
from src.alphadesk.intraday import scanner, timemachine
from src.alphadesk.intraday.rules import (
    BAR, DEFAULT_FLAT_BY, OPEN_MIN, atr_at, clock, day_from_frame, hhmm, make_plan, orb_trigger,
)

LOW_DATA = "Yahoo keeps only 60 days of 5-minute bars; run `train --refresh` weekly to grow the store."


def _dir():
    return ibars.intraday_dir()


def _now(args) -> datetime:
    return datetime.fromisoformat(args.now) if getattr(args, "now", None) else ibars.now_ist()


def _symbols(args) -> list[str]:
    if getattr(args, "symbols", None):
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if getattr(args, "watchlist", None):
        data = json.loads(open(args.watchlist).read())
        return sorted({h["symbol"] for h in data.get("hits", [])})
    return scanner.universe(args.source, limit=args.limit)


def cmd_daily(args) -> int:
    syms = _symbols(args)
    res = scanner.scan_daily(syms)
    path = scanner.save_watchlist(res)
    print(f"Daily scan ({res['source']}): {res['fetched']} of {res['universe']} charts fetched, "
          f"{res['liquid']} liquid (>= Rs 10 cr/day, >= Rs 50), {len(res['hits'])} with a setup. -> {path}")
    for h in res["hits"][: args.show]:
        print(f"  {h['symbol']:<12} {h['price']:>9.2f}  Rs{h['traded_value_cr']:>7.1f} cr  "
              f"vol {h['volume_x']:.1f}x  {', '.join(h['setups'])}")
    print("A scan lists charts that match a rule. It is not a list of stocks to buy.")
    return 0 if res["hits"] else 1


def cmd_orb(args) -> int:
    now = _now(args)
    state = cal.market_state(now)
    if state != "open" and not args.now:
        print(f"Market is {state}. Use --now 'YYYY-MM-DD HH:MM' (IST) to replay a session.")
        return 1
    syms = _symbols(args)
    res = scanner.scan_orb(syms, args.or_minutes, args.side, now, args.min_vol_x)
    path = scanner.save_watchlist(res)
    print(f"ORB scan at {now:%Y-%m-%d %H:%M} IST: {res['usable']} usable of {res['universe']} charts "
          f"({res['failed']} failed or incomplete), {len(res['hits'])} triggered. -> {path}")
    for h in res["hits"][: args.show]:
        print(f"  {h['symbol']:<12} {h['side']:<5} {h['time']}  {h['price']:>9.2f} (level {h['level']:.2f}, "
              f"vol {h['volume_x']:.1f}x)")
    return 0 if res["hits"] else 1


def _load_day(args, symbol: str):
    now = _now(args)
    day = date.fromisoformat(args.date) if getattr(args, "date", None) else now.date()
    range_ = "60d" if day != ibars.now_ist().date() else "1d"
    ticker, frame = ibars.fetch(symbol, range_=range_, offline=getattr(args, "offline", None))
    frame = frame[frame.index.date == day] if not frame.empty else frame
    return ticker, day, (day_from_frame(day, frame) if not frame.empty else None), now


def cmd_entry(args) -> int:
    ticker, day, d, now = _load_day(args, args.symbol)
    if d is None:
        print(f"No usable 5-minute bars for {args.symbol} on {day} (holiday, halt or Yahoo gap).")
        return 1
    past = day != ibars.now_ist().date()
    now_min = None if past and not args.now else now.hour * 60 + now.minute
    if past and args.now:
        now_min = now.hour * 60 + now.minute
    trig = orb_trigger(d, args.side, args.or_minutes, args.level, args.min_vol_x, now_min=now_min)
    head = f"{args.symbol} {day:%a %Y-%m-%d} {args.side} opening-range ({args.or_minutes}-min) on 5-minute bars (Yahoo, free, can lag)"
    print(head)
    st = trig["status"]
    if st == "triggered":
        rng = trig["range"]
        print(f"TRIGGERED {clock(trig['time'])} at Rs {trig['price']:.2f}: bar {trig['idx'] + 1} closed "
              f"{'above' if args.side == 'long' else 'below'} the level {trig['level']:.2f} "
              f"(volume {trig['vol_x']}x the range). Nothing was placed.")
        out = {"symbol": args.symbol, "date": day.isoformat(), "side": args.side, "status": st,
               "entry": trig["price"], "entry_idx": trig["idx"], "entry_time": clock(trig["time"]),
               "level": trig["level"], "or_minutes": args.or_minutes, "min_vol_x": args.min_vol_x,
               "range": rng}
        if args.write:
            (_dir() / "entry.json").write_text(json.dumps(out, indent=2))
            print(f"-> {_dir() / 'entry.json'}")
        print("Next: set the stop and target BEFORE you buy: `plan --write`.")
        return 0
    msgs = {"waiting": f"WAITING last close {trig.get('last_close')} is {trig.get('distance_pct')}% from the level {trig.get('level')}.",
            "expired": "EXPIRED: no close beyond the level before the entry cut-off (14:45 IST).",
            "forming": "The opening range is still forming.", "no_range": "No opening range in these bars."}
    print(msgs.get(st, st))
    return 1


def cmd_plan(args) -> int:
    src = json.loads((_dir() / "entry.json").read_text()) if not args.entry else None
    if src is None:
        if not (args.symbol and args.date and args.side and args.range_high and args.range_low):
            print("Give --symbol --date --side --entry --range-high --range-low, or run `entry --write` first.")
            return 1
        src = {"symbol": args.symbol, "date": args.date, "side": args.side, "entry": args.entry,
               "range": {"high": args.range_high, "low": args.range_low}, "entry_idx": args.entry_idx,
               "or_minutes": args.or_minutes, "min_vol_x": args.min_vol_x}
    atr_v = None
    if args.stop.startswith("atr"):
        _, day, d, _ = _load_day(argparse.Namespace(date=src["date"], now=None, offline=None), src["symbol"])
        if d is None:
            print("The ATR stop needs the session's bars and none were found.")
            return 1
        atr_v = atr_at(d, int(src["entry_idx"]))
    try:
        plan = make_plan(src["side"], float(src["entry"]), src["range"]["high"], src["range"]["low"],
                         args.stop, args.target, atr_v, args.flat_by)
    except ValueError as exc:
        print(f"Plan refused: {exc}")
        return 1
    print(f"Exit plan: {src['symbol']} {plan.side}, {src['date']}, opening-range on 5-minute bars")
    print(f"  Entry        Rs {plan.entry:.2f}" + (f" at {src.get('entry_time')}" if src.get("entry_time") else ""))
    print(f"  Stop         Rs {plan.stop:.2f}  {plan.stop_rule}")
    print(f"  Target       Rs {plan.target:.2f}  {plan.target_rule}")
    print(f"  Risk/share   Rs {plan.risk:.2f} ({plan.risk_pct:.2f}% of the entry)   reward:risk {plan.reward_risk}")
    print(f"  Square off   {plan.flat_by} IST, win or lose: no overnight position")
    print("The stop is a plan, not a guarantee: a gap or a fast market can fill past it.")
    if args.write:
        (_dir() / "plan.json").write_text(json.dumps({**src, **plan.as_dict()}, indent=2))
        print(f"-> {_dir() / 'plan.json'}")
    return 0


def cmd_replay(args) -> int:
    from src.alphadesk.intraday.rules import Plan
    plan_d = json.loads((_dir() / "plan.json").read_text())
    plan = Plan(**{k: plan_d[k] for k in Plan.__dataclass_fields__})
    day = date.fromisoformat(plan_d["date"])
    sym = plan_d["symbol"]
    stored = ibars.load_store(sym)
    _, fresh = ibars.fetch(sym, offline=args.offline)
    import pandas as pd
    merged = pd.concat([stored, fresh]) if not stored.empty else fresh
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    days = ibars.days_from_frame(merged, before=day)
    pool = timemachine.build_pool(days)
    try:
        mc = timemachine.replay(plan, int(plan_d["entry_idx"]), pool, args.replays, args.seed)
    except ValueError as exc:
        print(f"Cannot replay: {exc}. {LOW_DATA}")
        return 1
    hist = timemachine.history(days, plan, plan_d.get("or_minutes", 15), plan_d.get("min_vol_x"))
    p = mc["price_at_flat"]
    print(f"{sym} {plan.side} {day}: entry {plan.entry:.2f} stop {plan.stop:.2f} target {plan.target:.2f} "
          f"({plan.reward_risk}R), flat by {plan.flat_by} IST.")
    print(f"{mc['replays']:,} replays of the rest of the session, seed {mc['seed']}, from {mc['sessions_in_pool']} real sessions.\n")
    print(f"  target first   {mc['target_first'] * 100:5.1f} %\n  stop first     {mc['stop_first'] * 100:5.1f} %\n"
          f"  neither        {mc['neither'] * 100:5.1f} %   still open at {plan.flat_by}\n"
          f"  green          {mc['green'] * 100:5.1f} %\n")
    print(f"  Price at {plan.flat_by}: median {p['median']:.2f}, 90% of replays between {p['p5']:.2f} and {p['p95']:.2f}.")
    print(f"  Average R: {mc['avg_r_gross']:+.3f} gross, {mc['avg_r_net']:+.3f} net of NSE intraday costs.\n")
    print(f"History: the same rule on {hist['sessions']} real sessions fired {hist['fired']} times: "
          f"{hist['target']} target, {hist['stop']} stop, {hist['flat']} flat. Average R "
          f"{hist['avg_r_gross']} gross, {hist['avg_r_net']} net.\n")
    print(timemachine.verdict(mc, plan.reward_risk))
    decided = mc["target_first"] + mc["stop_first"]
    h_decided = hist["target"] + hist["stop"]
    if decided > 0 and h_decided >= 10:
        gap = abs(mc["target_first"] / decided - hist["target"] / h_decided) * 100
        if gap >= 15:
            print(f"The replays and the real history disagree by {gap:.0f} points on how often the target "
                  "comes first. Replays draw bars independently, so they cannot copy trend persistence "
                  "after a breakout; the history can, but it is a small sample. Trust neither alone.")
    print(f"\nA simulation built from this stock's own recent sessions, not a prediction. Same seed, same numbers: --seed {mc['seed']}.")
    if args.write:
        (_dir() / "timemachine.json").write_text(json.dumps({"plan": plan_d, "mc": mc, "history": hist}, indent=2))
    return 0


def cmd_train(args) -> int:
    syms = _symbols(args)
    res = cb.calibrate(syms, refresh=args.refresh)
    trades = res.pop("_trades")
    out = _dir() / "calibration.json"
    out.write_text(json.dumps(res, indent=2))
    print(f"Calibration on {res['symbols']} symbols, {res['sessions']} sessions "
          f"(split {res['split_date']}: {res['in_sample_sessions']} in-sample, {res['out_of_sample_sessions']} out-of-sample).")
    print(f"{res['variants_tried']} variants tried, {res['variants_positive_in_sample']} positive net of costs in-sample. "
          f"Selection bar (what the best of {res['variants_tried']} worthless variants reaches): Sharpe {res['selection_bar']}.")
    for i, r in enumerate(res["top"][:3], 1):
        print(f"  #{i} {json.dumps(r['config'])}\n      in-sample {r['in_sample']['trades']} trades, "
              f"{r['in_sample']['avg_r_net']}R net, Sharpe {r['in_sample']['sharpe']} | out-of-sample "
              f"{r['out_of_sample']['trades']} trades, {r['out_of_sample']['avg_r_net']}R net")
    print("\n" + res["verdict"])
    if res["edge_candidate"]:
        (_dir() / "config.json").write_text(json.dumps(res["best"]["config"], indent=2))
        print(f"-> best config saved to {_dir() / 'config.json'}")
    if args.record:
        res["_trades"] = trades
        print("Paper ledger:", json.dumps(cb.record_oos(res)))
    if res["sessions"] < 120:
        print(LOW_DATA)
    return 0


def cmd_day(args) -> int:
    from src.alphadesk.intraday import days
    d = date.fromisoformat(args.date) if args.date else ibars.now_ist().date()
    run = {"open": days.run_open, "close": days.run_close, "replay": days.run_replay}.get(args.kind)
    try:
        rec = run(d, "cli") if run else days.run_refresh(d, "cli")
    except (ValueError, RuntimeError) as exc:
        print(f"{d}: {exc}")
        return 1
    s = rec["summary"]
    print(f"{rec['date']} {rec['weekday']} [{rec['status']}] {rec['runs'][-1]['note']}")
    if rec.get("day"):
        print(f"  day: {rec['day']['label']}")
    print(f"  taken {s['taken']} (won {s['wins']}, lost {s['losses']}, flat {s['flat']}, open {s['open']})  "
          f"net {s['net_r']:+.2f}R  Rs {s['net_inr']:+,.0f}")
    for t in rec["trades"]:
        print(f"    {t['decided_at']} {t['symbol']:<11}{t['side']:<6}{t['status']:<9}{t['r_net']:+.2f}R  Rs {t['pnl_inr']:+,.0f}")
    return 0


def cmd_backfill(args) -> int:
    from datetime import timedelta
    from src.alphadesk.intraday import days
    end = date.fromisoformat(args.end) if args.end else ibars.now_ist().date() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days)
    res = days.backfill(start, end, "cli", force=args.force)
    print(f"filed {len(res['filed'])} days, kept {len(res['kept'])} already settled, {len(res['failed'])} failed")
    for f in res["failed"][:10]:
        print("  failed:", f["date"], f["error"])
    return 0 if not res["failed"] else 1


def cmd_days(args) -> int:
    from src.alphadesk.intraday import days
    rows = days.list_days(args.limit)
    print(f"{'date':<12}{'type':<16}{'status':<9}{'taken':>6}{'won':>5}{'win%':>7}{'net R':>8}{'net Rs':>11}")
    for r in rows:
        wr = "—" if r["win_rate"] is None else f"{r['win_rate'] * 100:.0f}"
        print(f"{r['date']:<12}{(r['label'] or '—'):<16}{r['status']:<9}{r['taken']:>6}{r['wins']:>5}{wr:>7}{r['net_r']:>8.2f}{r['net_inr']:>11,.0f}")
    return 0


def cmd_cron(args) -> int:
    from src.alphadesk.intraday import scheduler
    print(scheduler.cron_lines())
    print("# crontab -e, paste the lines above. Or run the API server with INTRADAY_SCHEDULER=1.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="alphadesk-intraday", description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--source", default="nifty200", help="index list key or 'store'")
        p.add_argument("--symbols", help="comma-separated NSE symbols")
        p.add_argument("--watchlist", help="a watchlist.json file")
        p.add_argument("--limit", type=int)
        p.add_argument("--show", type=int, default=15)

    p = sub.add_parser("daily"); common(p); p.set_defaults(fn=cmd_daily)
    p = sub.add_parser("orb"); common(p)
    p.add_argument("--or-minutes", type=int, default=15); p.add_argument("--side", default="both")
    p.add_argument("--min-vol-x", type=float); p.add_argument("--now"); p.set_defaults(fn=cmd_orb)

    p = sub.add_parser("entry"); p.add_argument("symbol")
    p.add_argument("--side", choices=["long", "short"], default="long")
    p.add_argument("--or-minutes", type=int, default=15); p.add_argument("--level", type=float)
    p.add_argument("--min-vol-x", type=float); p.add_argument("--now"); p.add_argument("--date")
    p.add_argument("--offline"); p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_entry)

    p = sub.add_parser("plan")
    p.add_argument("--stop", default="or-mid"); p.add_argument("--target", default="2R")
    p.add_argument("--flat-by", default=DEFAULT_FLAT_BY)
    p.add_argument("--symbol"); p.add_argument("--date"); p.add_argument("--side")
    p.add_argument("--entry", type=float); p.add_argument("--entry-idx", type=int, default=3)
    p.add_argument("--range-high", type=float); p.add_argument("--range-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15); p.add_argument("--min-vol-x", type=float)
    p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("replay"); p.add_argument("--replays", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=timemachine.DEFAULT_SEED); p.add_argument("--offline")
    p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("day", help="the day log: open | refresh | close | replay")
    p.add_argument("kind", choices=["open", "refresh", "close", "replay"]); p.add_argument("--date")
    p.set_defaults(fn=cmd_day)
    p = sub.add_parser("backfill", help="file past days by replaying them from stored prices")
    p.add_argument("--days", type=int, default=60); p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--force", action="store_true", help="redo days that are already settled")
    p.set_defaults(fn=cmd_backfill)
    p = sub.add_parser("days", help="list filed days"); p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_days)
    p = sub.add_parser("cron", help="print the two crontab lines"); p.set_defaults(fn=cmd_cron)

    p = sub.add_parser("train"); common(p)
    p.add_argument("--refresh", action="store_true", help="fetch and store the last 60 days first")
    p.add_argument("--record", action="store_true", help="feed out-of-sample trades to the orb_in paper engine")
    p.set_defaults(fn=cmd_train)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
