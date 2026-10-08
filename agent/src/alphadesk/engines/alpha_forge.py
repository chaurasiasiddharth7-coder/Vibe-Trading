"""LLM/zoo alpha forge — rank India-capable alphas on one symbol; gate + SDM.

Panel contract matches factors Registry: dict of wide DataFrames
(index=date, columns=instrument).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.alphadesk.cio_schema import Agreement, CioTradePlan
from src.alphadesk.survival import is_engine_tradable, load_engine

logger = logging.getLogger(__name__)

ENGINE_ID = "alpha_forge"
MIN_ABS_IC = 0.03
MIN_BARS = 60


def _wide_panel(df: pd.DataFrame, symbol: str) -> dict[str, pd.DataFrame]:
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    lower = {str(c).lower(): c for c in df.columns}

    def series(name: str) -> pd.Series:
        return df[lower[name]].astype(float)

    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    panel: dict[str, pd.DataFrame] = {}
    for col in ("open", "high", "low", "close", "volume"):
        if col not in lower and col != "volume":
            continue
        if col == "volume" and "volume" not in lower:
            s = pd.Series(1.0, index=idx)
        else:
            s = series(col)
            s.index = idx
        panel[col] = pd.DataFrame({bare: s.values}, index=idx)
    # amount approx
    if "close" in panel and "volume" in panel:
        panel["amount"] = panel["close"] * panel["volume"]
    if "vwap" not in panel and "close" in panel:
        panel["vwap"] = panel["close"].copy()
    return panel


def _ic_single(factor_wide: pd.DataFrame, close_wide: pd.DataFrame, horizon: int = 5) -> float:
    """IC for single-instrument panel via time-series corr of factor vs fwd return."""
    if factor_wide.empty or close_wide.empty:
        return 0.0
    col = factor_wide.columns[0]
    f = factor_wide[col]
    c = close_wide[col]
    fwd = c.pct_change(horizon).shift(-horizon)
    aligned = pd.concat([f, fwd], axis=1).dropna()
    if len(aligned) < 30:
        return 0.0
    a = aligned.iloc[:, 0].rank()
    b = aligned.iloc[:, 1].rank()
    if float(a.std()) == 0 or float(b.std()) == 0:
        return 0.0
    return float(a.corr(b))


def rank_alphas(
    df: pd.DataFrame,
    symbol: str,
    *,
    zoo: str = "qlib158",
    limit: int = 12,
) -> list[dict[str, Any]]:
    from src.factors.registry import Registry

    reg = Registry()
    ids = reg.list(zoo=zoo, universe="equity_in")[:limit]
    panel = _wide_panel(df, symbol)
    close = panel.get("close")
    if close is None:
        return []
    ranked: list[dict[str, Any]] = []
    for alpha_id in ids:
        try:
            values = reg.compute(alpha_id, panel)
            ic = _ic_single(values, close)
            latest_col = values.columns[0]
            latest_s = values[latest_col].dropna()
            latest = float(latest_s.iloc[-1]) if len(latest_s) else 0.0
            ranked.append(
                {
                    "alpha_id": alpha_id,
                    "ic": round(ic, 4),
                    "latest": latest,
                    "pass": abs(ic) >= MIN_ABS_IC,
                }
            )
        except Exception as exc:
            logger.debug("alpha %s skip: %s", alpha_id, exc)
            continue
    ranked.sort(key=lambda r: abs(float(r["ic"])), reverse=True)
    return ranked


def maybe_register_sdm(alpha_id: str, ic: float) -> dict[str, Any] | None:
    try:
        from src.strategy_store.models import Artifact, ArtifactStatus, ArtifactType
        from src.strategy_store._shared import get_store

        store = get_store()
        artifact = Artifact(
            id="",
            type=ArtifactType.FACTOR,
            name=f"alphadesk:{alpha_id}",
            universe="equity_in",
            status=ArtifactStatus.CREATED,
            theme=("momentum",),
            signal_definition=f"AlphaDesk forge IC={ic}",
        )
        artifact_id = store.register_artifact(artifact)
        return {"id": artifact_id, "name": artifact.name}
    except Exception as exc:
        logger.debug("sdm register skipped: %s", exc)
        return None


def proposal_for_symbol(
    symbol: str,
    df: pd.DataFrame,
    *,
    price: float = 0.0,
) -> CioTradePlan:
    if not is_engine_tradable(ENGINE_ID):
        raise RuntimeError("alpha_forge engine DEAD or sleeve depleted")
    state = load_engine(ENGINE_ID)
    lower = {str(c).lower(): c for c in df.columns}
    px = price
    if not px:
        px = float(df[lower["close"]].iloc[-1])

    if len(df) < MIN_BARS:
        return CioTradePlan(
            action="WATCH",
            confidence_score=35,
            entry_range=f"₹{px:,.2f}",
            target_price=px,
            stop_loss=px,
            executive_summary="Alpha forge: insufficient history",
            engine_id=ENGINE_ID,
            survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
        )

    ranked = rank_alphas(df, symbol)
    top = ranked[0] if ranked else None
    if not top or not top.get("pass"):
        return CioTradePlan(
            action="WATCH",
            confidence_score=42,
            entry_range=f"₹{px:,.2f}",
            target_price=round(px * 1.05, 2),
            stop_loss=round(px * 0.97, 2),
            executive_summary=(
                "Alpha forge: no India-zoo alpha cleared IC gate "
                f"(|IC|>={MIN_ABS_IC}). Top="
                f"{top['alpha_id'] if top else 'none'} "
                f"IC={top['ic'] if top else 'n/a'}."
            ),
            conviction_drivers=["alpha_forge_gate"],
            disconfirming_evidence=["ic_gate_fail"],
            engine_id=ENGINE_ID,
            survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
        )

    ic = float(top["ic"])
    latest = float(top["latest"])
    signal = ic * (1.0 if latest >= 0 else -1.0)
    if signal > 0:
        action = "BUY"
    elif signal < 0:
        action = "SELL"
    else:
        action = "WATCH"

    maybe_register_sdm(str(top["alpha_id"]), ic)

    return CioTradePlan(
        action=action,  # type: ignore[arg-type]
        confidence_score=min(70, 50 + int(abs(ic) * 100)),
        entry_range=f"₹{px:,.2f}",
        target_price=round(px * (1.06 if action == "BUY" else 0.94), 2),
        stop_loss=round(px * (0.97 if action == "BUY" else 1.03), 2),
        risk_reward_ratio="1:2",
        position_size_percent=0.0 if action == "WATCH" else 1.5,
        holding_horizon="factor 2-8 weeks",
        executive_summary=(
            f"Alpha forge: {top['alpha_id']} IC={ic:.3f} latest={latest:.4f}. "
            "Gated zoo factor — confirm with PM/BR."
        ),
        conviction_drivers=[str(top["alpha_id"]), f"ic={ic:.3f}"],
        disconfirming_evidence=[],
        invalidation="Disable on SDM decay or IC flip",
        agreement=Agreement(
            ta="bull" if action == "BUY" else ("bear" if action == "SELL" else "neutral"),
            kronos="neutral",
            fundamental="ok",
            aligned=True,
        ),
        do_not_trade_if=["ic_gate_fail", "sdm_decayed", "engine_dead"],
        engine_id=ENGINE_ID,
        arena_score=0,
        survival_status=str(state.get("status") or "ALIVE"),  # type: ignore[arg-type]
    )


def diagnose(df: pd.DataFrame, symbol: str) -> dict[str, Any]:
    return {"ranked": rank_alphas(df, symbol)[:5]}
