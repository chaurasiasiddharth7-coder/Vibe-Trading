"""Data behind the Kite-style pages: Portfolio (with groups), F&O, Gold/ETF/MF, Suggestions, guard status.

Data rules:

* Kite data (holdings, positions, MF, margins, quotes) is read through the read-only client and only for the
  owner. Showing Kite Connect data to other people breaks exchange data-vending rules, so every other user sees
  only their own paper trades.
* Nothing here can place, change or cancel an order: the client has no such methods.
* Groups are stored per desk profile (``alphadesk_root()/groups.json``), so each user's groups are private.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

IST = timezone(timedelta(hours=5, minutes=30))
ASSET_TYPES = ("stock", "fno", "equity_etf", "debt_etf", "gold", "mf")
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()
CACHE_SECONDS = 20

# --- asset type -------------------------------------------------------------------------------------------

_GOLD = re.compile(r"(GOLD|SILVER|SGB)", re.I)
_DEBT = re.compile(r"(LIQUID|GILT|BBETF|EBBETF|SDL|BOND|LIQ|MONEY|CPSE?BOND|10YR|5YR|GSEC|NETFSDL|LTGILT)", re.I)
_ETF = re.compile(r"(BEES|ETF|IETF|^MON100$|^MAFANG$|^MAHKTECH$|^NIFTY1$|^ICICIB22$|^HNGSNGBEES$)", re.I)


def classify(symbol: str, exchange: str = "NSE") -> str:
    """Which tab a holding belongs to. Each holding lands in exactly one."""
    s = (symbol or "").upper().replace(".NS", "").replace(".BO", "")
    ex = (exchange or "").upper()
    if ex in {"NFO", "BFO", "MCX", "CDS"} or re.search(r"\d{2}[A-Z]{3}(\d+)?(CE|PE|FUT)$", s):
        return "fno"
    if ex == "MF" or re.fullmatch(r"INF[0-9A-Z]{9}", s):
        return "mf"
    if s.startswith("SGB") or (_GOLD.search(s) and (_ETF.search(s) or s.startswith("SGB"))):
        return "gold"
    try:
        from src.alphadesk.universes import asset_class_for

        known = asset_class_for(s)
        if known == "gold":
            return "gold"
        if known == "liquid":
            return "debt_etf"
        if known in {"index_etf", "sector_etf"}:
            return "equity_etf"
    except Exception:  # pragma: no cover - universes optional
        pass
    if _ETF.search(s):
        return "debt_etf" if _DEBT.search(s) else "equity_etf"
    return "stock"


# --- who may see Kite data ------------------------------------------------------------------------------


def is_owner() -> bool:
    from src.alphadesk import profiles, users

    user = users.current_user()
    if user is not None:
        return user.role == "owner" and not user.must_change
    return profiles.current_id() is None


def _cached(key: str, fn: Callable[[], Any], seconds: int = CACHE_SECONDS) -> Any:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now, val)
    return val


def kite_state() -> dict[str, Any]:
    """Is a fresh Kite login available on this server? Never reveals the token."""
    try:
        from src.live.profit_guard.feeds import kite_auth

        kite_auth._load_env()
        st = kite_auth.status(int(time.time() * 1000))
        return {"connected": bool(st.get("token_fresh") and st.get("api_key_set")),
                "expires_at_ist": st.get("expires_at_ist"), "api_key_set": st.get("api_key_set")}
    except Exception as exc:  # pragma: no cover
        return {"connected": False, "error": str(exc)[:120]}


def kite_client() -> Any | None:
    """The read-only Kite client for the owner, or ``None`` (not the owner, no fresh login, no key)."""
    if not is_owner():
        return None
    if not kite_state().get("connected"):
        return None
    from src.live.profit_guard.feeds import kite_auth
    from src.live.profit_guard.feeds.kite_client import ReadOnlyKite

    tok = kite_auth.load_token() or {}
    return ReadOnlyKite(access_token=tok.get("access_token"))


def _kite_call(name: str, *args: Any) -> Any:
    kite = kite_client()
    if kite is None:
        return None
    return _cached(f"kite:{name}:{args}", lambda: getattr(kite, name)(*args))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --- holdings (one shape for every page) -----------------------------------------------------------------


def _row(symbol: str, exchange: str, qty: float, avg: float, ltp: float, *, source: str,
         day_change_pct: float | None = None, close: float | None = None, name: str | None = None,
         side: str = "LONG", extra: dict[str, Any] | None = None, row_id: str | None = None) -> dict[str, Any]:
    value = qty * ltp
    pnl = (ltp - avg) * qty if side == "LONG" else (avg - ltp) * qty
    day_change = None
    if close:
        day_change = (ltp - close) * qty
    elif day_change_pct is not None and ltp:
        day_change = value - value / (1 + day_change_pct / 100) if day_change_pct > -100 else None
    return {
        "id": row_id or f"{source}:{exchange}:{symbol}", "symbol": symbol, "exchange": exchange, "name": name or symbol,
        "asset_type": classify(symbol, exchange), "source": source, "side": side,
        "qty": qty, "avg": round(avg, 4), "ltp": round(ltp, 4), "value": round(value, 2),
        "invested": round(qty * avg, 2), "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / (qty * avg) * 100, 2) if qty and avg else None,
        "day_change": round(day_change, 2) if day_change is not None else None,
        "day_change_pct": round(day_change_pct, 2) if day_change_pct is not None else None,
        **(extra or {}),
    }


def kite_holdings() -> list[dict[str, Any]]:
    raw = _kite_call("holdings") or []
    out = []
    for h in raw:
        qty = _f(h.get("quantity")) + _f(h.get("t1_quantity"))
        if qty <= 0:
            continue
        out.append(_row(str(h.get("tradingsymbol")), str(h.get("exchange") or "NSE"), qty, _f(h.get("average_price")),
                        _f(h.get("last_price")), source="kite", day_change_pct=_f(h.get("day_change_percentage"), 0.0),
                        close=_f(h.get("close_price")) or None))
    return out


def kite_mf() -> list[dict[str, Any]]:
    raw = _kite_call("mf_holdings") or []
    out = []
    for h in raw:
        qty = _f(h.get("quantity"))
        if qty <= 0:
            continue
        out.append(_row(str(h.get("tradingsymbol")), "MF", qty, _f(h.get("average_price")), _f(h.get("last_price")),
                        source="kite", name=h.get("fund"), extra={"folio": h.get("folio"),
                                                                  "nav_date": h.get("last_price_date")}))
    return out


def paper_holdings() -> list[dict[str, Any]] | None:
    """This user's open practice trades, valued at the latest price the desk has. ``None`` if unreadable."""
    try:
        from src.alphadesk.board_paper import list_trades, with_live_prices

        trades = [t for t in list_trades() if t.get("status") == "OPEN"]
        try:
            trades = with_live_prices(trades)
        except Exception:
            pass
    except Exception:
        return None
    out = []
    for t in trades:
        ltp = _f(t.get("current_price") or t.get("entry"))
        out.append(_row(str(t["symbol"]), "NSE", _f(t.get("qty")), _f(t.get("entry")), ltp, source="paper",
                        name=(t.get("decision") or {}).get("name"), side=str(t.get("side") or "LONG"),
                        extra={"stop": t.get("stop_now") or t.get("stop"), "target": t.get("target"), "trade_id": t.get("id"),
                               "price_as_of": t.get("price_as_of")},
                        row_id=f"paper:{t.get('id') or t['symbol']}"))
    return out


