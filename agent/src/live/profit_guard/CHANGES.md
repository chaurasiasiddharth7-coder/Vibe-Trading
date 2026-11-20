# Profit Guard: changes (2026-11-19, P1-P5)

PAPER ONLY. No live order path exists or was added.

## Files touched
- `src/live/runtime/triggers.py`: added the `india_equity` market spec (09:15-15:30 IST, Mon-Fri).
  Holidays are not hard-coded there: the module is stdlib-only. Profit Guard applies the NSE store's
  list via `session.nse_holidays()`.

## Files added (src/live/profit_guard/)
- `session.py`: pure IST session clock (`session_phase`, expiry day, minutes to close, NSE holidays).
- `models.py`: PositionPlan, PortfolioState, Tick, actions (MoveStop, ExitPosition, ExitAll, Alert). Decimal money.
- `config.py` + `profit_guard.yaml`: every parameter, validated. Starting values only; tune via backtest.
- `rules.py`: pure exit logic.
  - Layer 1: initial stop, breakeven, scale-out, chandelier/percent trail, time stop, opposite signal.
  - Layer 2: loss cap plus tiered / lock_fixed / lock_and_trail profit lock.
  - Layer 3: square-off, expiry, events, BTST gap exit.
  - Stops never loosen and floors never fall; both are enforced in code.
- `store.py`: SQLite WAL + synchronous=FULL, single-writer file lock, write-ahead intents, fsync'd JSONL audit.
- `broker_port.py`: the one broker interface. `paper_broker.py`: next-tick fills, slippage, option
  market/SL-M rejection, LPP rejection, GTT OCO.
- `executor.py`: order of work is HALT check, write-ahead intent, throttle (8/s), then send.
  - Exits re-price and retry until filled, with an alert after 30s. Entries are never retried.
  - Never sells more than held.
  - ExitAll reuses `runtime/flatten.py`. A loss cap trips HALT.

## Tests
`agent/tests/profit_guard/`: 73 tests, including the Reddit-day replay, crash (kill -9) recovery,
the throttle under a 20-position flatten, HALT behaviour and property tests on stop monotonicity.

    cd agent && ../.venv/bin/python -m pytest tests/profit_guard -q

## Not done yet
- K1 (Kite live data, tomorrow).
- P6 (runner hook + reconcile + Telegram).
- P7 (backtest comparison).
- P8 (dashboard).
- P9 (break-it review).

## Readiness check fixes (2026-11-19, later)
An end-to-end simulated day found three bugs, now fixed and covered by tests (77 total):
- **Booked P&L now uses real fills.** Before, it used the price that triggered the exit. Flatten
  exits now close their plans. Before, closed positions kept counting in the day's P&L after a
  profit lock or loss cap.
- **An exit that is cancelled and replaced is no longer counted twice** (`Executor._unbook`).
- **The day's P&L ignores plans that closed on earlier days** (`portfolio_mtm(day_start_ms=...)`).
  Before, yesterday's profit could hide today's loss cap.
- **New setting `executor.exits_when_halted: true`** (default). HALT blocks new risk and stop moves,
  but protective exits still go out, so a paused bot never leaves positions without stops.
  Set it to false for a total freeze.

## K1 + P6 (2026-11-20)
- `feeds/`: read-only Kite client (an allowlist of data calls; every order, GTT and MF write is blocked,
  and a test fails the build if one becomes reachable or kiteconnect is imported anywhere else).
  It also holds the daily login (manual; token flushed ~07:30 IST), the one-click 127.0.0.1 login
  catcher, the KiteTicker feed, the instrument map, the candle cache and `check_live`.
- `runner.py`: the guard loop, every 10s in market hours.
  - Live prices with a REST fallback; no decision on a price older than 30s.
  - Paper fills; per-strategy profit lock; NSE holidays applied.
  - Paper entries through an inbox file (single writer), one plan per stock, BTST GTT backstop,
    and a 15:25 check that every carry position has its GTT.
- `reconcile.py`: adopts orphans with a 2% stop, closes plans with no position, HALTs on quantity mismatch.
- `alerts.py`: Telegram, max 20/min; logs only until TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
- `executor.rebuild_pending()`: after a restart it re-tracks sent exits and sends unsent ones once. Fill charges
  now come off booked P&L. Paper broker state persists across restarts.
- Scripts: `scripts/kite_login.command` (daily), `scripts/profit_guard_run.command` (start the guard).
- Tests: 114.

## 20 Nov 2026: overhaul (Phases 4-6), P7 backtest, P9 review

- Users with passwords: `src/alphadesk/users.py`, `src/api/users_routes.py`, cookie sign-in hook in `src/api/security.py`
  (`_bind_desk_session`). Argon2id; tries are counted before the password check so parallel tries can't skip
  the 5-try lock; unknown names lock too; turned-off accounts answer like a wrong password.
- Pages: `src/alphadesk/wealth.py` + `src/api/wealth_routes.py` (holdings, groups with quantities, F&O, option
  chain, Gold/ETF/MF, Suggestions, guard status, `/zerodha/callback`). Kite data for the owner only.
