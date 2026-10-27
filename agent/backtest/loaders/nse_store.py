"""The local NSE store: bars, symbol history, ISIN lineage, versioned factors, coverage.

Layout under ``$NSE_DATA_DIR`` (default ``~/.vibe-trading/nse``)::

    raw/bhavcopy/*.zip  raw/indices/*.csv      untouched downloads (never rewritten)
    bars.parquet        one row per ISIN per session, sorted by symbol/date
    indices.parquet     daily index OHLC (NIFTY 50 / NIFTY 500 / INDIA VIX)
    corporate_actions.json  symbolchange.csv  holidays.json
    factors.json        accepted price factors + dividends, each with availability times
    review.json         everything the price gate or parsers refused, with the reason
    factor_versions.jsonl   append-only ledger of every factor version ever produced

Point in time: a factor is ``available_at`` the ex-date 10:00 IST (its evidence is the opening
price), a demerger only once the close five sessions later confirmed it. ``load_factors(asof=D)``
returns what was knowable at the end of D, so a replay never reads a future adjustment.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from backtest.loaders.nse_corporate_actions import (
    DEMERGER_CHECK_SESSIONS, Dividend, Factor, apply_factors, build_factors, find_jumps,
    parse_ex_date,
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
LINK_MAX_GAP_DAYS = 10  # about 5 sessions between an old ISIN's last bar and the new one's first
LINK_PRICE_TOL = 0.01
LINK_OPEN_TOL = 0.10
DEFAULT_INDEX = "NIFTY 500"


def store_dir() -> Path:
    return Path(os.environ.get("NSE_DATA_DIR") or Path.home() / ".vibe-trading" / "nse")


def now_ist() -> datetime:
    return datetime.now(IST)


# --- reading ------------------------------------------------------------------------------------


def read_bars(root: Path, symbols: list[str] | None = None) -> pd.DataFrame:
    path = root / "bars.parquet"
    filters = [("symbol", "in", symbols)] if symbols else None
    frame = pd.read_parquet(path, filters=filters)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def read_index(root: Path, name: str = DEFAULT_INDEX) -> pd.DataFrame | None:
    path = root / "indices.parquet"
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    frame = frame[frame["index_name"] == name]
    if frame.empty:
        return None
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date").sort_index()[["open", "close"]]


def read_actions(root: Path) -> list[dict]:
    path = root / "corporate_actions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def symbol_aliases(symbol: str, root: Path) -> list[str]:
    """The symbol plus its earlier names (NSE symbolchange.csv: SYMB-OLD, SYMB-NEW)."""
    path = root / "symbolchange.csv"
    names = [symbol]
    if not path.exists():
        return names
    try:
        table = pd.read_csv(path, header=0, usecols=[1, 2], names=["old", "new"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("symbolchange.csv unreadable: %s", exc)
        return names
    old_for = dict(zip(table["new"].astype(str).str.strip().str.upper(),
                       table["old"].astype(str).str.strip().str.upper()))
    while names[-1] in old_for and old_for[names[-1]] not in names:
        names.append(old_for[names[-1]])
    return names


# --- ISIN lineage -------------------------------------------------------------------------------


_ACTION_WINDOW_DAYS = 7
_ACTIONS: dict[str, tuple[float, dict[str, list[date]]]] = {}


def action_dates(root: Path) -> dict[str, list[date]]:
    """Ex-dates of every non-dividend corporate action per symbol (cached per file version)."""
    path = root / "corporate_actions.json"
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    cached = _ACTIONS.get(str(path))
    if not cached or cached[0] != mtime:
        out: dict[str, list[date]] = {}
        for row in read_actions(root):
            text = str(row.get("subject", "")).lower()
            ex = parse_ex_date(row.get("exDate"))
            if ex and text and not text.strip().startswith(("dividend", "interim dividend",
                                                            "final dividend", "special dividend")):
                out.setdefault(str(row.get("symbol", "")).upper(), []).append(ex)
        cached = (mtime, out)
        _ACTIONS[str(path)] = cached
    return cached[1]


def link_isins(part: pd.DataFrame, event_dates: list[date] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """One continuous history out of the rows for a symbol, across ISINs.

    A new ISIN continues the old only when it succeeds it within about 5 sessions with no
    overlap and its first PREVCLOSE equals the old last close within 1%. Without a corporate
    action (``event_dates``: ex-dates of splits, bonuses, ...) within a week of the switch its
    first open must also be within 10% of that close; with one, the open may sit anywhere, since
    a split reissues the ISIN at a fraction of the price. Anything else starts a new history:
    a symbol reused by another company must not be glued to the old one. The latest chain is
    returned with notes on what was cut.
    """
    segments = sorted(((g["date"].min(), g["date"].max(), isin, g.sort_values("date"))
                       for isin, g in part.groupby("isin")), key=lambda s: s[0])
    notes: list[str] = []
    chain = [segments[0]]
    for seg in segments[1:]:
        prev = chain[-1]
        first, last = seg[3].iloc[0], prev[3].iloc[-1]
        gap_days = (seg[0] - prev[1]).days
        near_action = any(abs((e - seg[0].date()).days) <= _ACTION_WINDOW_DAYS
                          for e in event_dates or [])
        continuous = pd.notna(first.get("prev_close")) and (
            abs(float(first["prev_close"]) / float(last["close"]) - 1) <= LINK_PRICE_TOL
            or (near_action and 0.02 <= float(first["prev_close"]) / float(last["close"]) <= 50))
        open_ok = near_action or abs(float(first["open"]) / float(last["close"]) - 1) <= LINK_OPEN_TOL
        if 0 < gap_days <= LINK_MAX_GAP_DAYS and continuous and open_ok:
            chain.append(seg)
        else:
            notes.append(f"{prev[2]} -> {seg[2]} not linked (gap {gap_days}d)")
            chain = [seg]
    return pd.concat([s[3] for s in chain], ignore_index=True), notes


def symbol_history(root: Path, symbol: str, bars: pd.DataFrame | None = None) -> pd.DataFrame | None:
    """RAW daily history for ``symbol`` (and earlier names), linked across ISINs; None if absent."""
    names = symbol_aliases(symbol, root)
    part = read_bars(root, names) if bars is None else bars[bars["symbol"].isin(names)]
    if part.empty:
        return None
    events = [e for n in names for e in action_dates(root).get(n, [])]
    linked, notes = link_isins(part, events)
    for note in notes:
        logger.info("nse_store: %s: %s", symbol, note)
    linked = linked.drop_duplicates("date", keep="last")
    return linked.set_index("date").sort_index()[BAR_COLUMNS + ["isin"]]


# --- factors, ledger, point in time -------------------------------------------------------------


def _witnessed(root: Path, accepted: list[Factor]) -> list[Factor]:
    """Factors Yahoo's adjustment history and the ex-date gap both support (nse_witness.py),
    except where an accepted factor already sits within a few days of the same event."""
    path = root / "witness.json"
    if not path.exists():
        return []
    out = []
    for sym, rec in json.loads(path.read_text()).items():
        for w in rec.get("found", []):
            ex = date.fromisoformat(w["ex_date"])
            if any(a.symbol == sym and abs((a.ex_date - ex).days) <= 4 for a in accepted):
                continue
            out.append(Factor(sym, ex, "witnessed", w["factor"],
                              f"Yahoo-witnessed adjustment, ex-date gap {w['gap']}", "yahoo_witness"))
    return out


def _available_at(factor: Factor, sessions: pd.DatetimeIndex) -> datetime:
    ex = datetime.combine(factor.ex_date, datetime.min.time(), tzinfo=IST).replace(hour=10)
    if factor.kind not in ("demerger", "witnessed"):
        return ex
    pos = int(sessions.searchsorted(pd.Timestamp(factor.ex_date)))
    later = min(pos + DEMERGER_CHECK_SESSIONS, len(sessions) - 1)
    day = sessions[later].to_pydatetime()
    return datetime(day.year, day.month, day.day, 16, tzinfo=IST)


def _key(kind: str, symbol: str, ex: date, text: str) -> str:
    return f"{kind}|{symbol}|{ex.isoformat()}|{text}"


def _read_ledger(path: Path) -> dict[str, dict]:
    seen: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            seen.setdefault(rec["key"], rec)  # the first version of each key
    return seen


def build_all(root: Path) -> dict:
    """Derive factors / dividends / review lists from the store; append new versions to the ledger.

    The ledger is append-only: the first time a key (or a changed value of it) is produced its
    ``first_observed_at`` is recorded and never rewritten.
    """
    actions = read_actions(root)
    if not actions:
        raise RuntimeError("corporate_actions.json missing or empty: run the pull first")
    symbols = sorted({str(r.get("symbol", "")).upper() for r in actions})
    everything = read_bars(root, sorted({n for s in symbols for n in symbol_aliases(s, root)}))
    raw = {}
    for symbol in symbols:
        hist = symbol_history(root, symbol, everything)
        if hist is not None:
            raw[symbol] = hist[BAR_COLUMNS]
    factors, dividends, review = build_factors(actions, raw, read_index(root))
    factors += _witnessed(root, factors)
    sessions = pd.DatetimeIndex(sorted(everything["date"].unique()))

    ledger_path = root / "factor_versions.jsonl"
    ledger = _read_ledger(ledger_path)
    stamp = now_ist().isoformat(timespec="seconds")
    records, lines = [], []

    def record(kind: str, symbol: str, ex: date, text: str, value: float, avail: datetime, extra: dict):
        key = _key(kind, symbol, ex, text)
        first = ledger.get(key)
        observed = first["first_observed_at"] if first and first["value"] == value else stamp
        row = {"key": key, "kind": kind, "symbol": symbol, "ex_date": ex.isoformat(), "text": text,
               "value": value, "available_at": avail.isoformat(timespec="seconds"),
               "first_observed_at": observed, **extra}
        if not first or first["value"] != value:
            lines.append(json.dumps(row))
            ledger.setdefault(key, row)
        records.append(row)

    for f in factors:
        record(f.kind, f.symbol, f.ex_date, f.text, f.factor, _available_at(f, sessions),
               {"status": f.status})
    for d in dividends:
        avail = datetime.combine(d.ex_date, datetime.min.time(), tzinfo=IST).replace(hour=10)
        record("dividend", d.symbol, d.ex_date, d.text, d.amount, avail, {"status": "confirmed"})
    if lines:
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    (root / "factors.json").write_text(json.dumps(records), encoding="utf-8")
    (root / "review.json").write_text(
        json.dumps([{**r, "ex_date": r["ex_date"].isoformat()} for r in review]), encoding="utf-8")
    return {"factors": len(factors), "dividends": len(dividends), "review": len(review),
            "new_versions": len(lines)}


_RECORDS: dict[str, tuple[float, list[dict]]] = {}


def _records(path: Path) -> list[dict]:
    """factors.json parsed once per file version (the jump report asks per symbol)."""
    mtime = path.stat().st_mtime
    cached = _RECORDS.get(str(path))
    if not cached or cached[0] != mtime:
        cached = (mtime, json.loads(path.read_text(encoding="utf-8")))
        _RECORDS[str(path)] = cached
    return cached[1]


def load_factors(root: Path, symbol: str, asof: str | None = None) -> tuple[list[Factor], list[Dividend]]:
    """Factors and dividends of ``symbol`` (and earlier names) knowable at the end of ``asof``."""
    path = root / "factors.json"
    if not path.exists():
        return [], []
    cutoff = None
    if asof:
        d = date.fromisoformat(asof)
        cutoff = datetime.combine(d, datetime.max.time(), tzinfo=IST)
    names = set(symbol_aliases(symbol, root))
    factors, dividends = [], []
    for r in _records(path):
        if r["symbol"] not in names:
            continue
        if cutoff and datetime.fromisoformat(r["available_at"]) > cutoff:
            continue
        ex = date.fromisoformat(r["ex_date"])
        if r["kind"] == "dividend":
            dividends.append(Dividend(symbol, ex, r["value"], r["text"]))
        else:
            factors.append(Factor(symbol, ex, r["kind"], r["value"], r["text"], r["status"]))
    return factors, dividends


# --- reports ------------------------------------------------------------------------------------


def coverage(root: Path, start: date, end: date) -> dict:
    """Every weekday in [start, end] accounted for: a session, a listed holiday, or unaccounted."""
    sessions = {d.date() for d in pd.to_datetime(read_bars(root, None)["date"].unique())} \
        if (root / "bars.parquet").exists() else set()
    holidays: set[date] = set()
    path = root / "holidays.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for rows in payload.values() if isinstance(payload, dict) else []:
                for row in rows if isinstance(rows, list) else []:
                    d = parse_holiday(row.get("tradingDate"))
                    if d:
                        holidays.add(d)
        except (ValueError, AttributeError):
            pass
    extra = root / "holidays_extra.txt"  # your own list: NSE publishes only the current year's
    if extra.exists():
        for line in extra.read_text().split():
            try:
                holidays.add(date.fromisoformat(line))
            except ValueError:
                pass
    unaccounted, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in sessions and day not in holidays:
            unaccounted.append(day)
        day += timedelta(days=1)
    return {"sessions": len(sessions), "listed_holidays": len(holidays),
            "unaccounted_weekdays": unaccounted}


def parse_holiday(raw: object) -> date | None:
    try:
        return datetime.strptime(str(raw).strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def jump_report(root: Path, basis: str = "price") -> pd.DataFrame:
    """Every adjusted session move beyond 25% across the universe: the final check (rule 31).

    Each row is either a genuine move or an unresolved action; ``reviewed`` marks symbols that
    already carry something in review.json so they are triaged first.
    """
    bars = read_bars(root)
    reviewed = {r["symbol"] for r in json.loads((root / "review.json").read_text())} \
        if (root / "review.json").exists() else set()
    rows = []
    for symbol, part in bars.groupby("symbol"):
        hist = symbol_history(root, str(symbol), bars)
        if hist is None or len(hist) < 2:
            continue
        factors, dividends = load_factors(root, str(symbol))
        adjusted = apply_factors(hist[BAR_COLUMNS], factors, dividends, basis)
        for ts, ret in find_jumps(adjusted):
            rows.append({"symbol": symbol, "date": ts.date(), "move": round(ret, 4),
                         "has_review_item": symbol in reviewed})
    return pd.DataFrame(rows, columns=["symbol", "date", "move", "has_review_item"])