def holdings() -> dict[str, Any]:
    kite = kite_state() if is_owner() else {"connected": False}
    rows: list[dict[str, Any]] = []
    note = None
    read: list[str] = []
    if is_owner() and kite.get("connected"):
        try:
            rows += kite_holdings() + kite_mf()
            read.append("kite")
        except Exception as exc:
            note = f"Kite holdings could not be read: {str(exc)[:120]}"
    elif is_owner():
        note = "Kite isn't logged in on this server, so only practice trades show. Log in to Kite to see real holdings."
    else:
        note = "You see your own practice trades. Real broker holdings are only shown to the owner."
    paper = paper_holdings()
    if paper is not None:
        rows += paper
        read.append("paper")
    return {"rows": rows, "kite": kite, "note": note, "read": read,
            "as_of": datetime.now(IST).isoformat(timespec="seconds")}


# --- portfolio groups --------------------------------------------------------------------------------------

_GROUP_LOCK = threading.Lock()
_COLOURS = ("emerald", "sky", "violet", "amber", "rose", "slate", "teal", "indigo")


class GroupError(ValueError):
    pass


def _groups_path() -> Path:
    from src.alphadesk.store import alphadesk_root

    return alphadesk_root() / "groups.json"


def _load_groups() -> list[dict[str, Any]]:
    try:
        data = json.loads(_groups_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_groups(groups: list[dict[str, Any]]) -> None:
    path = _groups_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(groups, indent=2), encoding="utf-8")
    tmp.replace(path)