- Frontend: Kite-style shell (`components/shell/`), Login, Users, Portfolio, F&O, Wealth, Suggestions, My layout,
  Profit Guard strip on Stocks. Hosted build only; the local full app keeps its sidebar.
- `requirements-lock.txt`: argon2-cffi 25.1.0 + bindings with hashes (the Docker build would otherwise crash).
- P7: `backtest_btst.py`, results in `docs/profit_guard_p7/`. Current +1%/-2% exit loses; tiered lock and
  sell-at-open make money; strict gate fails on capture.
- P9 review: 9 confirmed findings fixed, each with a test in `tests/test_review_fixes.py`.
- Backups of every pre-existing file I edited are in the session VM only; the edits are listed in the readiness doc.

## 20 Nov 2026 (afternoon): Strategies page
- `src/alphadesk/strategies.py` + `src/api/strategies_routes.py`; page `frontend/src/pages/Strategies.tsx`.
- Catalogue across every profile (owner) or your own (others): signal board, intraday, BTST, Profit Guard exit
  settings, research artifacts, and lab strategies people build.
- Lab rule builder (13 condition kinds, stop/target/trail/time exits; no code), daily-bar backtest with the P7
  robustness checks (halves, double costs, random entries, last-30% hold-out) and a pass gate.
- Import from text or a link: the AI model drafts rules into the form (owner only); nothing saves until you press Save.
- Paper switch: built-ins default on; turning BTST or intraday off skips new picks/days but still settles open ones
  (edits in `btst/scheduler.py` and `intraday/scheduler.py`). Lab strategies need one test before paper; when on they
  are checked at 15:45 IST on trading days and open trades in the same practice ledger, tagged with the strategy;
  `board_paper.close_trade` keeps their results out of the signal board's own record.
- Live switch: locked by design (paper-only rule, 19 Nov). `set_live` always refuses; the route returns 409; a test
  proves the module imports no broker code.
- Tests: `tests/test_strategies.py` (30), `frontend/src/pages/__tests__/Strategies.test.tsx` (5).

## 20 Nov 2026 (evening): deep audit fixes
Independent audit: full suite 6306 passed / 19 failed (18 pre-existing library/count drift, 1 line-count test now fixed).
14 confirmed findings, all fixed with tests in `tests/test_audit_fixes.py` (15 tests):
- H1 inbox replayed after restart -> read position kept in the store (`meta.inbox_offset`); not read before 09:15.
- H2 crash between the rules and the exit -> the exited plan is written in the same transaction as its exit intent
  (`store.record_intent(plan=...)`, `executor.saved_with_intent`); nothing is marked exited without an exit on record.
- H3 session cookie path `/api/desk` -> `/api` (analysis and Kronos charts work for password users).
- M1 sign-in brake behind Caddy/Docker -> trusted-proxy walk of X-Forwarded-For (private nets + `VT_TRUSTED_PROXIES`);
  limit per address+username (20/min) plus 100/min per address.
- M2 weak new password could lock you out -> the right current password clears the count.
- M3 exited guard plans listed as open (`state<>'CLOSED'`) -> `state<>'EXITED'`.
- M4 `ReadOnlyKite()._k.place_order` reachable -> any private attribute access raises.
- M5 privacy blur missed two amounts -> fixed.
- L1 lock countdown told real from fake usernames -> identical lock model.
- L2 key profile could share a password user's desk -> `profiles.create` refuses user ids.
- L3 Strategies page unreachable -> already fixed (page in `users.PAGES`).
- L4 stale saved key broke password sign-in -> Login clears it.
- L5 `guard_daemon.sh` never restarted a crashed guard -> restarts with backoff until 15:40, reports crashes.
- L6 installer removed the schedule on a typo -> only "install" / "remove" act.
Also: paper order ids reserved above every id the store references (no reuse after a crash); server Kite login
requires `KITE_OWNER_USER_ID`; morning/arena/PM tools are owner-only for password users; api_server routes via
`src/api/desk_pages_routes.py`.

## 20 Nov 2026 (late): bridge, so the guard has positions to guard
- `bridge.py`, run inside the runner every 60s in market phases: today's overnight (BTST) pick is bought on paper
  after the 15:20 scan with the pick's stop (no +1% target: P7 showed it loses; +5% only as the GTT backstop), and
  every OPEN signal-board / Strategies-lab practice trade is mirrored once with its current stop and target.
- Sizing: a stop-out risks at most 0.5% of capital, no position above 20% of capital (`bridge:` in the yaml).
- Hand-overs go through the inbox; the store remembers them (`meta.bridge_done`), so restarts never re-enter.
- Runner: honours a handed-over stop/target; refuses an entry whose price is already past its stop.
- Hosted practice trades are read too when `DESK_API_KEY` (owner key) is set in agent/.env; otherwise this Mac's.
- Status page labels each position with its source. Tests: `tests/profit_guard/test_bridge.py` (8).
