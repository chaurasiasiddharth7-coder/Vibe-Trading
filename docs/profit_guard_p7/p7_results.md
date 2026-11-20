# P7 overnight exit backtest (2023-11-20 to 2026-11-17, 538 trades)

Same rules.py as live. Worst-case path inside each daily bar unless noted. Net of 0.25% costs.

| Var | Plan | Avg % | Total % | Win % | Worst % | Max DD % | Capture % | Best-path avg % | Random-entry avg % |
|---|---|---|---|---|---|---|---|---|---|
| A | Current: +1% target / -2% stop | -0.381 | -204.75 | 55.6 | -4.8 | -206.16 | -8.3 | 0.334 | -0.487 |
| B | Hold to close, no stop | -0.124 | -66.73 | 41.1 | -11.63 | -212.6 | -10.5 | -0.124 | -0.177 |
| C | Sell at the open | 0.261 | 140.4 | 59.9 | -4.7 | -18.93 | 21.4 | 0.261 | 0.169 |
| D | Lock fixed (+0.25% once +0.5%) | -0.36 | -193.92 | 9.3 | -4.8 | -211.18 | -9.6 | 0.074 | -0.457 |
| E | Tiered keep of peak (live default) | 0.36 | 193.56 | 59.5 | -4.8 | -28.96 | 9.7 | 1.946 | 0.078 |
| F | Lock and trail | 0.076 | 40.89 | 58.0 | -4.8 | -54.68 | 3.2 | 1.352 | -0.151 |
| G | +2% target / -2% stop | -0.433 | -232.75 | 44.2 | -4.8 | -233.68 | -16.9 | 0.307 | -0.497 |

Walk-forward: chose **C** on 2023-11-20..2025-12-12 (avg 0.33%), holdout 2025-12-15..2026-11-17: avg 0.101% over 162 trades (baseline A -0.408%).

Gate for C: **FAIL**

- [x] holdout profitable after costs
- [x] worst trade better than baseline
- [ ] capture above 50pct
- [x] both halves profitable
- [x] survives double costs
- [x] beats random entries
