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
  to live.** The intraday rerun below (15-minute candles, like the original)
  loses money every single year. Mark it as a failed strategy.

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


## Intraday rerun (15-minute candles, like the original)

Script: `agent/scripts/bull_call_spread_intraday.py`, run on NIFTY 1-minute
candles (Apr 2017 → 27 Nov 2026). One lot (65) per trade, real NSE costs:
₹20 + GST per order, 0.1% STT on sell premium, 0.0353% exchange fee and
0.5 point slippage per leg per side. Weekly expiry on Thursday until Aug 2025,
Tuesday after. Strikes ATM±100, up to 3 entries a day, net premium target/stop
30% of the debit, day P&L tracker +₹3,000 / −₹2,000, exit by 15:15.
`algobulls_*` versions enter at 09:30 every day. `filtered_*` versions need the
15-minute close above its 20-EMA and the previous daily close above the
20-day EMA. Those P&L thresholds are our guesses: on AlgoBulls you set them
yourself.

### Bottom line

- **Every version loses every year**, in both periods, at every setting
  tried. The AlgoBulls rules lose about ₹1.4 lakh a year per lot. The
  filtered version loses about ₹60,000.
- **Two reasons, both structural:**
  1. **NIFTY has lost ground during market hours.** From 09:30 to 15:15
     the index fell about 6,900 points in total over these 9.5 years. The
     overnight gap plus the first 15 minutes gained about 20,100. A bullish
     position opened at 09:30 and closed at 15:15 is on the wrong side of
     that. This alone explains the roughly ₹20,000 a year per lot lost
     before costs.
  2. **Costs.** About ₹250 per round trip, times 3 to 10 trades a week, comes
     to ₹40,000 to ₹1.2 lakh a year per lot. The short-leg stop makes it worse:
     it fires on about 60% of trades (mostly *winning* ones, so the win rate
     looks good at 62%) and each fire triggers a re-entry and another set of
     costs.
- The trend filter and dropping the short-leg stop roughly halve the loss.
  They don't turn it positive.

### 1 lot, 15-minute candles, real costs

| Variant | Period | ₹/yr per lot | Return/yr on ₹2L | Max DD ₹ | Sharpe | Costs ₹/yr | Trades | Win rate | Green years | Exit mix |
|---|---|---|---|---|---|---|---|---|---|---|
| algobulls_style | 2017-2021 | -159,325 | -79.7% | -742,584 | -6.13 | 120,738 | 2339 | 61.1% | 0/5 | short_sl 61%, day_tracker 20%, time 11%, stop 7%, target 0% |
| algobulls_style | 2022-2026 | -123,685 | -61.8% | -611,988 | -4.48 | 123,733 | 2460 | 62.9% | 0/5 | short_sl 59%, day_tracker 19%, time 14%, stop 7%, target 0% |
| algobulls_style | Full | -140,935 | -70.5% | -1,345,310 | -5.26 | 122,168 | 4799 | 62.0% | 0/10 | short_sl 60%, day_tracker 20%, time 13%, stop 7%, target 0% |
| filtered_no_short_sl | 2017-2021 | -72,366 | -36.2% | -341,287 | -4.10 | 42,873 | 830 | 42.2% | 0/5 | day_tracker 41%, time 35%, target 18%, stop 5% |
| filtered_no_short_sl | 2022-2026 | -48,265 | -24.1% | -247,415 | -3.16 | 37,510 | 744 | 45.2% | 0/5 | day_tracker 39%, time 36%, target 19%, stop 5% |
| filtered_no_short_sl | Full | -59,950 | -30.0% | -577,540 | -3.65 | 40,087 | 1574 | 43.6% | 0/10 | day_tracker 40%, time 36%, target 19%, stop 5% |
| filtered_keep_short_sl | 2017-2021 | -93,055 | -46.5% | -433,911 | -6.37 | 68,142 | 1322 | 58.9% | 0/5 | short_sl 60%, day_tracker 19%, time 16%, stop 5%, target 0% |
| filtered_keep_short_sl | 2022-2026 | -67,530 | -33.8% | -342,387 | -5.18 | 57,388 | 1144 | 60.5% | 0/5 | short_sl 55%, time 20%, day_tracker 18%, stop 6%, target 0% |
| filtered_keep_short_sl | Full | -79,891 | -39.9% | -766,856 | -5.80 | 62,571 | 2466 | 59.6% | 0/10 | short_sl 58%, day_tracker 18%, time 18%, stop 6%, target 0% |
| algobulls_no_short_sl | 2017-2021 | -120,066 | -60.0% | -559,407 | -3.84 | 77,186 | 1493 | 42.5% | 0/5 | day_tracker 45%, time 27%, target 21%, stop 8% |
| algobulls_no_short_sl | 2022-2026 | -88,922 | -44.5% | -453,295 | -2.74 | 78,566 | 1557 | 45.0% | 0/5 | day_tracker 47%, time 27%, target 21%, stop 5% |
| algobulls_no_short_sl | Full | -104,012 | -52.0% | -1,005,947 | -3.27 | 77,827 | 3050 | 43.8% | 0/10 | day_tracker 46%, time 27%, target 21%, stop 6% |

### filtered_no_short_sl, full period, sensitivity

| Test | ₹/yr per lot | Return/yr on ₹2L | Max DD ₹ | Sharpe | Costs ₹/yr | Trades | Win rate | Green years | Exit mix |
|---|---|---|---|---|---|---|---|---|---|
| strikes ±1 | -65,795 | -32.9% | -632,766 | -5.84 | 48,715 | 1944 | 40.0% | 0/10 | time 35%, stop 28%, target 24%, day_tracker 13% |
| strikes ±4 | -58,126 | -29.1% | -579,368 | -2.90 | 34,771 | 1321 | 38.3% | 1/10 | day_tracker 77%, time 23%, target 0%, stop 0% |
| 1 entry/day | -53,937 | -27.0% | -520,352 | -3.41 | 33,673 | 1319 | 41.9% | 0/10 | time 38%, day_tracker 37%, target 22%, stop 3% |
| no day tracker | -75,420 | -37.7% | -728,539 | -3.45 | 47,769 | 1882 | 44.7% | 0/10 | time 43%, stop 32%, target 25% |
| target/stop 50% | -53,700 | -26.8% | -517,822 | -3.19 | 33,948 | 1330 | 40.0% | 0/10 | day_tracker 52%, time 47%, target 1%, stop 0% |


Caveat: options are still priced with Black-Scholes on 20-day realized vol,
not real option prices. Real NIFTY options usually trade above realized vol,
which would make buying the ITM leg cost more. That makes the result worse,
not better.
