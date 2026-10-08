#!/usr/bin/env python3
"""Morning AlphaDesk desk runner for cron / scheduled-runs.

Enable with:
  VIBE_TRADING_ENABLE_SCHEDULER=1 vibe-trading serve --port 8899

Then POST /scheduled-runs with schedule \"0 4 * * 1-5\" (≈09:30 IST),
or call POST /api/desk/morning.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MORNING_PROMPT = """
You are AlphaDesk India morning desk.
1) POST /api/desk/survival/heartbeat (or assume scorecard refreshed)
2) Load ~/.vibe-trading/alphadesk/portfolio/latest.json
3) Run PM hygiene mentally: junk/stubs first, tax-aware trims for liquidity, keep core
4) Debate ONLY holdings + 10 watchlist (RELIANCE,HDFCBANK,TCS,INFY,TATAMOTORS,SBIN,ICICIBANK,LT,SUNPHARMA,BAJFINANCE)
5) Skip DEAD engines. Prefer Arena winner. No live API orders — paper / manual tickets only.
6) Output table: symbol | engine | action | confidence | sleeve note | one-line why
""".strip()


def run_morning_desk(
    *,
    target_net: float = 0.0,
    force: bool = False,
) -> dict[str, Any]:
    """Heartbeat + PM plan on capped universe + persist morning report.

    Anti-thrash: skips if morning_desk ran within cooldown unless ``force``.
    """
    from src.alphadesk.engines.debate import capped_universe
    from src.alphadesk.engines.portfolio_manager import build_action_plan
    from src.alphadesk.idle import mark_run, should_sleep
    from src.alphadesk.live_unlock import status_all
    from src.alphadesk.store import alphadesk_root, load_portfolio_snapshot
    from src.alphadesk.survival import heartbeat
    from src.alphadesk.tax_ytd import summary as tax_summary
    from src.alphadesk.token_budget import status as budget_status

    idle = should_sleep("morning_desk", force=force)
    if idle.get("sleep"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "anti_thrash",
            "seconds_left": idle.get("seconds_left"),
            "last": idle.get("last"),
            "prompt": MORNING_PROMPT,
        }

    hb = heartbeat()
    snap = load_portfolio_snapshot()
    holdings = (snap or {}).get("holdings") or []
    universe = capped_universe(holdings)
    pm_plan: dict[str, Any] | None = None
    if holdings:
        try:
            pm_plan = build_action_plan(holdings, target_net=float(target_net or 0))
        except Exception as exc:
            pm_plan = {"error": str(exc)}

    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat": {
            "halted": hb.get("halted"),
            "engines": hb.get("engines"),
            "book": hb.get("book"),
        },
        "universe": universe,
        "holding_count": len(holdings),
        "pm_plan": pm_plan,
        "live": status_all(),
        "tax_ytd": tax_summary(),
        "token_budget": budget_status(),
        "prompt": MORNING_PROMPT,
    }

    reports = alphadesk_root() / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = reports / f"morning_{day}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    mark_run("morning_desk", meta={"path": str(path), "universe_n": len(universe)})

    return {
        "ok": True,
        "skipped": False,
        "path": str(path),
        "universe": universe,
        "holding_count": len(holdings),
        "halted": hb.get("halted"),
        "pm_hygiene_exits": (pm_plan or {}).get("hygiene_exits") if isinstance(pm_plan, dict) else None,
        "tax_ytd": report["tax_ytd"],
        "token_budget": report["token_budget"],
        "prompt": MORNING_PROMPT,
    }


def main() -> None:
    result = run_morning_desk()
    print(json.dumps({k: result.get(k) for k in ("ok", "skipped", "path", "halted", "universe")}, indent=2))
    print(MORNING_PROMPT)


if __name__ == "__main__":
    main()
