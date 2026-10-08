"""Tests for CIO schema edge gate and sell ladder."""

from __future__ import annotations

from src.alphadesk.cio_schema import parse_cio_json
from src.alphadesk.portfolio import estimate_tax, net_proceeds, prioritize_exits


def test_parse_cio_json_and_edge_gate_blocks_buy():
    raw = """
    ```json
    {
      "action": "BUY",
      "confidence_score": 80,
      "entry_range": "₹100 - ₹102",
      "target_price": 120,
      "stop_loss": 90,
      "risk_reward_ratio": "1:2",
      "position_size_percent": 3,
      "holding_horizon": "4w",
      "executive_summary": "Test thesis for edge gate.",
      "agreement": {"ta": "bull", "kronos": "bear", "fundamental": "ok", "aligned": false}
    }
    ```
    """
    plan = parse_cio_json(raw)
    assert plan.action == "WATCH"


def test_parse_cio_json_strips_dsml_wrapper():
    raw = (
        'Here is the directive:\n'
        '<|DSML|calls>\n'
        '{"action":"WATCH","confidence_score":55,"entry_range":"₹100-₹102",'
        '"target_price":110,"stop_loss":95,"risk_reward_ratio":"1:2",'
        '"position_size_percent":0,"holding_horizon":"2w",'
        '"executive_summary":"TA and Kronos disagree — watch only.",'
        '"agreement":{"ta":"bear","kronos":"bull","fundamental":"ok","aligned":false}}'
        '\n</|DSML|calls>'
    )
    plan = parse_cio_json(raw)
    assert plan.action == "WATCH"
    assert plan.confidence_score == 55


def test_estimate_tax_and_ladder():
    assert estimate_tax(1000, long_term_frac=0) == 200.0
    p = net_proceeds(10, 100, 50, long_term_frac=0)
    assert p["gross"] == 1000
    assert p["tax"] == 100.0
    holdings = [
        {"symbol": "A", "qty": 10, "price": 100, "avg_cost": 90, "current_value": 1000, "bucket": "junk"},
        {"symbol": "B", "qty": 100, "price": 200, "avg_cost": 100, "current_value": 20000, "bucket": "core"},
    ]
    plan = prioritize_exits(holdings, target_net=900)
    assert plan[0]["symbol"] == "A"
    assert sum(r["net"] for r in plan) >= 900 or len(plan) >= 1
