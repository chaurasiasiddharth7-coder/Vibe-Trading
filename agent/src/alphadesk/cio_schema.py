"""Pydantic models for AlphaDesk CIO trade directives."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Action = Literal["BUY", "SELL", "HOLD", "WATCH", "TRIM"]
SurvivalStatus = Literal["ALIVE", "PROBATION", "DEAD"]


class Agreement(BaseModel):
    ta: Literal["bull", "bear", "neutral"] = "neutral"
    kronos: Literal["bull", "bear", "neutral"] = "neutral"
    fundamental: Literal["ok", "weak", "avoid"] = "ok"
    aligned: bool = False


class PortfolioFit(BaseModel):
    current_weight_pct: float | None = None
    post_trade_weight_pct: float | None = None
    cash_impact_inr: float | None = None
    est_tax_inr: float | None = None
    net_proceeds_inr: float | None = None


class CioTradePlan(BaseModel):
    """Strict CIO JSON emitted by india_fund_committee / Arena engines."""

    action: Action
    confidence_score: int = Field(ge=0, le=100)
    entry_range: str = ""
    target_price: float
    stop_loss: float
    risk_reward_ratio: str = "1:2"
    position_size_percent: float = Field(default=0.0, ge=0, le=5)
    holding_horizon: str = ""
    executive_summary: str
    conviction_drivers: list[str] = Field(default_factory=list)
    disconfirming_evidence: list[str] = Field(default_factory=list)
    invalidation: str = ""
    agreement: Agreement = Field(default_factory=Agreement)
    portfolio_fit: PortfolioFit = Field(default_factory=PortfolioFit)
    do_not_trade_if: list[str] = Field(default_factory=list)
    engine_id: str = ""
    arena_score: int = Field(default=0, ge=0, le=100)
    survival_status: SurvivalStatus = "ALIVE"

    @field_validator("position_size_percent")
    @classmethod
    def _cap_size(cls, v: float) -> float:
        return min(float(v), 5.0)


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
# DeepSeek/NIM sometimes wraps text in DSML / fake tool-call tags.
_TAG_RE = re.compile(
    r"</?\|?(?:DSML|tool)(?:\|[^>]*)?[^>]*>",
    re.IGNORECASE,
)


def _strip_tool_markup(text: str) -> str:
    """Remove DSML / fake tool-call *tags* without deleting nested JSON."""
    cleaned = _TAG_RE.sub(" ", text or "")
    cleaned = re.sub(r"<\|DSML\|[^\n>]*", " ", cleaned)
    return cleaned


def apply_ta_kronos_gate(plan: CioTradePlan) -> CioTradePlan:
    """TA vs Kronos disagreement blocks fresh BUY."""
    if (
        plan.action == "BUY"
        and plan.agreement.ta != plan.agreement.kronos
        and plan.agreement.ta != "neutral"
        and plan.agreement.kronos != "neutral"
    ):
        return plan.model_copy(update={"action": "WATCH"})
    return plan


def apply_multi_engine_gate(
    plan: CioTradePlan,
    *,
    peer_actions: set[str],
) -> CioTradePlan:
    """If peers disagree on BUY, force WATCH."""
    plan = apply_ta_kronos_gate(plan)
    if plan.action == "BUY" and "BUY" in peer_actions:
        others = peer_actions - {"BUY"}
        if others & {"SELL", "TRIM", "HOLD", "WATCH"}:
            # Only downgrade when a peer explicitly opposes with SELL/TRIM
            if others & {"SELL", "TRIM"}:
                return plan.model_copy(update={"action": "WATCH"})
    if plan.action == "BUY" and len(peer_actions) >= 2 and "BUY" not in (
        peer_actions - {plan.action}
    ):
        # sole BUY among mixed peers including WATCH/HOLD — keep if aligned
        if not plan.agreement.aligned and "SELL" in peer_actions:
            return plan.model_copy(update={"action": "WATCH"})
    return plan


def parse_cio_json(text: str) -> CioTradePlan:
    """Parse CIO JSON from raw LLM text (strips markdown fences / DSML junk)."""
    raw = _strip_tool_markup((text or "").strip())
    match = _FENCE_RE.search(raw)
    if match:
        raw = match.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in CIO output")
    data: dict[str, Any] = json.loads(raw[start : end + 1])
    plan = CioTradePlan.model_validate(data)
    return apply_ta_kronos_gate(plan)
