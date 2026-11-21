# Desk tasks: who does what, and when

Every job the desk runs, in one place. All trading is paper (fake money); Kite is used for prices only.
Times are IST, on NSE trading days unless stated.

| When | Task | What it does | Where it shows |
|---|---|---|---|
| 09:15 | Intraday opening scan | Plans the day's opening-range trades, **only if the intraday paper switch is on** (off since the 21 Nov 2026 audit) | Intraday page |
| 15:20 | BTST scan | Picks one overnight stock, **only if the BTST switch is on** (off since the audit) | BTST page |
| 15:40 | Intraday settle | Settles any open intraday day | Intraday page |
| 15:45 | **Kill switch** (`health.check_all_profiles`) | Judges every strategy with paper on, in every profile, on its live record; pauses any proven loser and writes the reason on its switch | Strategies page (red "Paused" line), `GET /api/desk/health` |
| 15:45 | Lab paper scans | Opens practice trades for lab strategies whose switch is on (after the kill switch, so a paused one opens nothing) | Strategies page |
| Every load | Signal board record | Rebuilt from its own closed practice trades each time (never a cached counter) | Signal board track record |
| Saturday 10:00 | **Weekly audit** (`health.weekly`) | Last week per strategy, longs vs shorts, Kronos split, live vs replay; flags anything losing three weeks running | `GET /api/desk/health/weekly`, saved under `alphadesk/reports/weekly/` |
| Start-up | Audit pauses + kill switch | Applies the 21 Nov audit pauses once per profile and runs the kill switch | server log |

## The rules every strategy lives by

1. **Paper only.** Live stays locked until the paper-only rule is lifted in writing.
2. **The gate** (past-prices test): 100+ trades; profitable after costs and at double costs; both halves profitable;
   last 30% profitable; beats random entries with the same exits; still profitable without its best 5% of trades.
   The page counts every test run on a strategy; past 45, a pass is likely luck.
3. **The kill switch:** after 30 live trades, a strategy whose average is below zero with 95% confidence is paused.
   A losing live record may lean on replayed days of the same rules to prove it. It restarts only after a test run
   after the pause passes the gate.
4. **Live apart from replays.** Every trade says where it came from (live, replay, hand-closed) and which rule version it
   ran on. Scorecards show the live record; replays are shown beside it as tests.
5. **Weekly audit** every Saturday.

## What the 21 Nov 2026 audit changed

- Intraday breakout and BTST: paper switched off (both lost after costs).
- Signal board: longs only (shorts lost -326R over 5 years), fixed exits only (+392R fixed vs +91R trailing). Open
  practice trades moved to fixed exits from the next session; a stop that already tightened stays where it is.
- Profit Guard: signal-board and lab trades keep their own stop and target (no breakeven move, no half-selling, no
  trailing, no same-day profit lock); the BTST hand-over is off.
- Survival tracker: a "dead" engine keeps counting; backtest replays (`br_pdh_pdl`, intraday calibration) are kept in
  a separate `replay` block instead of being counted as a live record.

## Run by hand

```
cd agent
python -m src.alphadesk.health check     # the kill switch, every profile
python -m src.alphadesk.health weekly    # this week's audit flags, every profile
```
