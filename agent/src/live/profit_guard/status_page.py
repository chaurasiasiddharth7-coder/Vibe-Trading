"""The desk at a glance: one self-refreshing page on the Mac, rewritten by the guard daemon every few minutes.

    cd agent && ../.venv/bin/python -m src.live.profit_guard.status_page --out ../logs/desk_status.html
    cd agent && ../.venv/bin/python -m src.live.profit_guard.status_page --summary     # one line for a notification

Reads only: the Kite token file (fresh or not, never the token), the guard store (read-only, never its lock),
today's daemon log and today's self-test result. Paper trading only; nothing here can place an order.
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.live.profit_guard import session

ROOT = Path(__file__).resolve().parents[4]  # repo root (agent/src/live/profit_guard -> repo)
PR_URL = "https://github.com/chaurasiasiddharth7-coder/Vibe-Trading/pull/1"
REFRESH_S = 60
LABEL = {"btst": "overnight", "signal_board": "signal board", "lab": "Strategies lab", "swing": "swing",
         "manual": "manual"}


def _d(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def kite() -> dict[str, Any]:
    try:
        from src.live.profit_guard.feeds import kite_auth

        st = kite_auth.status(int(time.time() * 1000))
        return {"fresh": bool(st.get("token_fresh")), "until": st.get("expires_at_ist")}
    except Exception as exc:  # noqa: BLE001
        return {"fresh": False, "until": None, "error": str(exc)[:80]}


def guard(db: Path | None = None, now_ms: int | None = None) -> dict[str, Any]:
    """Today's paper positions and P&L from the guard store, read-only."""
    from src.live.profit_guard.store import default_paths

    now_ms = now_ms or int(time.time() * 1000)
    db = db or default_paths()[0]
    out: dict[str, Any] = {"open": [], "closed_today": [], "realized_today": "0", "day": None}
    if not Path(db).exists():
        out["note"] = "The guard hasn't run yet."
        return out
    today = session.trade_date(now_ms)
    day_start = session.ist_ms(datetime.fromisoformat(today).date(), datetime.min.time())
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        plans = [json.loads(r["json"]) for r in con.execute("SELECT json FROM plans")]
        row = con.execute("SELECT json FROM portfolio WHERE trade_date=? ORDER BY scope LIMIT 1", (today,)).fetchone()
        con.close()
    except sqlite3.Error as exc:
        out["note"] = f"Guard store unreadable: {exc}"
        return out
    realized = Decimal(0)
    for p in plans:
        if p.get("state") != "EXITED":
            out["open"].append(p)
        elif int(p.get("exit_ms") or 0) >= day_start:
            out["closed_today"].append(p)
            realized += _d(p.get("realized"))
    out["realized_today"] = str(realized)
    out["day"] = json.loads(row["json"]) if row else None
    return out


def daemon_log(day: str | None = None, root: Path = ROOT) -> dict[str, Any]:
    day = day or datetime.now().strftime("%Y-%m-%d")
    p = root / "logs" / f"guard-{day}.log"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []
    events = [ln for ln in lines if not ln.startswith("{")]
    state = "not started"
    for ln in events:
        if "guard running" in ln:
            state = "running"
        elif "EXITED UNEXPECTEDLY" in ln:
            state = "crashed, restarting"
        elif "guard stopped" in ln:
            state = "stopped for the day"
        elif "no Kite login today" in ln:
            state = "skipped (no Kite login)"
        elif "SELF-TEST FAILED" in ln:
            state = "not started: self-test failed"
        elif "guard_daemon start" in ln and state == "not started":
            state = "waiting for Kite login"
    return {"state": state, "events": events[-12:], "crashes": sum("EXITED UNEXPECTEDLY" in ln for ln in events)}


def selftest(day: str | None = None, root: Path = ROOT) -> dict[str, Any]:
    day = day or datetime.now().strftime("%Y-%m-%d")
    p = root / "logs" / f"selftest-{day}.txt"
    if not p.exists():
        return {"ran": False}
    text = p.read_text(encoding="utf-8", errors="replace")
    last = [ln for ln in text.splitlines() if "passed" in ln or "failed" in ln or "error" in ln]
    return {"ran": True, "ok": "failed" not in text and "error" not in (last[-1] if last else "error"),
            "line": last[-1].strip() if last else "no result"}


def collect(now_ms: int | None = None, root: Path = ROOT, db: Path | None = None) -> dict[str, Any]:
    return {"at": datetime.now().strftime("%d %b %H:%M"), "kite": kite(), "guard": guard(db, now_ms),
            "daemon": daemon_log(root=root), "selftest": selftest(root=root)}


def summary(s: dict[str, Any]) -> str:
    g = s["guard"]
    pnl = _d(g["realized_today"])
    return (f"Paper guard {s['daemon']['state']} · {len(g['closed_today'])} closed today "
            f"({'+' if pnl >= 0 else '−'}₹{abs(pnl):,.0f}) · {len(g['open'])} open"
            + (f" · {s['daemon']['crashes']} crash(es)" if s["daemon"]["crashes"] else ""))


