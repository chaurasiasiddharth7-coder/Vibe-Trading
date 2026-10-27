"""One call from symbol to trade check: entry status, exit plan, replay and history.

The same steps as the CLI's ``entry`` -> ``plan`` -> ``replay``, returned as a dict for the API.
Read-only: it never places an order.
"""

from __future__ import annotations

from datetime import date as date_cls, datetime

import pandas as pd

from src.alphadesk.intraday import bars as ibars
from src.alphadesk.intraday import timemachine
from src.alphadesk.intraday.rules import atr_at, clock, day_from_frame, make_plan, orb_trigger


def trade_check(symbol: str, side: str = "long", date: str | None = None, or_minutes: int = 15,
                stop: str = "or-mid", target: str = "2R", min_vol_x: float | None = None,
                replays: int = 10_000, seed: int = timemachine.DEFAULT_SEED,
                now: datetime | None = None, offline: str | None = None) -> dict:
    now = now or ibars.now_ist()
    ticker, fresh = ibars.fetch(symbol, range_="60d", offline=offline)
    if fresh.empty:
        return {"ok": False, "reason": f"no 5-minute bars for {symbol} from Yahoo"}
    stored = ibars.load_store(symbol)
    merged = pd.concat([stored, fresh]) if not stored.empty else fresh
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    sessions = ibars.split_sessions(merged)
    day = date_cls.fromisoformat(date) if date else max(sessions)
    frame = sessions.get(day)
    d = day_from_frame(day, frame) if frame is not None else None
    if d is None:
        return {"ok": False, "reason": f"no usable bars for {symbol} on {day}"}
    live = day == now.date()
    now_min = now.hour * 60 + now.minute if live else None
    trig = orb_trigger(d, side, or_minutes, min_vol_x=min_vol_x, now_min=now_min)
    out = {"ok": True, "symbol": symbol.upper(), "ticker": ticker, "date": day.isoformat(),
           "side": side, "entry": {k: v for k, v in trig.items()}}
    if trig["status"] != "triggered":
        return out
    out["entry"]["clock"] = clock(trig["time"])
    rng = trig["range"]
    atr_v = atr_at(d, trig["idx"]) if stop.startswith("atr") else None
    try:
        plan = make_plan(side, trig["price"], rng["high"], rng["low"], stop, target, atr_v)
    except ValueError as exc:
        return {**out, "plan_refused": str(exc)}
    out["plan"] = plan.as_dict()
    days = ibars.days_from_frame(merged, before=day)
    try:
        mc = timemachine.replay(plan, trig["idx"], timemachine.build_pool(days), replays, seed)
    except ValueError as exc:
        return {**out, "replay_refused": str(exc)}
    out["replay"] = mc
    out["history"] = timemachine.history(days, plan, or_minutes, min_vol_x)
    out["verdict"] = timemachine.verdict(mc, plan.reward_risk)
    out["note"] = ("Simulated from recent sessions, net of NSE intraday costs. Not a prediction and "
                   "not advice; nothing was placed.")
    return out
