"""The desk's "Today" brief: index strip, market mood, top picks and the news that matters.

Every part degrades on its own: a failed news feed or a missing AI model never blanks the rest.

* Indices (NIFTY 50, NIFTY BANK, INDIA VIX): Kite live on the owner's desk; otherwise the NSE store's close
  when it is current, else Yahoo's last close (the NSE index archive lags), else the store's older close with
  its date. Kite numbers stay owner-only (exchange rules), as everywhere on the desk.
* Breadth and movers: Nifty 500 from Kite quotes while live (owner), else the NSE store's last two sessions.
* Top picks: the strongest buy ideas from this profile's latest Ideas scan (paper ideas, not advice).
* News: Indian market headlines from public RSS feeds and Yahoo, ranked by what moves the market
  (RBI, Fed, inflation, results, FII flows, crude...) and flagged when they name a stock on your watchlist.
  Only the headline, source, time and link are kept.
* Insight: three plain sentences from the numbers above; on the owner's desk an AI summary when a model
  is configured (cached for an hour), with the rule-based text as the fallback.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

INDICES = ["NIFTY 50", "NIFTY BANK", "INDIA VIX"]
YAHOO_INDEX = {"NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK", "INDIA VIX": "^INDIAVIX"}

FEEDS = [
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Mint", "https://www.livemint.com/rss/markets"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
]
YAHOO_QUERIES = ["Nifty", "Sensex"]

# What tends to move Indian markets, with a weight each. A headline's importance is the sum, plus recency.
IMPORTANT = {
    r"\brbi\b|repo rate|monetary policy|mpc\b": 5,
    r"\bfed\b|fomc|powell|us rate|treasury yield": 4,
    r"inflation|\bcpi\b|\bwpi\b|\bgdp\b|\biip\b|fiscal deficit|budget": 4,
    r"\bsebi\b": 3,
    r"\bfii|\bfpi|foreign investor|\bdii\b": 4,
    r"crude|brent|oil price": 3,
    r"rupee|\binr\b|dollar index": 3,
    r"tariff|sanction|war\b|geopolit|ceasefire": 3,
    r"results?\b|q[1-4]\b|earnings|profit|guidance": 2,
    r"\bnifty\b|\bsensex\b|bank nifty": 2,
    r"\bipo\b|listing": 1,
    r"crash|plunge|surge|record high|all-time high|52-week": 2,
    r"block deal|bulk deal|stake sale|buyback|dividend|split|bonus": 1,
}
_IMPORTANT = [(re.compile(p, re.I), w) for p, w in IMPORTANT.items()]
_CACHE: dict[str, tuple[float, Any]] = {}
_LOCK = threading.Lock()


def _cached(key: str, ttl: float, fn):
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    with _LOCK:
        _CACHE[key] = (time.time(), value)
    return value


def now_ist() -> datetime:
    return datetime.now(IST)


# --- market status and indices ------------------------------------------------------------------------


def market_status(now: datetime | None = None) -> dict[str, str]:
    now = now or now_ist()
    try:
        from src.alphadesk.intraday import calendar as cal

        state = cal.market_state(now)
    except Exception:  # noqa: BLE001
        state = "closed"
    label = {"open": "Market open", "pre_open": "Pre-open", "closed": "Market closed", "holiday": "Market holiday"}[state]
    return {"state": state, "label": label}


def _row(name: str, ltp: float | None, prev: float | None, **extra: Any) -> dict[str, Any]:
    chg = (ltp - prev) if ltp is not None and prev else None
    return {"symbol": name, "exchange": "INDEX", "ltp": ltp, "prev_close": prev,
            "change": round(chg, 2) if chg is not None else None,
            "change_pct": round(chg / prev * 100, 2) if chg is not None and prev else None, **extra}


def _yahoo_index(name: str) -> dict[str, Any] | None:
    def fetch():
        from backtest.loaders import yahoo_client

        bars = yahoo_client.get_chart(YAHOO_INDEX[name], interval="1d", range_="10d")
        bars = [b for b in bars if b.get("close")]
        if len(bars) < 2:
            return None
        last, prev = bars[-1], bars[-2]
        ts = last.get("trade_date")
        as_of = datetime.fromtimestamp(float(ts), IST).date().isoformat() if ts else None
        return _row(name, float(last["close"]), float(prev["close"]), as_of=as_of, source="yahoo")

    try:
        return _cached(f"yidx:{name}", 600, fetch)
    except Exception as exc:  # noqa: BLE001
        logger.info("yahoo index %s: %s", name, exc)
        return None


def latest_stock_session() -> str | None:
    try:
        from src.alphadesk import watchlist

        stocks, _ = watchlist._eod_tables()
        dates = [v["as_of"] for v in stocks.values() if v.get("as_of")]
        return max(dates) if dates else None
    except Exception:  # noqa: BLE001
        return None


def index_rows(live: bool) -> list[dict[str, Any]]:
    """NIFTY 50, NIFTY BANK and INDIA VIX, freshest source first."""
    out: list[dict[str, Any]] = []
    if live:
        from src.live.profit_guard.feeds import live_quotes

        live_quotes.prime(INDICES)
        for n in INDICES:
            q = live_quotes.quote(n)
            if q:
                out.append(_row(n, q.get("last_price"), (q.get("ohlc") or {}).get("close"), source="kite"))
        if len(out) == len(INDICES):
            return out
    have = {r["symbol"] for r in out}
    try:
        from src.alphadesk import watchlist

        _, store = watchlist._eod_tables()
    except Exception:  # noqa: BLE001
        store = {}
    session = latest_stock_session()
    for n in INDICES:
        if n in have:
            continue
        e = store.get(n)
        if e and session and e["as_of"] >= session:
            out.append(_row(n, e["ltp"], e["prev"], as_of=e["as_of"], source="nse"))
            continue
        y = _yahoo_index(n)
        if y:
            out.append(y)
        elif e:
            out.append(_row(n, e["ltp"], e["prev"], as_of=e["as_of"], source="nse", stale=True))
    order = {n: i for i, n in enumerate(INDICES)}
    return sorted(out, key=lambda r: order.get(r["symbol"], 99))


# --- breadth and movers -------------------------------------------------------------------------------


def _members() -> dict[str, str]:
    try:
        from src.alphadesk.index_lists import members

        return {m["symbol"]: m.get("industry") or "Other" for m in members("nifty500")}
    except Exception:  # noqa: BLE001
        return {}


def breadth(live: bool) -> dict[str, Any]:
    """Advancers/decliners, top movers and sector moves across the Nifty 500."""
    universe = _members()
    changes: dict[str, tuple[float, float]] = {}  # symbol -> (ltp, pct)
    source, as_of = "nse", None
    if live and universe:
        from src.live.profit_guard.feeds import live_quotes

        live_quotes.prime(list(universe))
        for s in universe:
            q = live_quotes.quote(s)
            prev = (q or {}).get("ohlc", {}).get("close")
            if q and prev and q.get("last_price"):
                changes[s] = (float(q["last_price"]), (float(q["last_price"]) - prev) / prev * 100)
        if changes:
            source, as_of = "kite", now_ist().strftime("%H:%M")
    if not changes:
        from src.alphadesk import watchlist

        stocks, _ = watchlist._eod_tables()
        pool = universe or {s: "Other" for s in stocks}
        for s in pool:
            e = stocks.get(s)
            if e and e.get("prev"):
                changes[s] = (e["ltp"], (e["ltp"] - e["prev"]) / e["prev"] * 100)
                as_of = max(as_of or "", e["as_of"])
    if not changes:
        return {}
    adv = sum(1 for _, p in changes.values() if p > 0.05)
    dec = sum(1 for _, p in changes.values() if p < -0.05)
    ranked = sorted(changes.items(), key=lambda kv: kv[1][1], reverse=True)
    mover = lambda kv: {"symbol": kv[0], "ltp": round(kv[1][0], 2), "change_pct": round(kv[1][1], 2)}  # noqa: E731
    sectors: dict[str, list[float]] = {}
    for s, (_, p) in changes.items():
        sectors.setdefault(universe.get(s, "Other"), []).append(p)
    sector_avg = sorted(((k, sum(v) / len(v), len(v)) for k, v in sectors.items() if len(v) >= 5),
                        key=lambda t: t[1], reverse=True)
    sec = lambda t: {"sector": t[0], "change_pct": round(t[1], 2), "stocks": t[2]}  # noqa: E731
    return {
        "source": source, "as_of": as_of, "universe": "Nifty 500" if universe else "all NSE stocks",
        "advancers": adv, "decliners": dec, "unchanged": len(changes) - adv - dec,
        "gainers": [mover(kv) for kv in ranked[:5]], "losers": [mover(kv) for kv in ranked[::-1][:5]],
        "sectors_up": [sec(t) for t in sector_avg[:3]], "sectors_down": [sec(t) for t in sector_avg[::-1][:3]],
    }


# --- top picks ----------------------------------------------------------------------------------------


def top_picks(limit: int = 5) -> dict[str, Any]:
    """Best buy ideas from this profile's most recent Ideas scan."""
    from src.alphadesk import board_jobs
    from src.alphadesk.index_lists import UNIVERSES

    best: dict[str, Any] | None = None
    for key in UNIVERSES:
        try:
            b = board_jobs.load_last(key)
        except (OSError, ValueError):
            b = None
        if b and (best is None or str(b.get("scanned_at", "")) > str(best.get("scanned_at", ""))):
            best = b
    if not best:
        return {"rows": [], "scanned_at": None, "universe": None}
    rows = []
    for r in (best.get("long") or [])[:limit]:
        rows.append({k: r.get(k) for k in ("symbol", "score", "price", "change_percent", "entry", "stop", "target",
                                           "risk_pct", "kronos_change_pct", "as_of")}
                    | {"reasons": (r.get("reasons") or [])[:3]})
    return {"rows": rows, "scanned_at": best.get("scanned_at"), "universe": best.get("universe"),
            "longs": len(best.get("long") or []), "shorts": len(best.get("short") or []),
            "scanned": best.get("scanned")}