def _clean_group(body: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    g = dict(base or {"id": uuid.uuid4().hex[:12], "allocations": [], "created_at": time.time()})
    if "name" in body or base is None:
        name = str(body.get("name") or "").strip()[:40]
        if not name:
            raise GroupError("Give the group a name.")
        g["name"] = name
    if "colour" in body or base is None:
        c = body.get("colour")
        g["colour"] = c if c in _COLOURS else _COLOURS[0]
    if "goal_amount" in body:
        amt = body.get("goal_amount")
        g["goal_amount"] = round(_f(amt), 2) if amt not in (None, "") and _f(amt) > 0 else None
    if "goal_date" in body:
        d = body.get("goal_date")
        try:
            g["goal_date"] = date.fromisoformat(str(d)).isoformat() if d else None
        except ValueError as exc:
            raise GroupError("Goal date must look like 2030-03-31.") from exc
    if "asset_types" in body or base is None:
        types = body.get("asset_types")
        g["asset_types"] = [t for t in ASSET_TYPES if isinstance(types, list) and t in types] or list(ASSET_TYPES)
    return g


def create_group(body: dict[str, Any]) -> dict[str, Any]:
    with _GROUP_LOCK:
        groups = _load_groups()
        if len(groups) >= 20:
            raise GroupError("You can have up to 20 groups.")
        g = _clean_group(body)
        if any(x["name"].lower() == g["name"].lower() for x in groups):
            raise GroupError("A group with that name exists.")
        groups.append(g)
        _save_groups(groups)
        return g


def update_group(group_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _GROUP_LOCK:
        groups = _load_groups()
        for i, g in enumerate(groups):
            if g["id"] == group_id:
                groups[i] = _clean_group(body, g)
                _save_groups(groups)
                return groups[i]
    raise GroupError("No such group.")


def delete_group(group_id: str) -> None:
    with _GROUP_LOCK:
        groups = _load_groups()
        left = [g for g in groups if g["id"] != group_id]
        if len(left) == len(groups):
            raise GroupError("No such group.")
        _save_groups(left)


def set_allocation(group_id: str, holding_id: str, qty: float, held: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Put ``qty`` of a holding in a group (0 removes it). The total across groups never exceeds what is held."""
    if qty < 0:
        raise GroupError("Quantity can't be negative.")
    row = held.get(holding_id)
    if row is None and qty > 0:
        raise GroupError("You don't hold that.")
    with _GROUP_LOCK:
        groups = _load_groups()
        target = next((g for g in groups if g["id"] == group_id), None)
        if target is None:
            raise GroupError("No such group.")
        if row is not None and qty > 0:
            if row["asset_type"] not in target["asset_types"]:
                raise GroupError(f"This group doesn't hold {row['asset_type'].replace('_', ' ')}.")
            if row.get("side") == "SHORT":
                raise GroupError("Short practice trades can't go in a group.")
            elsewhere = sum(_f(a["qty"]) for g in groups if g["id"] != group_id
                            for a in g["allocations"] if a["holding_id"] == holding_id)
            if qty + elsewhere > _f(row["qty"]) + 1e-9:
                free = max(0.0, _f(row["qty"]) - elsewhere)
                raise GroupError(f"Only {free:g} of {row['symbol']} is free to add (you hold {_f(row['qty']):g}).")
        allocs = [a for a in target["allocations"] if a["holding_id"] != holding_id]
        if qty > 0:
            allocs.append({"holding_id": holding_id, "symbol": row["symbol"] if row else holding_id.split(":")[-1],
                           "qty": qty, "added_at": time.time()})
        target["allocations"] = allocs
        _save_groups(groups)
        return target


def _source_of(holding_id: str) -> str:
    return holding_id.split(":", 1)[0]


def _trim(groups: list[dict[str, Any]], held: dict[str, dict[str, Any]], read: set[str]) -> list[str]:
    """After a sell, shrink the newest allocations first so groups never claim more than is held.

    Only holdings from a source that was read in full this time are trimmed: a Kite logout or a failed read
    must never look like "you sold everything" and wipe the groups."""
    notes = []
    by_holding: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for g in groups:
        for a in g["allocations"]:
            if _source_of(a["holding_id"]) in read:
                by_holding.setdefault(a["holding_id"], []).append((g, a))
    for hid, pairs in by_holding.items():
        have = _f((held.get(hid) or {}).get("qty"))
        total = sum(_f(a["qty"]) for _, a in pairs)
        if total <= have + 1e-9:
            continue
        excess = total - have
        for g, a in sorted(pairs, key=lambda p: p[1].get("added_at", 0), reverse=True):
            if excess <= 0:
                break
            cut = min(_f(a["qty"]), excess)
            a["qty"] = round(_f(a["qty"]) - cut, 6)
            excess -= cut
            notes.append(f"{a['symbol']}: {g['name']} reduced by {cut:g} because you now hold {have:g}.")
        for g, _a in pairs:
            g["allocations"] = [x for x in g["allocations"] if _f(x["qty"]) > 0]
    return notes


def groups_view(rows: list[dict[str, Any]], read: Iterable[str] | None = None) -> dict[str, Any]:
    """Groups with live value, day change, P&L and goal progress; plus what is left ungrouped.

    ``read`` = the sources fully read this time (default: those present in ``rows``)."""
    held = {r["id"]: r for r in rows if r.get("side") != "SHORT"}
    sources = set(read) if read is not None else {r["source"] for r in rows}
    with _GROUP_LOCK:
        groups = _load_groups()
        notes = _trim(groups, held, sources)
        if notes:
            _save_groups(groups)
    out = []
    used: dict[str, float] = {}
    for g in groups:
        value = invested = day = 0.0
        lines = []
        waiting = []
        for a in g["allocations"]:
            r = held.get(a["holding_id"])
            if r is None:
                waiting.append({"holding_id": a["holding_id"], "symbol": a["symbol"], "qty": _f(a["qty"])})
                continue
            share = _f(a["qty"]) / _f(r["qty"]) if _f(r["qty"]) else 0
            used[r["id"]] = used.get(r["id"], 0.0) + _f(a["qty"])
            lines.append({"holding_id": r["id"], "symbol": r["symbol"], "qty": _f(a["qty"]),
                          "value": round(r["value"] * share, 2), "pnl": round(r["pnl"] * share, 2)})
            value += r["value"] * share
            invested += r["invested"] * share
            day += _f(r.get("day_change")) * share
        goal = g.get("goal_amount")
        out.append({**g, "lines": lines, "waiting": waiting, "value": round(value, 2), "invested": round(invested, 2),
                    "pnl": round(value - invested, 2), "day_change": round(day, 2),
                    "goal_progress_pct": round(value / goal * 100, 1) if goal else None})
    ungrouped = []
    for r in held.values():
        free = _f(r["qty"]) - used.get(r["id"], 0.0)
        if free > 1e-9:
            ungrouped.append({"holding_id": r["id"], "symbol": r["symbol"], "qty": round(free, 6),
                              "value": round(r["value"] * free / _f(r["qty"]), 2)})
    total = sum(r["value"] for r in held.values())
    ungrouped_value = sum(u["value"] for u in ungrouped)
    return {"groups": out, "ungrouped": ungrouped, "notes": notes,
            "ungrouped_pct": round(ungrouped_value / total * 100, 1) if total else None, "colours": list(_COLOURS)}


# --- F&O ------------------------------------------------------------------------------------------------

_EXP_RE = re.compile(r"^(?P<und>[A-Z&-]+?)(?P<yy>\d{2})(?P<rest>[A-Z0-9]+?)(?P<kind>CE|PE|FUT)$")


def _guard_plans() -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Open plans and today's day-P&L state from the guard store, read-only (never takes the guard's lock)."""
    import sqlite3

    from src.live.profit_guard.store import default_paths

    db, _ = default_paths()
    if not Path(db).exists():
        return [], None, "The Profit Guard has not run on this server yet."
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        plans = [json.loads(r["json"]) for r in con.execute("SELECT json FROM plans WHERE state<>'EXITED'")]
        today = datetime.now(IST).date().isoformat()
        row = con.execute("SELECT json, updated_ms FROM portfolio WHERE trade_date=? AND scope='account'", (today,)).fetchone()
        port = {**json.loads(row["json"]), "updated_ms": row["updated_ms"]} if row else None
        last = con.execute("SELECT MAX(updated_ms) FROM plans").fetchone()[0]
        con.close()
    except sqlite3.Error as exc:
        return [], None, f"Guard store unreadable: {exc}"
    if port is not None:
        port["last_plan_update_ms"] = last
    return plans, port, None


def guard_status() -> dict[str, Any]:
    if not is_owner():
        return {"available": False, "reason": "The Profit Guard runs on the owner's account."}
    plans, port, err = _guard_plans()
    from src.live.profit_guard.config import load_config

    try:
        capital = _f(load_config().raw.get("capital_inr"))
    except Exception:
        capital = 0.0
    open_rows = [{
        "id": p.get("id"), "strategy": p.get("strategy"), "symbol": p.get("symbol"), "side": p.get("side"),
        "qty": p.get("qty_open") or p.get("qty"), "entry": _f(p.get("entry")), "stop": _f(p.get("stop")),
        "target": _f(p.get("target")) if p.get("target") else None, "state": p.get("state"),
        "realized": _f(p.get("realized")), "carry": bool(p.get("carry")), "is_option": bool(p.get("is_option")),
        "gtt": bool(p.get("gtt_id")), "mode": p.get("mode", "paper"),
    } for p in plans]
    return {"available": err is None, "reason": err, "kite": kite_state(), "open": open_rows,
            "day": port, "capital_inr": capital or None, "paper_only": True}


def _expiry_from_symbol(sym: str, instruments: dict[str, dict[str, Any]]) -> str | None:
    inst = instruments.get(sym)
    if inst and inst.get("expiry"):
        return str(inst["expiry"])[:10]
    return None


def fno_view() -> dict[str, Any]:
    plans, port, err = _guard_plans() if is_owner() else ([], None, None)
    guard_by_symbol = {p.get("symbol"): p for p in plans}
    rows: list[dict[str, Any]] = []
    margin_used = None
    note = None
    kite = kite_state() if is_owner() else {"connected": False}
    if is_owner() and kite.get("connected"):
        try:
            pos = (_kite_call("positions") or {}).get("net") or []
            insts = _nfo_instruments()
            for p in pos:
                ex = str(p.get("exchange") or "")
                if ex not in {"NFO", "BFO"} or not _f(p.get("quantity")):
                    continue
                sym = str(p.get("tradingsymbol"))
                g = guard_by_symbol.get(sym) or {}
                qty = _f(p.get("quantity"))
                rows.append({"symbol": sym, "underlying": (insts.get(sym) or {}).get("name") or _underlying(sym),
                             "qty": qty, "side": "LONG" if qty > 0 else "SHORT", "entry": _f(p.get("average_price")),
                             "ltp": _f(p.get("last_price")), "pnl": _f(p.get("pnl")), "m2m": _f(p.get("m2m")),
                             "expiry": _expiry_from_symbol(sym, insts), "source": "kite",
                             "stop": _f(g.get("stop")) or None, "target": _f(g.get("target")) or None,
                             "state": g.get("state"), "locked": _f(g.get("realized")) or None})
            m = _kite_call("margins") or {}
            eq = m.get("equity") or {}
            margin_used = _f((eq.get("utilised") or {}).get("debits")) or None
        except Exception as exc:
            note = f"Kite positions could not be read: {str(exc)[:120]}"
    elif is_owner():
        note = "Kite isn't logged in on this server: showing paper F&O plans from the guard only."
    else:
        note = "F&O positions come from the owner's broker account and are not shown to other users."
    seen = {r["symbol"] for r in rows}
    for p in plans:
        if not p.get("is_option") or p.get("symbol") in seen:
            continue
        rows.append({"symbol": p.get("symbol"), "underlying": p.get("underlying") or _underlying(str(p.get("symbol"))),
                     "qty": p.get("qty_open") or p.get("qty"), "side": p.get("side"), "entry": _f(p.get("entry")),
                     "ltp": None, "pnl": None, "m2m": None, "expiry": None, "source": "paper",
                     "stop": _f(p.get("stop")), "target": _f(p.get("target")) or None, "state": p.get("state"),
                     "locked": _f(p.get("realized")) or None})
    today = datetime.now(IST).date()
    expiries = sorted(d for d in (r.get("expiry") for r in rows) if d)
    nearest = (date.fromisoformat(expiries[0]) - today).days if expiries else None
    day_mtm = sum(_f(r.get("m2m")) for r in rows if r.get("m2m") is not None) if rows else None
    for r in rows:
        r["one_r"] = abs(_f(r["entry"]) - _f(r["stop"])) if r.get("stop") else None
        if r["one_r"] and r.get("ltp"):
            move = (_f(r["ltp"]) - _f(r["entry"])) * (1 if r["side"] == "LONG" else -1)
            r["r_now"] = round(move / r["one_r"], 2)
        else:
            r["r_now"] = None
    return {"rows": rows, "day_mtm": day_mtm, "floor": (port or {}).get("floor"),
            "peak": (port or {}).get("peak_mtm"), "margin_used": margin_used, "nearest_expiry_days": nearest,
            "expiry_today": nearest == 0, "kite": kite, "note": note or err}


def _underlying(sym: str) -> str:
    m = _EXP_RE.match(sym or "")
    return m.group("und") if m else (sym or "")


def _nfo_instruments() -> dict[str, dict[str, Any]]:
    kite = kite_client()
    if kite is None:
        return {}
    from src.live.profit_guard.feeds.instruments import load_instruments

    def build() -> dict[str, dict[str, Any]]:
        today = datetime.now(IST).date()
        return {r["tradingsymbol"]: r for r in load_instruments(kite, "NFO", today)}

    return _cached("nfo_instruments", build, seconds=3600)


INDEX_QUOTE = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK", "FINNIFTY": "NSE:NIFTY FIN SERVICE",
               "MIDCPNIFTY": "NSE:NIFTY MID SELECT"}


def option_chain(underlying: str, expiry: str | None = None, width: int = 10) -> dict[str, Any]:
    """±``width`` strikes around the money for one expiry: LTP, OI, OI change; PCR and max pain."""
    und = (underlying or "").upper().strip()
    if not re.fullmatch(r"[A-Z&-]{2,20}", und):
        raise ValueError("Pick an underlying like NIFTY or RELIANCE.")
    kite = kite_client()
    if kite is None:
        return {"available": False, "reason": "The option chain needs the owner's Kite login on this server."}
    insts = [r for r in _nfo_instruments().values() if r.get("name") == und and r.get("instrument_type") in {"CE", "PE"}]
    if not insts:
        return {"available": False, "reason": f"No options found for {und}."}
    expiries = sorted({str(r["expiry"])[:10] for r in insts})
    exp = expiry if expiry in expiries else expiries[0]
    legs = [r for r in insts if str(r["expiry"])[:10] == exp]
    spot_key = INDEX_QUOTE.get(und, f"NSE:{und}")
    spot = _f(((_cached(f"ltp:{spot_key}", lambda: kite.ltp([spot_key])) or {}).get(spot_key) or {}).get("last_price"))
    strikes = sorted({_f(r["strike"]) for r in legs})
    if not strikes:
        return {"available": False, "reason": "No strikes."}
    atm = min(strikes, key=lambda s: abs(s - spot)) if spot else strikes[len(strikes) // 2]
    i = strikes.index(atm)
    window = strikes[max(0, i - width): i + width + 1]
    wanted = [r for r in legs if _f(r["strike"]) in window]
    keys = [f"NFO:{r['tradingsymbol']}" for r in wanted]
    quotes = _cached(f"quote:{exp}:{und}", lambda: kite.quote(keys), seconds=10) or {}
    by_strike: dict[float, dict[str, Any]] = {s: {"strike": s} for s in window}
    for r in wanted:
        q = quotes.get(f"NFO:{r['tradingsymbol']}") or {}
        side = "ce" if r["instrument_type"] == "CE" else "pe"
        oi = _f(q.get("oi"))
        by_strike[_f(r["strike"])][side] = {
            "symbol": r["tradingsymbol"], "ltp": _f(q.get("last_price")), "oi": oi,
            "oi_change": oi - _f(q.get("oi_day_low", oi)) if q.get("oi_day_low") is not None else None,
            "volume": _f(q.get("volume")), "lot": int(_f(r.get("lot_size"))),
        }
    rows = [by_strike[s] for s in window]
    ce_oi = sum(_f((r.get("ce") or {}).get("oi")) for r in rows)
    pe_oi = sum(_f((r.get("pe") or {}).get("oi")) for r in rows)
    return {"available": True, "underlying": und, "expiry": exp, "expiries": expiries[:6], "spot": spot or None,
            "atm": atm, "rows": rows, "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None,
            "max_pain": max_pain(rows)}


def max_pain(rows: list[dict[str, Any]]) -> float | None:
    """Strike where option writers pay out least (sum of in-the-money value times OI)."""
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return None
    best, best_pain = None, None
    for s in strikes:
        pain = 0.0
        for r in rows:
            k = r["strike"]
            pain += max(0.0, s - k) * _f((r.get("ce") or {}).get("oi"))
            pain += max(0.0, k - s) * _f((r.get("pe") or {}).get("oi"))
        if best_pain is None or pain < best_pain:
            best, best_pain = s, pain
    return best


# --- Gold, ETF, MF ----------------------------------------------------------------------------------------


def wealth_view() -> dict[str, Any]:
    h = holdings()
    tabs: dict[str, list[dict[str, Any]]] = {"gold": [], "equity_etf": [], "debt_etf": [], "mf": []}
    for r in h["rows"]:
        if r["asset_type"] in tabs and r.get("side") != "SHORT":
            tabs[r["asset_type"]].append(r)
    sips: list[dict[str, Any]] = []
    if is_owner() and h["kite"].get("connected"):
        try:
            for s in _kite_call("mf_sips") or []:
                if str(s.get("status", "")).upper() != "ACTIVE":
                    continue
                sips.append({"fund": s.get("fund"), "symbol": s.get("tradingsymbol"),
                             "amount": _f(s.get("instalment_amount")), "frequency": s.get("frequency"),
                             "next_date": str(s.get("next_instalment") or "")[:10] or None})
        except Exception:
            pass
    today = datetime.now(IST).date()
    week_end = today + timedelta(days=7)
    due = [s for s in sips if s["next_date"] and today.isoformat() <= s["next_date"] <= week_end.isoformat()]
    for fund in tabs["mf"]:
        nxt = next((s for s in sips if s["symbol"] == fund["symbol"]), None)
        fund["next_sip"] = nxt
    alloc = {k: round(sum(r["value"] for r in v), 2) for k, v in tabs.items()}
    total = sum(alloc.values())
    invested = sum(r["invested"] for v in tabs.values() for r in v)
    return {"tabs": tabs, "sips": sips, "sips_due_week": due, "sips_due_amount": round(sum(s["amount"] for s in due), 2),
            "allocation": alloc, "allocation_pct": {k: round(v / total * 100, 1) if total else 0 for k, v in alloc.items()},
            "total_value": round(total, 2), "total_pnl": round(total - invested, 2),
            "return_pct": round((total - invested) / invested * 100, 2) if invested else None,
            "kite": h["kite"], "note": h["note"]}


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised return for dated cash flows (money in negative, value now positive). Newton, then bisection."""
    if len(flows) < 2 or not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(rate: float) -> float:
        return sum(v / (1 + rate) ** ((d - t0).days / 365.0) for d, v in flows)

    lo, hi = -0.9999, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-9:
            break
    return round((lo + hi) / 2 * 100, 2)


# --- Suggestions --------------------------------------------------------------------------------------------


def _boards() -> list[dict[str, Any]]:
    from src.alphadesk import profiles
    from src.alphadesk.store import alphadesk_root

    def read(root: Path) -> list[dict[str, Any]]:
        out = []
        d = root / "board_last"
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                try:
                    out.append(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return out

    mine = read(alphadesk_root())
    if mine or profiles.current_id() is None:
        return mine
    # A family user who hasn't run a scan sees the owner's last board (built from public end-of-day prices).
    return read(profiles.root_for(None))


def _track_line(row: dict[str, Any]) -> str:
    hist = (row.get("history") or {}).get("symbol_side") or {}
    trades = int(_f(hist.get("trades")))
    if trades:
        wr = _f(hist.get("win_rate"))
        wr = wr * 100 if wr <= 1 else wr
        return f"Past test: {trades} trades, {wr:.0f}% won, {_f(hist.get('avg_r')):+.2f}R average"
    side = (row.get("history") or {}).get("side") or {}
    if side.get("trades"):
        return f"Past test of this side: {int(_f(side.get('trades')))} trades, {_f(side.get('avg_r')):+.2f}R average"
    return "Not tested on past prices yet: treat as untested"


def suggestions() -> dict[str, Any]:
    cards: dict[str, dict[str, Any]] = {}
    as_of = None
    for b in _boards():
        as_of = max(filter(None, [as_of, b.get("as_of")]), default=None)
        for row in b.get("rows") or []:
            side = row.get("side")
            if side not in {"LONG", "SHORT", "STEER_CLEAR"}:
                continue
            sym = row["symbol"]
            card = {
                "id": f"board:{sym}", "symbol": sym, "name": row.get("name") or sym,
                "asset_type": classify(sym), "strategy": "signal board",
                "action": {"LONG": "buy", "SHORT": "short", "STEER_CLEAR": "avoid"}[side],
                "score": _f(row.get("score")), "reason": (row.get("reasons") or ["No reason recorded"])[0],
                "risk": (row.get("cautions") or [None])[0] or ("Prices are out of date" if row.get("stale") else None),
                "entry": row.get("entry"), "stop": row.get("stop"), "target": row.get("target"),
                "track": _track_line(row), "as_of": row.get("as_of"), "stale": bool(row.get("stale")),
                "universe": b.get("universe"),
            }
            old = cards.get(card["id"])
            if old is None or abs(card["score"]) > abs(old["score"]):
                cards[card["id"]] = card
    try:
        from src.alphadesk.btst import days

        latest = days.list_days("live", limit=1)
        rec = latest[0] if latest else None
        pick = (rec or {}).get("pick")
        if pick:
            st = days.stats("live")
            picks = (st or {}).get("picks") or {}
            n = int(_f(picks.get("n") or picks.get("count") or picks.get("trades")))
            avg = picks.get("avg_net_pct")
            track = (f"Overnight picks so far: {n} settled, {_f(avg):+.2f}% average after costs" if n
                     else "Overnight picks: no settled record yet")
            cards[f"btst:{pick['symbol']}"] = {
                "id": f"btst:{pick['symbol']}", "symbol": pick["symbol"], "name": pick.get("name") or pick["symbol"],
                "asset_type": "stock", "strategy": "overnight (BTST)", "action": "buy",
                "score": 99.0, "reason": f"Today's overnight pick for {rec.get('date')}",
                "risk": "Held overnight: a gap at the open can skip the stop", "entry": pick.get("entry"),
                "stop": pick.get("stop"), "target": pick.get("target"), "track": track, "as_of": rec.get("date"),
                "stale": rec.get("status") == "settled", "universe": None,
            }
    except Exception:
        pass
    order = {"buy": 0, "short": 1, "avoid": 2}
    ranked = sorted(cards.values(), key=lambda c: (order[c["action"]], -abs(c["score"])))
    return {"cards": ranked, "as_of": as_of, "disclaimer": "Model ideas, not advice. Practice with fake money first."}
