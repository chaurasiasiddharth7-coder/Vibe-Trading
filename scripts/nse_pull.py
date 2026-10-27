#!/usr/bin/env python3
"""Build and maintain the local NSE store used by the ``nse_bhavcopy`` loader.

Official, free NSE sources only (method from abhij1306/NSE-noodles, MIT): the daily bhavcopy
archive (old CM format to 5 Jul 2024, UDiFF from 8 Jul 2024), the corporate-action feed, the
symbol-change list, the holiday list and the daily index archive. Requests are paced (2 s) and
resumable; raw downloads are kept so a rebuild never re-downloads. The data belongs to NSE: do
not redistribute it.

    python scripts/nse_pull.py --start 2023-01-01      # recent years first (minutes)
    python scripts/nse_pull.py --start 2011-06-22      # full history (~3,800 files, ~2 h)
    python scripts/nse_pull.py --update                # continue from the store's last session
    python scripts/nse_pull.py --build-only            # re-derive factors from what is on disk
    python scripts/nse_pull.py --report                # coverage gaps, review list, jump report
    python scripts/nse_pull.py --reconcile             # cross-check unresolved actions against Yahoo
    python scripts/nse_pull.py --probe-weekends        # try every weekend for a special session
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from backtest.loaders import nse_store  # noqa: E402
from backtest.loaders.nse_corporate_actions import is_demerger, parse_ex_date  # noqa: E402

FIRST_ISIN_DAY, OLD_LAST, UDIFF_FIRST = date(2011, 6, 22), date(2024, 7, 5), date(2024, 7, 8)
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
EQUITY_SERIES = {"EQ": 0, "BE": 1, "BZ": 2, "BL": 3, "BT": 3, "IL": 3, "IQ": 3}
# Known weekend special sessions (Budget Saturdays, Muhurat Saturdays/Sundays). A 404 is harmless.
SPECIAL_SESSIONS = [date(2015, 2, 28), date(2016, 10, 30), date(2019, 10, 27), date(2020, 2, 1),
                    date(2020, 11, 14), date(2023, 11, 12), date(2025, 2, 1)]
INDEX_NAMES = {"NIFTY 50", "NIFTY 500", "INDIA VIX"}
INDEX_ALIASES = {"CNX NIFTY": "NIFTY 50", "S&P CNX NIFTY": "NIFTY 50", "CNX 500": "NIFTY 500",
                 "S&P CNX 500": "NIFTY 500"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, text/csv, application/zip, */*",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}
OLD = dict(date="TIMESTAMP", isin="ISIN", series="SERIES", symbol="SYMBOL", open="OPEN",
           high="HIGH", low="LOW", close="CLOSE", prev="PREVCLOSE", volume="TOTTRDQTY")
UDIFF = dict(date="TradDt", isin="ISIN", series="SctySrs", symbol="TckrSymb", open="OpnPric",
             high="HghPric", low="LwPric", close="ClsPric", prev="PrvsClsgPric",
             volume="TtlTradgVol")


class Client:
    def __init__(self, interval: float = 2.0) -> None:
        self.http = httpx.Client(headers=HEADERS, timeout=40, follow_redirects=True)
        self.interval, self.last, self.warm = interval, 0.0, False

    def get(self, url: str, params: dict | None = None, cookies: bool = False) -> bytes | None:
        """Bytes, or None for a 404 (holiday) / persistent error."""
        if cookies and not self.warm:
            self.http.get("https://www.nseindia.com")
            self.warm = True
        for attempt in range(5):
            time.sleep(max(0.0, self.last + self.interval - time.monotonic()))
            self.last = time.monotonic()
            try:
                r = self.http.get(url, params=params)
            except httpx.HTTPError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.content
            time.sleep(2 ** attempt)
        return None


# --- bhavcopy -----------------------------------------------------------------------------------


def archive(day: date) -> tuple[str, str]:
    if day <= OLD_LAST:
        name = f"cm{day.day:02d}{MONTHS[day.month - 1]}{day.year}bhav.csv.zip"
        return name, ("https://nsearchives.nseindia.com/content/historical/EQUITIES/"
                      f"{day.year}/{MONTHS[day.month - 1]}/{name}")
    name = f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return name, f"https://nsearchives.nseindia.com/content/cm/{name}"


def ordinary_equity(isin: str) -> bool:
    """IN + E/9 + issuer + security type 01, read from the ISIN itself (NSE's series labels are
    only hints). Drops rights entitlements (-RE), preference shares, debt and funds."""
    return len(isin) == 12 and isin[:2] == "IN" and isin[2] in "E9" and isin[7:9] == "01"


def parse_day(day: date, data: bytes) -> list[dict]:
    cols = OLD if day <= OLD_LAST else UDIFF
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        text = z.read([n for n in z.namelist() if n.lower().endswith(".csv")][0]).decode("utf-8-sig")
    best: dict[str, tuple[int, dict]] = {}
    for raw in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        rank = EQUITY_SERIES.get(row.get(cols["series"], "").upper())
        isin = row.get(cols["isin"], "").upper()
        if rank is None or not ordinary_equity(isin):
            continue
        try:
            o, h, lo, c, v = (float(row[cols[k]]) for k in ("open", "high", "low", "close", "volume"))
        except (KeyError, ValueError):
            continue
        try:
            prev = float(row[cols["prev"]])
        except (KeyError, ValueError):
            prev = float("nan")
        if min(o, h, lo, c) <= 0 or not lo <= o <= h or not lo <= c <= h or v < 0:
            continue
        bar = dict(date=day, isin=isin, symbol=row[cols["symbol"]].upper(), open=o, high=h, low=lo,
                   close=c, prev_close=prev, volume=v)
        if isin not in best or rank < best[isin][0]:
            best[isin] = (rank, bar)
    return [b for _, b in best.values()]


def candidate_days(start: date, end: date, extra: list[date], weekends: bool) -> list[date]:
    days = [start + timedelta(n) for n in range((end - start).days + 1)]
    return [d for d in days if (d.weekday() < 5 or weekends or d in extra)
            and d >= FIRST_ISIN_DAY and not OLD_LAST < d < UDIFF_FIRST]


def pull_files(root: Path, client: Client, kind: str, days: list[date], name_url) -> None:
    """Download each missing day once. A 404 is remembered (holiday candidate) in not_found.txt."""
    folder = root / "raw" / kind
    folder.mkdir(parents=True, exist_ok=True)
    memo = root / "raw" / f"not_found_{kind}.txt"
    skip = set(memo.read_text().split()) if memo.exists() else set()
    for i, day in enumerate(days):
        name, url = name_url(day)
        if (folder / name).exists() or day.isoformat() in skip:
            continue
        data = client.get(url)
        if data is None:
            skip.add(day.isoformat())
        else:
            (folder / name).write_bytes(data)
        if i % 50 == 0:
            print(f"{kind} {day} ({i + 1}/{len(days)})", flush=True)
            memo.write_text("\n".join(sorted(skip)))
    memo.write_text("\n".join(sorted(skip)))


def build_bars(root: Path) -> None:
    frames = []
    for path in sorted((root / "raw" / "bhavcopy").glob("*.zip")):
        n = path.name
        day = (datetime.strptime(n[2:11], "%d%b%Y").date() if n.startswith("cm")
               else datetime.strptime(n.split("_")[6], "%Y%m%d").date())
        try:
            frames.append(pd.DataFrame(parse_day(day, path.read_bytes())))
        except (zipfile.BadZipFile, IndexError) as exc:
            print(f"skip {n}: {exc}", file=sys.stderr)
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise SystemExit("no bhavcopy files on disk")
    bars = pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])
    bars.to_parquet(root / "bars.parquet", index=False, row_group_size=200_000)
    print(f"bars.parquet: {len(bars):,} rows, {bars['date'].nunique()} sessions, "
          f"{bars['isin'].nunique()} ISINs")


# --- corporate actions, lists -------------------------------------------------------------------


def pull_actions(root: Path, client: Client, start: date, end: date) -> None:
    path = root / "corporate_actions.json"
    rows: list[dict] = json.loads(path.read_text()) if path.exists() else []
    day = start
    while day <= end:
        stop = min(day + timedelta(days=30), end)
        data = client.get("https://www.nseindia.com/api/corporates-corporateActions",
                          {"index": "equities", "from_date": f"{day:%d-%m-%Y}",
                           "to_date": f"{stop:%d-%m-%Y}"}, cookies=True)
        if data is None:
            print(f"corporate actions {day}..{stop}: failed, rerun to retry", file=sys.stderr)
        else:
            payload = json.loads(data)
            rows.extend(payload.get("data", []) if isinstance(payload, dict) else payload)
        day = stop + timedelta(days=1)
    unique = {json.dumps(r, sort_keys=True): r for r in rows}
    path.write_text(json.dumps(list(unique.values())), encoding="utf-8")
    print(f"corporate actions: {len(unique)} rows")


def pull_lists(root: Path, client: Client) -> None:
    for name, url, cookies in (
        ("symbolchange.csv", "https://nsearchives.nseindia.com/content/equities/symbolchange.csv", False),
        ("holidays.json", "https://www.nseindia.com/api/holiday-master?type=trading", True),
    ):
        data = client.get(url, cookies=cookies)
        if data:
            (root / name).write_bytes(data)


# --- indices (targeted at demerger ex-dates unless --indices-all) -------------------------------


def index_archive(day: date) -> tuple[str, str]:
    name = f"ind_close_all_{day:%d%m%Y}.csv"
    return name, "https://nsearchives.nseindia.com/content/indices/" + name


def demerger_days(root: Path) -> list[date]:
    """Previous session, ex-date and the fifth session after, for every demerger in the feed."""
    sessions = sorted({d.date() for d in pd.to_datetime(
        pd.read_parquet(root / "bars.parquet", columns=["date"])["date"].unique())})
    wanted: set[date] = set()
    for row in nse_store.read_actions(root):
        if not is_demerger(str(row.get("subject", ""))):
            continue
        ex = parse_ex_date(row.get("exDate"))
        if ex is None:
            continue
        pos = next((i for i, s in enumerate(sessions) if s >= ex), None)
        if pos is None:
            continue
        wanted.update(sessions[max(pos - 1, 0): pos + 1])
        if pos + 5 < len(sessions):
            wanted.add(sessions[pos + 5])
    return sorted(wanted)


def build_indices(root: Path) -> None:
    rows = []
    for path in sorted((root / "raw" / "indices").glob("ind_close_all_*.csv")):
        n = path.stem.split("_")[-1]
        day = date(int(n[4:]), int(n[2:4]), int(n[:2]))
        text = path.read_bytes().decode("utf-8-sig", errors="replace")
        for raw in csv.DictReader(io.StringIO(text)):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            name = row.get("index name", "").upper()
            name = INDEX_ALIASES.get(name, name)
            if name not in INDEX_NAMES:
                continue
            try:
                vals = [float(row[k].replace(",", "")) for k in
                        ("open index value", "high index value", "low index value",
                         "closing index value")]
            except (KeyError, ValueError):
                continue
            rows.append(dict(date=day, index_name=name, open=vals[0], high=vals[1], low=vals[2],
                             close=vals[3]))
    if rows:
        pd.DataFrame(rows).to_parquet(root / "indices.parquet", index=False)
        print(f"indices.parquet: {len(rows)} rows")


# --- reports ------------------------------------------------------------------------------------


def reconcile(root: Path, everything: bool = False) -> None:
    """Cross-check adjustments against Yahoo's for symbols with open problems (or all)."""
    from backtest.loaders import nse_witness

    syms: set[str] = set()
    if (root / "review.json").exists():
        syms |= {r["symbol"] for r in json.loads((root / "review.json").read_text())}
    if (root / "jumps.csv").exists():
        syms |= set(pd.read_csv(root / "jumps.csv")["symbol"])
    if everything:
        last = pd.read_parquet(root / "bars.parquet", columns=["symbol", "date"])
        syms = set(last[last["date"] >= last["date"].max() - pd.Timedelta(days=365)]["symbol"])
    print(f"reconciling {len(syms)} symbols against Yahoo (about {len(syms) * 0.7 / 60:.0f} min)")
    print("witness:", nse_witness.run(root, sorted(syms)))
    print("factors:", nse_store.build_all(root))


