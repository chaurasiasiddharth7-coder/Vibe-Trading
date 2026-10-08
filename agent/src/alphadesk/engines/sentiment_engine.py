"""Sentiment tilt engine — news lexicon (FinBERT optional) → CioTradePlan.

Tilt only: never hard-overrides PM/BR. Max position_size_percent 1.0.
Fails closed to WATCH when news unavailable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.alphadesk.cio_schema import Agreement, CioTradePlan
from src.alphadesk.survival import is_engine_tradable, load_engine

logger = logging.getLogger(__name__)

ENGINE_ID = "sentiment"

_POS = {
    "surge",
    "rally",
    "beat",
    "growth",
    "record",
    "upgrade",
    "profit",
    "strong",
    "outperform",
    "bullish",
    "wins",
    "approve",
    "expansion",
    "deal",
    "order",
    "buyback",
    "dividend",
}
_NEG = {
    "fall",
    "drop",
    "miss",
    "loss",
    "fraud",
    "probe",
    "downgrade",
    "weak",
    "cut",
    "slash",
    "bearish",
    "default",
    "ban",
    "penalty",
    "crash",
    "selloff",
    "warning",
    "layoff",
}


def score_headlines(texts: list[str]) -> dict[str, Any]:
    """Lexicon sentiment in [-1, 1]; optional FinBERT average if installed."""
    if not texts:
        return {"score": 0.0, "method": "empty", "n": 0}

    # Try FinBERT once (optional dependency)
    finbert_scores: list[float] = []
    try:
        finbert_scores = _finbert_scores(texts[:8])
    except Exception as exc:
        logger.debug("finbert unavailable: %s", exc)

    if finbert_scores:
        score = sum(finbert_scores) / len(finbert_scores)
        return {
            "score": round(max(-1.0, min(1.0, score)), 4),
            "method": "finbert",
            "n": len(finbert_scores),
        }

    pos = neg = 0
    for t in texts:
        tokens = set(re.findall(r"[a-z]+", t.lower()))
        pos += len(tokens & _POS)
        neg += len(tokens & _NEG)
    total = pos + neg
    if total == 0:
        return {"score": 0.0, "method": "lexicon", "n": len(texts)}
    score = (pos - neg) / total
    return {"score": round(score, 4), "method": "lexicon", "n": len(texts), "pos": pos, "neg": neg}


_FINBERT_PIPELINE = None


def _get_finbert_pipeline():
    global _FINBERT_PIPELINE
    if _FINBERT_PIPELINE is None:
        from transformers import pipeline  # type: ignore
        _FINBERT_PIPELINE = pipeline("sentiment-analysis", model="ProsusAI/finbert")
    return _FINBERT_PIPELINE


def _finbert_scores(texts: list[str]) -> list[float]:
    pipe = _get_finbert_pipeline()
    out: list[float] = []
    for t in texts:
        r = pipe(t[:512])[0]
        label = str(r.get("label", "")).lower()
        conf = float(r.get("score") or 0)
        if "positive" in label:
            out.append(conf)
        elif "negative" in label:
            out.append(-conf)
        else:
            out.append(0.0)
    return out


def fetch_headlines(symbol: str, *, limit: int = 12) -> list[str]:
    """Yahoo search-news for NSE tickers (StockNewsTool lacks .NS routing)."""
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    try:
        from src.tools.stock_news_tool import _fetch_yahoo_news

        articles = _fetch_yahoo_news(bare, limit)
        texts = []
        for a in articles:
            title = str(a.get("title") or "")
            snippet = str(a.get("summary") or a.get("snippet") or "")
            texts.append(f"{title} {snippet}".strip())
        return [t for t in texts if t]
    except Exception as exc:
        logger.warning("news fetch failed for %s: %s", symbol, exc)
        return []


def proposal_for_symbol(
    symbol: str,
    *,
    price: float = 0.0,
    headlines: list[str] | None = None,
) -> CioTradePlan:
    if not is_engine_tradable(ENGINE_ID):
        raise RuntimeError("sentiment engine DEAD or sleeve depleted")

    state = load_engine(ENGINE_ID)
    texts = headlines if headlines is not None else fetch_headlines(symbol)
    scored = score_headlines(texts)
    s = float(scored["score"])

    # Tilt bands — never strong conviction
    if abs(s) < 0.15 or not texts:
        action = "WATCH"
        conf = 40
        summary = (
            f"Sentiment neutral/unavailable ({scored.get('method')}, n={scored.get('n')}). "
            "Tilt only — defer to PM/BR."
        )
    elif s >= 0.15:
        action = "BUY"
        conf = min(65, 50 + int(abs(s) * 20))
        summary = (
            f"Bullish news tilt score={s:.2f} via {scored.get('method')}. "
            "Soft ADD bias only (max 1% size)."
        )
    else:
        action = "SELL"
        conf = min(65, 50 + int(abs(s) * 20))
        summary = (
            f"Bearish news tilt score={s:.2f} via {scored.get('method')}. "
            "Soft TRIM bias only (max 1% size)."
        )

    px = price or 100.0
    return CioTradePlan(
        action=action,  # type: ignore[arg-type]
        confidence_score=conf,
        entry_range=f"₹{px:,.2f}",
        target_price=round(px * (1.04 if action == "BUY" else 0.96), 2),
        stop_loss=round(px * (0.98 if action == "BUY" else 1.02), 2),
        risk_reward_ratio="1:2",
        position_size_percent=0.0 if action == "WATCH" else 1.0,
        holding_horizon="news-tilt 1-4 weeks",
        executive_summary=summary,
        conviction_drivers=[f"sentiment:{scored.get('method')}", f"score={s}"],
        disconfirming_evidence=["tilt_not_standalone_edge"],
        invalidation="Ignore if PM hygiene EXIT or BR gate opposing hard SELL/BUY",
        agreement=Agreement(
            ta="neutral",
            kronos="neutral",
            fundamental="ok" if s >= 0 else "weak",
            aligned=True,
        ),
        do_not_trade_if=["no_headlines", "engine_dead", "override_pm_exit"],
        engine_id=ENGINE_ID,
        arena_score=0,
        survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
    )


def diagnose(symbol: str) -> dict[str, Any]:
    texts = fetch_headlines(symbol)
    scored = score_headlines(texts)
    return {"symbol": symbol, "headlines": texts[:5], **scored}
