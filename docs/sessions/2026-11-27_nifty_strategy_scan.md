# NIFTY strategy scan, 2017-2026

Script: `agent/scripts/nifty_strategy_scan.py`, run on NIFTY 1-minute candles
(Apr 2017 → 27 Nov 2026). Each strategy trades 1 futures lot (65), with the
spot index standing in for the future. Costs per round trip: ₹20 + GST per
order, 0.02% STT on sells, exchange fee, stamp duty, 1 point of slippage per
side. One lot is about ₹16 lakh of NIFTY exposure, so the "return on ₹2.5L"
column is leveraged roughly 6x. Read the ₹ figures, not the %.

Daily strategies decide on the 15:15 price and trade at the close.
Opening-range breakouts fill at the minute close that confirms the break and
stop out at the minute close that breaches the other side. (Filling exactly at
the range level made ORB look profitable. That was a fill artifact.)

## Bottom line

- **Nothing reliably beats just holding NIFTY.** Holding made about ₹1.1
  lakh a year per lot and was up in 9 of 10 years.
- **EMA 20/50 trend, long only** is the best of the rest. It makes almost the
  same money over the full period (₹97,000 vs ₹1.1 lakh a year) with a smaller
  worst drop (₹1.9 lakh vs ₹3.1 lakh) and a better Sharpe (0.85 vs 0.68). But it
  trailed buy-and-hold in 2022-2026. It's a risk reducer, not an edge.
- **Overnight hold** (the index version of BTST) worked in 2017-2021
  (+₹1 lakh a year) and stopped working in 2022-2026 (−₹10,500 a year). Costs on
  247 round trips a year eat most of the overnight gap.
- **Intraday long, opening-range breakout, gap fade: losers.** NIFTY's
  in-session drift has been negative and daily costs pile up.
- **RSI(2) dip buying** is roughly breakeven over the full period.
- **Not tested:** option-selling strategies (short straddles, strangles, iron
  condors). Their edge depends on real option prices being above realized
  vol, which can't be tested without historical option-chain data (e.g. from
  Kite).

### 2017-2021

| Strategy | ₹/yr per lot | Return/yr on ₹2.5L | Max DD ₹ | Sharpe | Trade days/yr | Green years |
|---|---|---|---|---|---|---|
| Buy & hold (1 lot) | 111,225 | 44.5% | -307,236 | 0.84 | 247 | 5/5 |
| Overnight: buy 15:15, sell 09:30 | 101,601 | 40.6% | -268,623 | 1.15 | 247 | 4/5 |
| Intraday long: 09:30 → 15:15 | -173,612 | -69.4% | -840,619 | -1.67 | 247 | 0/5 |
| 15-min opening range breakout | -70,583 | -28.2% | -379,174 | -0.99 | 247 | 0/5 |
| 30-min opening range breakout | -34,142 | -13.7% | -226,991 | -0.45 | 246 | 2/5 |
| Gap fade (gap ≥ 0.4%) | -7,117 | -2.8% | -145,044 | -0.08 | 98 | 4/5 |
| EMA 20/50 trend, long only | 115,674 | 46.3% | -97,801 | 1.20 | 196 | 5/5 |
| EMA 20/50 trend, long/short | 120,054 | 48.0% | -188,716 | 0.91 | 247 | 5/5 |
| RSI(2) dip buy above 200-DMA | -494 | -0.2% | -279,066 | -0.01 | 49 | 2/5 |

### 2022-2026

| Strategy | ₹/yr per lot | Return/yr on ₹2.5L | Max DD ₹ | Sharpe | Trade days/yr | Green years |
|---|---|---|---|---|---|---|
| Buy & hold (1 lot) | 109,390 | 43.8% | -266,783 | 0.59 | 247 | 4/5 |
| Overnight: buy 15:15, sell 09:30 | -10,524 | -4.2% | -273,718 | -0.08 | 247 | 2/5 |
| Intraday long: 09:30 → 15:15 | -146,169 | -58.5% | -849,776 | -1.09 | 247 | 0/5 |
| 15-min opening range breakout | -54,181 | -21.7% | -445,891 | -0.53 | 247 | 2/5 |
| 30-min opening range breakout | -25,511 | -10.2% | -391,756 | -0.25 | 246 | 2/5 |
| Gap fade (gap ≥ 0.4%) | -9,218 | -3.7% | -198,242 | -0.10 | 82 | 2/5 |
| EMA 20/50 trend, long only | 78,616 | 31.4% | -146,033 | 0.61 | 151 | 3/5 |
| EMA 20/50 trend, long/short | 47,842 | 19.1% | -216,533 | 0.26 | 247 | 2/5 |
| RSI(2) dip buy above 200-DMA | 34,933 | 14.0% | -100,914 | 0.43 | 53 | 3/5 |

### Full 2017-2026

| Strategy | ₹/yr per lot | Return/yr on ₹2.5L | Max DD ₹ | Sharpe | Trade days/yr | Green years |
|---|---|---|---|---|---|---|
| Buy & hold (1 lot) | 110,199 | 44.1% | -307,236 | 0.68 | 247 | 9/10 |
| Overnight: buy 15:15, sell 09:30 | 44,609 | 17.8% | -273,718 | 0.40 | 246 | 6/10 |
| Intraday long: 09:30 → 15:15 | -159,536 | -63.8% | -1,627,496 | -1.33 | 246 | 0/10 |
| 15-min opening range breakout | -62,198 | -24.9% | -682,555 | -0.71 | 246 | 2/10 |
| 30-min opening range breakout | -29,732 | -11.9% | -436,630 | -0.33 | 246 | 4/10 |
| Gap fade (gap ≥ 0.4%) | -8,177 | -3.3% | -224,829 | -0.09 | 90 | 6/10 |
| EMA 20/50 trend, long only | 96,768 | 38.7% | -189,311 | 0.85 | 173 | 8/10 |
| EMA 20/50 trend, long/short | 83,303 | 33.3% | -274,161 | 0.52 | 247 | 7/10 |
| RSI(2) dip buy above 200-DMA | 17,487 | 7.0% | -279,066 | 0.21 | 51 | 5/10 |