def report(root: Path) -> None:
    bars = pd.read_parquet(root / "bars.parquet", columns=["date"])["date"]
    first, last = bars.min(), bars.max()
    cov = nse_store.coverage(root, first, last)
    print(f"sessions {cov['sessions']} ({first} .. {last}); listed holidays {cov['listed_holidays']};"
          f" unaccounted weekdays {len(cov['unaccounted_weekdays'])}")
    for d in cov["unaccounted_weekdays"][:20]:
        print("  unaccounted:", d)
    review_path = root / "review.json"
    if review_path.exists():
        review = json.loads(review_path.read_text())
        print(f"review list: {len(review)} items (review.json)")
    jumps = nse_store.jump_report(root)
    jumps.to_csv(root / "jumps.csv", index=False)
    print(f"adjusted jumps beyond 25%: {len(jumps)} (jumps.csv); "
          f"{int(jumps['has_review_item'].sum())} on symbols with review items")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--update", action="store_true", help="start the day after the store's last session")
    ap.add_argument("--extra-days", nargs="*", default=[], help="more weekend sessions to try")
    ap.add_argument("--probe-weekends", action="store_true", help="try every weekend day")
    ap.add_argument("--indices-all", action="store_true", help="index archive for every session")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--reconcile", action="store_true", help="cross-check unresolved actions against Yahoo")
    ap.add_argument("--reconcile-all", action="store_true", help="... for every symbol active in the last year")
    a = ap.parse_args()
    root = nse_store.store_dir()
    root.mkdir(parents=True, exist_ok=True)
    if a.report:
        report(root)
        return
    if a.reconcile or a.reconcile_all:
        reconcile(root, a.reconcile_all)
        report(root)
        return
    if not a.build_only:
        start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
        if a.update and (root / "bars.parquet").exists():
            last = pd.read_parquet(root / "bars.parquet", columns=["date"])["date"].max()
            start = pd.Timestamp(last).date() + timedelta(days=1)
        client = Client()
        extra = SPECIAL_SESSIONS + [date.fromisoformat(d) for d in a.extra_days]
        pull_files(root, client, "bhavcopy", candidate_days(start, end, extra, a.probe_weekends),
                   archive)
        pull_actions(root, client, start - timedelta(days=45), end + timedelta(days=60))
        pull_lists(root, client)
    build_bars(root)
    if not a.build_only:
        days = (sorted({d.date() for d in pd.to_datetime(
            pd.read_parquet(root / "bars.parquet", columns=["date"])["date"].unique())})
                if a.indices_all else demerger_days(root))
        pull_files(root, client, "indices", days, index_archive)
    build_indices(root)
    print("factors:", nse_store.build_all(root))
    report(root)


if __name__ == "__main__":
    main()