def _e(v: Any) -> str:
    return html.escape(str(v))


def render(s: dict[str, Any]) -> str:
    g, k, dmn, st = s["guard"], s["kite"], s["daemon"], s["selftest"]
    pnl = _d(g["realized_today"])
    day = g.get("day") or {}
    good, bad, warn = "#047857", "#be123c", "#b45309"
    guard_tone = good if dmn["state"] == "running" else bad if ("crash" in dmn["state"] or "failed" in dmn["state"]) else warn
    tiles = [
        ("Kite login", "Fresh" + (f" until {k['until']}" if k.get("until") else "") if k["fresh"] else "Needed: run kite_login",
         good if k["fresh"] else bad),
        ("Paper guard", dmn["state"].capitalize(), guard_tone),
        ("Today's paper P&L (closed)", f"{'+' if pnl >= 0 else '−'}₹{abs(pnl):,.0f}", good if pnl >= 0 else bad),
        ("Open paper positions", str(len(g["open"])), "#111827"),
    ]
    tile_html = "".join(
        f"<div class=t><div class=l>{_e(a)}</div><div class=v style='color:{c}'>{_e(b)}</div></div>" for a, b, c in tiles)

    def rows(ps: list[dict[str, Any]], closed: bool) -> str:
        if not ps:
            return f"<tr><td colspan=6 class=m>{'No trades closed today' if closed else 'Nothing open'}</td></tr>"
        out = []
        for p in ps:
            r = _d(p.get("realized"))
            out.append("<tr>" + "".join(f"<td>{x}</td>" for x in (
                f"{_e(p.get('symbol'))} <span class=m>{_e(LABEL.get(p.get('strategy'), p.get('strategy') or ''))}</span>",
                _e(p.get("side")), _e(p.get("qty_open") or p.get("qty")), _e(p.get("entry")),
                _e(p.get("exit_price") if closed else p.get("stop")),
                f"<span style='color:{good if r >= 0 else bad}'>{'+' if r >= 0 else '−'}₹{abs(r):,.0f}</span>" if closed
                else _e(p.get("target") or "—"))) + "</tr>")
        return "".join(out)

    lock = ""
    if day:
        lock = (f"<p class=m>Day lock: peak ₹{_d(day.get('peak_mtm')):,.0f}"
                f" · floor {('₹' + format(_d(day['floor']), ',.0f')) if day.get('floor') else 'not armed'}"
                f"{' · LOSS CAP HIT' if day.get('loss_cap_hit') else ''}{' · PROFIT LOCKED' if day.get('lock_hit') else ''}</p>")
    test = ("<span style='color:%s'>%s</span>" % (good if st["ok"] else bad, _e(st["line"]))) if st.get("ran") else "not run yet today"
    events = "".join(f"<li>{_e(e)}</li>" for e in dmn["events"]) or "<li class=m>Nothing yet today</li>"
    return f"""<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content={REFRESH_S}>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Desk at a glance</title>
<style>body{{font:15px -apple-system,system-ui,sans-serif;margin:0;padding:20px;color:#111827;background:#f9fafb}}
h1{{font-size:20px;margin:0 0 4px}}.m{{color:#6b7280;font-size:13px}}.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:14px 0}}
.t{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px}}.l{{font-size:12px;color:#6b7280}}.v{{font:600 18px ui-monospace,Menlo,monospace;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:8px;margin:6px 0 14px}}
td,th{{padding:7px 10px;border-top:1px solid #f3f4f6;text-align:left;font-size:13px}}th{{background:#f3f4f6;font-weight:600}}
h2{{font-size:15px;margin:14px 0 4px}}ul{{margin:4px 0;padding-left:18px;font:12px ui-monospace,Menlo,monospace;color:#374151}}</style></head><body>
<h1>Desk at a glance</h1><p class=m>Paper trading only · updated {_e(s['at'])} · refreshes every minute</p>
<div class=g>{tile_html}</div>{lock}
<h2>Open paper positions</h2><table><tr><th>Stock</th><th>Side</th><th>Qty</th><th>Entry</th><th>Stop</th><th>Target</th></tr>{rows(g['open'], False)}</table>
<h2>Closed today</h2><table><tr><th>Stock</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Result</th></tr>{rows(g['closed_today'], True)}</table>
<h2>Today</h2><p class=m>Morning self-test: {test}</p><ul>{events}</ul>
<p class=m>Every weekday: 07:40 Kite login opens (press Enter, log in) · 09:00 self-test, then the guard runs to 15:40 · this page stays open and updates itself.
{_e(g.get('note') or '')} <a href="{PR_URL}">Pull request #1</a></p></body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.live.profit_guard.status_page")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args(argv)
    try:
        from src.live.profit_guard.feeds import kite_auth

        kite_auth._load_env()
    except Exception:  # noqa: BLE001
        pass
    s = collect()
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.out.with_suffix(".tmp")
        tmp.write_text(render(s), encoding="utf-8")
        tmp.replace(a.out)
    if a.summary or not a.out:
        print(summary(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
