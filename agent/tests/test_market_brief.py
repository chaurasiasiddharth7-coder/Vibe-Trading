"""The Today brief: index fallbacks, breadth, top picks, news ranking and the insight text."""

from __future__ import annotations

import time

import pytest

from src.alphadesk import market_brief as MB

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>RBI keeps repo rate unchanged, signals inflation watch</title><link>https://x.test/a</link>
<pubDate>Fri, 20 Nov 2026 04:30:00 GMT</pubDate></item>
<item><title><![CDATA[Celebrity buys a new car]]></title><link>https://x.test/b</link>
<pubDate>Fri, 20 Nov 2026 05:00:00 GMT</pubDate></item>
<item><title>No link here</title></item>
</channel></rss>"""
NOW = 1795152600.0  # 2026-11-20 05:30 UTC


@pytest.fixture(autouse=True)
def _fresh():
    MB._CACHE.clear()


def test_parse_rss_keeps_title_link_time():
    items = MB._parse_rss("ET", RSS)
    assert [i["title"] for i in items] == ["RBI keeps repo rate unchanged, signals inflation watch", "Celebrity buys a new car"]
    assert items[0]["source"] == "ET" and items[0]["ts"] == pytest.approx(NOW - 3600)


def test_news_ranks_market_movers_and_flags_watchlist():
    items = MB._parse_rss("ET", RSS) + [
        {"title": "RBI keeps repo rate unchanged; signals inflation watch", "url": "https://y.test/1", "source": "Mint", "ts": NOW - 1800},
        {"title": "LT wins big order from railways", "url": "https://y.test/2", "source": "Mint", "ts": NOW - 600},
        {"title": "Old story about markets", "url": "https://y.test/3", "source": "Mint", "ts": NOW - 3 * 86400},
    ]
    out = MB.news(["LT"], now=NOW, items=items)
    titles = [n["title"] for n in out]
    assert titles[0].startswith("RBI keeps repo rate")
    assert sorted(out[0]["sources"]) == ["ET", "Mint"]  # the same story from two outlets shows once
    lt = next(n for n in out if n["title"].startswith("LT wins"))
    assert lt["watchlist"] == ["LT"] and lt["important"]
    assert "Old story about markets" not in titles  # older than 36 hours


def test_index_rows_falls_back_to_yahoo_when_store_lags(monkeypatch):
    from src.alphadesk import watchlist

    monkeypatch.setattr(watchlist, "_eod_tables", lambda: (
        {"LT": {"ltp": 1, "prev": 1, "as_of": "2026-11-20"}},
        {"NIFTY 50": {"ltp": 22997.35, "prev": 22722.0, "as_of": "2026-10-15"}}))
    monkeypatch.setattr(MB, "_yahoo_index", lambda n: MB._row(n, 25800.6, 25514.3, as_of="2026-11-20", source="yahoo")
                        if n != "INDIA VIX" else None)
    rows = {r["symbol"]: r for r in MB.index_rows(live=False)}
    assert rows["NIFTY 50"]["ltp"] == 25800.6 and rows["NIFTY 50"]["source"] == "yahoo"
    assert rows["NIFTY BANK"]["source"] == "yahoo"
    assert "INDIA VIX" not in rows


def test_index_rows_marks_stale_store_when_yahoo_is_down(monkeypatch):
    from src.alphadesk import watchlist

    monkeypatch.setattr(watchlist, "_eod_tables", lambda: (
        {"LT": {"ltp": 1, "prev": 1, "as_of": "2026-11-20"}},
        {"NIFTY 50": {"ltp": 22997.35, "prev": 22722.0, "as_of": "2026-10-15"}}))
    monkeypatch.setattr(MB, "_yahoo_index", lambda n: None)
    rows = MB.index_rows(live=False)
    assert rows[0]["stale"] is True and rows[0]["as_of"] == "2026-10-15"


def test_breadth_from_store(monkeypatch):
    from src.alphadesk import watchlist

    stocks = {f"S{i}": {"ltp": 100 + i, "prev": 100.0, "as_of": "2026-11-20"} for i in range(-4, 8)}
    monkeypatch.setattr(watchlist, "_eod_tables", lambda: (stocks, {}))
    monkeypatch.setattr(MB, "_members", lambda: {s: ("IT" if i % 2 else "Banks") for i, s in enumerate(stocks)})
    br = MB.breadth(live=False)
    assert br["advancers"] == 7 and br["decliners"] == 4 and br["unchanged"] == 1
    assert br["gainers"][0]["symbol"] == "S7" and br["losers"][0]["symbol"] == "S-4"
    assert {s["sector"] for s in br["sectors_up"]} <= {"IT", "Banks"}


def test_top_picks_uses_latest_scan(monkeypatch):
    from src.alphadesk import board_jobs

    boards = {
        "nifty50": {"scanned_at": "2026-11-19T10:00:00", "universe": "nifty50", "long": [{"symbol": "OLD", "score": 3}]},
        "nifty200": {"scanned_at": "2026-11-20T10:00:00", "universe": "nifty200", "long": [
            {"symbol": "LT", "score": 4, "price": 4209, "entry": 4209, "stop": 4100, "target": 4400, "reasons": ["a", "b", "c", "d"]}],
            "short": [{}], "scanned": 200},
    }
    monkeypatch.setattr(board_jobs, "load_last", lambda u: boards.get(u))
    p = MB.top_picks()
    assert p["universe"] == "nifty200" and p["rows"][0]["symbol"] == "LT" and len(p["rows"][0]["reasons"]) == 3


def test_rule_insight_reads_like_a_brief():
    idx = [MB._row("NIFTY 50", 25800.6, 25514.3), MB._row("NIFTY BANK", 64635.2, 63063.0), MB._row("INDIA VIX", 12.1, 12.5)]
    br = {"universe": "Nifty 500", "advancers": 350, "decliners": 120, "unchanged": 30,
          "sectors_up": [{"sector": "Banks"}], "sectors_down": [{"sector": "IT"}]}
    picks = {"rows": [{"symbol": "LT"}, {"symbol": "TCS"}], "longs": 9}
    heads = [{"title": "RBI keeps repo rate unchanged", "important": True}]
    out = MB.rule_insight(idx, br, picks, heads, {"state": "open"})
    assert out[0] == "Nifty 50 is up +1.12% today, Bank Nifty +2.49%."
    assert "most stocks rose: 350 up, 120 down of 500. Banks led; IT lagged." in out[1]
    assert "calm" in out[2] and "LT, TCS" in out[3] and out[4].startswith("Watch: RBI")


def test_brief_survives_failures(monkeypatch):
    from src.live.profit_guard.feeds import live_quotes

    monkeypatch.setattr(live_quotes, "active", lambda: False)
    monkeypatch.setattr(live_quotes, "_owner", lambda: False)
    monkeypatch.setattr(MB, "index_rows", lambda live: [])
    monkeypatch.setattr(MB, "breadth", lambda live: (_ for _ in ()).throw(RuntimeError("store missing")))
    monkeypatch.setattr(MB, "raw_news", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(MB, "top_picks", lambda: {"rows": []})
    b = MB.brief(["LT"])
    assert b["breadth"] == {} and b["news"] == [] and b["insight"]["by"] == "rules" and b["status"]["label"]


def test_ai_insight_only_for_owner(monkeypatch):
    from src.live.profit_guard.feeds import live_quotes

    called = []
    monkeypatch.setattr(live_quotes, "active", lambda: False)
    monkeypatch.setattr(MB, "index_rows", lambda live: [])
    monkeypatch.setattr(MB, "breadth", lambda live: {})
    monkeypatch.setattr(MB, "news", lambda w: [])
    monkeypatch.setattr(MB, "top_picks", lambda: {"rows": []})
    monkeypatch.setattr(MB, "_llm_text", lambda prompt, timeout_s=25: called.append(1) or "- One\\n- Two\\n- Three")
    monkeypatch.setattr(live_quotes, "_owner", lambda: False)
    assert MB.brief()["insight"]["by"] == "rules" and not called
    monkeypatch.setattr(live_quotes, "_owner", lambda: True)
    monkeypatch.setattr(MB, "_llm_text", lambda prompt, timeout_s=25: "- Nifty rose on bank strength today.\n- VIX is calm.\n- Watch RBI policy.")
    b = MB.brief()
    assert b["insight"]["by"] == "ai" and b["insight"]["bullets"][0] == "Nifty rose on bank strength today."
