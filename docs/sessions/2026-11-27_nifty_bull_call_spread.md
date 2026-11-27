# NIFTY bull call spread: AlgoBulls original vs trend-filtered rebuild

Goal: check whether the AlgoBulls Odyssey "Nifty 50 Bull Call Spread"
(ABODSYM084) is worth running, and whether adding a trend filter and dropping
its short-leg stop-loss makes it better.

Code: `BullCallSpreadEngine` in
`agent/src/skills/options-strategy/example_signal_engine.py`, study script
`agent/scripts/bull_call_spread_backtest.py`, tests in
`agent/tests/test_options_strategy_engines.py`.

## Bottom line

- **None of the four versions beats just holding NIFTY**, in either period.
  Buy-and-hold made 14.0% a year in 2017-2021 and 7.9% in 2022-2026.
- **The results flip between periods.** The AlgoBulls-style version (enter
  every day, stop on the short leg) was best in 2017-2021 (+8.9% a year) and
  worst in 2022-2026 (-9.7%, -49% drawdown). The trend-filtered rebuild was
  the reverse: -2.7% then +2.3%. A strategy with a real edge shouldn't swing
  like that, so neither has one here.
- **The short-leg stop didn't hurt the way expected.** In 2017-2021 the
  versions with it did better than the ones without it. Removing it only
  helped in 2022-2026, and only together with the trend filter and the
  cooldown.
- **Costs decide everything.** The rebuild goes from +3.5% a year with no
  costs to -10% a year at 2% of premium per side. Every leg pays costs on the
  full premium of a deep ITM call, which is several times the spread's debit.
- **Verdict: don't pay for the Odyssey strategy and don't move either version
  to live.** At most, paper-trade the rebuild on Vibe-Trading's Strategies
  page with the live switch locked, and drop it if it keeps bleeding.

## Setup

| Item | Value |
|---|---|
| Data | NIFTY 50 index, daily OHLC built from 1-minute candles, Apr 2017 → 27 Nov 2026 |
| Periods | 2017-07 → 2021-12 and 2022-01 → 2026-11, parameters frozen (no tuning) |
| Structure | long ~0.65Δ call, short ~0.35Δ call, ~7-day expiry |
| Exits | 60% of max profit, 50% of debit lost, or 1 day before expiry |
| Sizing | 5% of capital per spread, compounding; ₹5 lakh start; margin off (long leg covers short) |
| Pricing | Black-Scholes on 30-day historical vol, `iv_skew=-0.3`, r = 6.5%, next-bar-close fills |
| Costs | 0.5% of premium per side (brokerage + STT + fees + ~1 point slippage) |

Variants:

| Variant | Entry | Short-leg stop | Cooldown after loss |
|---|---|---|---|
| `algobulls_style` | every day | yes (short call +50%) | none |
| `filter_only` | close > 20-EMA | yes | none |
| `no_short_stop_only` | every day | no | 2 bars |
| `improved` | close > 20-EMA | no | 2 bars |

## Variants (5% of capital per spread, 0.5% cost per side)

| Period | Variant | CAGR | Sharpe | MaxDD | WorstMo | Trades | WinRate | AvgWin/AvgLoss | LossStreak | Rejects |
|---|---|---|---|---|---|---|---|---|---|---|
| 2017-2021 | nifty_buy_and_hold | 14.0% | 0.80 | -38.2% | -23.7% |  |  |  |  |  |
| 2017-2021 | algobulls_style | 8.9% | 0.53 | -23.2% | -10.5% | 333 | 56.2% | 0.95 | 6 | 0 |
| 2017-2021 | filter_only | 2.3% | 0.22 | -31.4% | -11.4% | 226 | 56.6% | 0.85 | 5 | 0 |
| 2017-2021 | no_short_stop_only | -3.2% | -0.04 | -43.5% | -14.9% | 229 | 53.7% | 0.89 | 6 | 0 |
| 2017-2021 | improved | -2.7% | -0.02 | -34.7% | -10.2% | 176 | 54.0% | 0.88 | 6 | 0 |
| 2022-2026 | nifty_buy_and_hold | 7.9% | 0.63 | -16.2% | -11.1% |  |  |  |  |  |
| 2022-2026 | algobulls_style | -9.7% | -0.40 | -48.6% | -15.9% | 368 | 49.5% | 0.98 | 6 | 0 |
| 2022-2026 | filter_only | -12.2% | -0.42 | -62.5% | -19.4% | 221 | 50.7% | 0.90 | 8 | 0 |
| 2022-2026 | no_short_stop_only | -11.7% | -0.34 | -65.7% | -20.0% | 245 | 51.4% | 0.90 | 7 | 0 |
| 2022-2026 | improved | 2.3% | 0.21 | -25.3% | -12.1% | 171 | 55.0% | 0.92 | 4 | 0 |

## Improved variant, full period, sensitivity

| Test | CAGR | Sharpe | MaxDD | WorstMo | Trades | WinRate | AvgWin/AvgLoss | LossStreak | Rejects |
|---|---|---|---|---|---|---|---|---|---|
| ema_span=10 | 1.6% | 0.18 | -30.4% | -11.9% | 367 | 53.1% | 0.97 | 6 | 0 |
| ema_span=20 | -0.1% | 0.10 | -39.9% | -12.4% | 348 | 54.6% | 0.89 | 6 | 0 |
| ema_span=50 | -2.0% | 0.04 | -58.9% | -19.7% | 350 | 53.7% | 0.91 | 6 | 0 |
| cost=0%/side | 3.5% | 0.27 | -34.3% | -11.2% | 346 | 54.9% | 0.87 | 6 | 0 |
| cost=1%/side | -4.0% | -0.10 | -53.1% | -13.0% | 348 | 54.6% | 0.90 | 6 | 0 |
| cost=2%/side | -10.1% | -0.51 | -72.8% | -11.2% | 348 | 54.6% | 0.92 | 6 | 0 |
| iv_skew=0.0 | 1.5% | 0.18 | -38.0% | -13.0% | 344 | 53.8% | 0.95 | 6 | 0 |
| iv_skew=-0.6 | -2.9% | -0.08 | -45.9% | -9.6% | 347 | 54.5% | 0.85 | 6 | 0 |


## Caveats

- **Daily bars, not 15-minute.** The original is intraday. This tests the
  structure, the filter and the exits, not intraday entry timing.
- **Synthetic option prices.** No NIFTY option-chain history was available,
  so every option is priced from the index's own realized vol. Real IV is
  usually above realized vol, and the real smile differs. Re-run on real chain
  data (Kite historical) before trusting any number here.
- Weekly expiry is modeled as the first trading day 7+ days out, not the
  actual NSE expiry weekday (Thursday until Aug 2025, Tuesday since). Lot
  sizes aren't modeled (`lot_size=1`).
- One underlying, one parameter set, about 350 trades in total.
