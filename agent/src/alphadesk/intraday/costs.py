"""NSE intraday equity (MIS) round-trip costs. Every rate is configurable.

[OWNER-PENDING] confirm the defaults against your broker's tariff and the current statutory
schedule; they match a discount broker's intraday equity charges. A run should record the
``CostConfig`` it used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class CostConfig:
    brokerage_pct: float = 0.0003  # 0.03% of turnover per executed order ...
    brokerage_cap: float = 20.0  # ... capped at Rs 20 per order
    stt_sell_pct: float = 0.00025  # 0.025% on the sell side
    exchange_pct: float = 0.0000297  # NSE transaction charge, both sides
    sebi_pct: float = 0.000001  # Rs 10 per crore, both sides
    stamp_buy_pct: float = 0.00003  # 0.003% on the buy side
    gst: float = 0.18  # on brokerage + exchange + SEBI
    slippage_bps: float = 2.0  # per side, against you

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_COSTS = CostConfig()


def trade_costs(side: str, entry, exit_, qty: float, cfg: CostConfig = DEFAULT_COSTS):
    """Rupee cost of a round trip (excluding slippage), scalar or numpy array of exits.

    ``side`` is ``long`` (buy then sell) or ``short`` (sell then buy). Slippage is added by
    :func:`slippage_inr`.
    """
    buy, sell = (entry, exit_) if side == "long" else (exit_, entry)
    buy_v, sell_v = buy * qty, sell * qty
    brokerage = np.minimum(cfg.brokerage_pct * buy_v, cfg.brokerage_cap) + np.minimum(
        cfg.brokerage_pct * sell_v, cfg.brokerage_cap)
    exchange = cfg.exchange_pct * (buy_v + sell_v)
    sebi = cfg.sebi_pct * (buy_v + sell_v)
    gst = cfg.gst * (brokerage + exchange + sebi)
    return brokerage + exchange + sebi + gst + cfg.stt_sell_pct * sell_v + cfg.stamp_buy_pct * buy_v


def slippage_inr(entry, exit_, qty: float, cfg: CostConfig = DEFAULT_COSTS):
    return (entry + exit_) * qty * cfg.slippage_bps / 10_000


def net_r(side: str, entry: float, exit_, risk: float, risk_inr: float,
          cfg: CostConfig = DEFAULT_COSTS):
    """Net R of a trade sized so a stop-out loses ``risk_inr`` before costs.

    R = (gross P&L - costs - slippage) / risk_inr. Works on a scalar or an array of exits.
    """
    qty = max(int(risk_inr // risk), 1)
    sign = 1.0 if side == "long" else -1.0
    gross = sign * (np.asarray(exit_, dtype=float) - entry) * qty
    net = gross - trade_costs(side, entry, exit_, qty, cfg) - slippage_inr(entry, exit_, qty, cfg)
    return net / (qty * risk)
