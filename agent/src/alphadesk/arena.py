"""Arena: score PM vs Debate proposals; dead engines excluded."""

from __future__ import annotations

from typing import Any

from src.alphadesk.cio_schema import CioTradePlan, apply_multi_engine_gate
from src.alphadesk.survival import is_engine_tradable, load_engine, scorecard_row


def _base_score(plan: CioTradePlan) -> float:
    score = float(plan.confidence_score)
    if plan.action in {"WATCH", "HOLD"}:
        score *= 0.85
    if plan.agreement.aligned:
        score += 8
    return score


def score_proposal(plan: CioTradePlan) -> float:
    """Weight by sleeve health and expectancy when available."""
    eid = plan.engine_id or "unknown"
    if not is_engine_tradable(eid):
        return -1e9
    state = load_engine(eid)
    row = scorecard_row(state)
    score = _base_score(plan)
    sleeve_frac = float(state.get("sleeve_inr") or 0) / max(
        float(state.get("initial_sleeve_inr") or 1), 1.0
    )
    score *= 0.5 + 0.5 * max(0.0, min(1.0, sleeve_frac))
    exp = row.get("expectancy_r")
    if exp is not None:
        score += max(-15.0, min(15.0, float(exp) * 10))
    if state.get("status") == "PROBATION":
        score *= 0.7
    return score


def pick_winner(proposals: list[CioTradePlan]) -> tuple[CioTradePlan | None, list[dict[str, Any]]]:
    """Return winning plan (edge-gated) and vote ledger."""
    votes: list[dict[str, Any]] = []
    scored: list[tuple[float, CioTradePlan]] = []
    for plan in proposals:
        s = score_proposal(plan)
        votes.append(
            {
                "engine_id": plan.engine_id,
                "action": plan.action,
                "confidence_score": plan.confidence_score,
                "arena_score": round(s, 2),
                "survival_status": plan.survival_status,
                "excluded": s <= -1e8,
            }
        )
        if s > -1e8:
            scored.append((s, plan))

    if not scored:
        return None, votes

    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best = scored[0]
    # Multi-engine disagreement on BUY → WATCH
    actions = {p.action for _, p in scored}
    gated = apply_multi_engine_gate(best, peer_actions=actions)
    gated = gated.model_copy(
        update={
            "arena_score": int(max(0, min(100, round(best_score)))),
            "survival_status": load_engine(gated.engine_id or "pm").get("status", "ALIVE"),
        }
    )
    return gated, votes