# --- news ---------------------------------------------------------------------------------------------


def _clean_title(t: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()
    return re.sub(r"\s+", " ", t)[:220]


def _parse_rss(source: str, xml_text: str) -> list[dict[str, Any]]:
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = _clean_title(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        ts = None
        raw = item.findtext("pubDate")
        if raw:
            try:
                ts = parsedate_to_datetime(raw).astimezone(timezone.utc).timestamp()
            except (TypeError, ValueError):
                ts = None
        out.append({"title": title, "url": link, "source": source, "ts": ts})
    return out


def _fetch_feed(source: str, url: str) -> list[dict[str, Any]]:
    import requests

    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 (desk news reader)"})
        if r.status_code != 200:
            return []
        return _parse_rss(source, r.text)[:40]
    except Exception as exc:  # noqa: BLE001
        logger.info("news feed %s: %s", source, exc)
        return []


def _fetch_yahoo(query: str) -> list[dict[str, Any]]:
    try:
        from backtest.loaders import yahoo_client

        out = []
        for a in yahoo_client.search_news(query, 15):
            title, link = _clean_title(a.get("title") or ""), a.get("link") or ""
            if title and link.startswith("http"):
                out.append({"title": title, "url": link, "source": a.get("publisher") or "Yahoo",
                            "ts": float(a["providerPublishTime"]) if a.get("providerPublishTime") else None})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("yahoo news %s: %s", query, exc)
        return []


def importance(title: str) -> int:
    return sum(w for rx, w in _IMPORTANT if rx.search(title))


def _norm(t: str) -> str:
    return " ".join(sorted(set(re.findall(r"[a-z0-9]{4,}", t.lower()))))[:120]


def raw_news() -> list[dict[str, Any]]:
    def fetch():
        jobs = [(_fetch_feed, s, u) for s, u in FEEDS] + [(_fetch_yahoo, q, None) for q in YAHOO_QUERIES]
        items: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for got in pool.map(lambda j: j[0](j[1], j[2]) if j[2] else j[0](j[1]), jobs):
                items.extend(got)
        return items

    return _cached("news", 600, fetch)


def news(watch: list[str] | None = None, limit: int = 12, now: float | None = None,
         items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The headlines worth knowing today: ranked by market impact and freshness, duplicates folded together."""
    now = now or time.time()
    items = raw_news() if items is None else items
    watch_rx = [(s, re.compile(rf"\b{re.escape(s)}\b", re.I)) for s in (watch or []) if len(s) >= 2]
    seen: dict[str, dict[str, Any]] = {}
    for it in items:
        age_h = (now - it["ts"]) / 3600 if it.get("ts") else 12
        if age_h > 36 or age_h < -1:
            continue
        key = _norm(it["title"])
        if key in seen:
            seen[key]["sources"] = sorted({*seen[key]["sources"], it["source"]})
            continue
        mentions = [s for s, rx in watch_rx if rx.search(it["title"])]
        score = importance(it["title"]) + (3 if mentions else 0) + max(0.0, 3 - age_h / 4)
        seen[key] = {**it, "sources": [it["source"]], "watchlist": mentions, "importance": round(score, 2),
                     "important": importance(it["title"]) >= 4 or bool(mentions)}
    ranked = sorted(seen.values(), key=lambda x: (x["importance"], x.get("ts") or 0), reverse=True)
    return ranked[:limit]


# --- insight ------------------------------------------------------------------------------------------


def _fmt_pct(p: float | None) -> str:
    return "—" if p is None else f"{'+' if p > 0 else ''}{p:.2f}%"


def rule_insight(idx: list[dict[str, Any]], br: dict[str, Any], picks: dict[str, Any],
                 headlines: list[dict[str, Any]], status: dict[str, str]) -> list[str]:
    out: list[str] = []
    by = {r["symbol"]: r for r in idx}
    n = by.get("NIFTY 50")
    if n and n.get("change_pct") is not None:
        word = "up" if n["change_pct"] > 0 else "down" if n["change_pct"] < 0 else "flat"
        when = "today" if status["state"] == "open" else "at the last close"
        bank = by.get("NIFTY BANK")
        extra = f", Bank Nifty {_fmt_pct(bank['change_pct'])}" if bank and bank.get("change_pct") is not None else ""
        out.append(f"Nifty 50 is {word} {_fmt_pct(n['change_pct'])} {when}{extra}.")
    if br:
        total = br["advancers"] + br["decliners"] + br["unchanged"]
        tilt = ("most stocks rose" if br["advancers"] > 1.5 * br["decliners"] else
                "most stocks fell" if br["decliners"] > 1.5 * br["advancers"] else "stocks were mixed")
        lead = br["sectors_up"][0]["sector"] if br.get("sectors_up") else None
        lag = br["sectors_down"][0]["sector"] if br.get("sectors_down") else None
        sect = f" {lead} led; {lag} lagged." if lead and lag and lead != lag else ""
        out.append(f"Across the {br['universe']}, {tilt}: {br['advancers']} up, {br['decliners']} down of {total}.{sect}")
    vix = by.get("INDIA VIX")
    if vix and vix.get("ltp"):
        mood = "calm" if vix["ltp"] < 13 else "normal" if vix["ltp"] < 18 else "nervous"
        out.append(f"India VIX at {vix['ltp']:.1f}: the market is {mood}"
                   f"{' and getting jumpier' if (vix.get('change_pct') or 0) > 5 else ''}.")
    if picks.get("rows"):
        out.append(f"Your latest scan found {picks.get('longs', 0)} buy ideas; strongest: "
                   + ", ".join(r["symbol"] for r in picks["rows"][:3]) + ".")
    big = [h for h in headlines if h.get("important")][:1]
    if big:
        out.append(f"Watch: {big[0]['title']}")
    return out[:5]


def _llm_text(prompt: str, timeout_s: float = 25.0) -> str | None:
    result: list[str] = []

    def run():
        try:
            from src.providers.llm import build_llm

            msg = build_llm().invoke(prompt)
            text = getattr(msg, "content", msg)
            if isinstance(text, list):
                text = " ".join(str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in text)
            result.append(str(text))
        except Exception as exc:  # noqa: BLE001 - no model configured, or it failed
            logger.info("brief AI summary skipped: %s", exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    return result[0] if result else None


def ai_insight(facts: list[str], headlines: list[dict[str, Any]], hour_key: str) -> list[str] | None:
    """Three short sentences from a configured model, once an hour; None when no model answers."""
    def make():
        prompt = (
            "You write a 3-bullet morning brief for an Indian retail trader who paper-trades NSE stocks. "
            "Use only the facts and headlines below. Plain words, each bullet under 25 words, no buy/sell advice, "
            "no hype, mention what to watch today. Reply with exactly 3 lines starting with '- '.\n\nFacts:\n"
            + "\n".join(f"- {f}" for f in facts)
            + "\n\nHeadlines:\n" + "\n".join(f"- {h['title']} ({h['source']})" for h in headlines[:10])
        )
        text = _llm_text(prompt)
        if not text:
            return None
        lines = [re.sub(r"^[-*•\d.)\s]+", "", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if 8 <= len(ln) <= 300][:3]
        return lines or None

    return _cached(f"ai:{hour_key}", 3600, make)


# --- the whole brief ----------------------------------------------------------------------------------


def brief(watch: list[str] | None = None) -> dict[str, Any]:
    from src.live.profit_guard.feeds import live_quotes

    now = now_ist()
    status = market_status(now)
    owner = live_quotes._owner()
    live = owner and live_quotes.active()
    session_live = live and live_quotes.session_has_started(now)
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_idx = pool.submit(index_rows, live)
        f_br = pool.submit(lambda: breadth(session_live))
        f_news = pool.submit(lambda: news(watch))
        idx = f_idx.result()
        try:
            br = f_br.result()
        except Exception as exc:  # noqa: BLE001
            logger.info("breadth failed: %s", exc)
            br = {}
        try:
            heads = f_news.result()
        except Exception as exc:  # noqa: BLE001
            logger.info("news failed: %s", exc)
            heads = []
    try:
        picks = top_picks()
    except Exception as exc:  # noqa: BLE001
        logger.info("top picks failed: %s", exc)
        picks = {"rows": []}
    facts = rule_insight(idx, br, picks, heads, status)
    ai = ai_insight(facts, heads, now.strftime("%Y-%m-%d %H")) if owner else None  # owner's desk only (model cost)
    return {
        "generated_at": now.isoformat(),
        "status": status,
        "live": bool(live and any(r.get("source") == "kite" for r in idx)),
        "indices": idx,
        "insight": {"bullets": ai or facts, "by": "ai" if ai else "rules"},
        "breadth": br,
        "picks": picks,
        "news": heads,
        "disclaimer": "Paper-trading ideas from the desk's own signals, not investment advice.",
    }
