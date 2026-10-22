# AlphaDesk Desk — DESIGN.md

Scope: the `/desk` route only. Reference: Refero **Mercury** ("Alpine banking at blue hour") for restraint and number-first hierarchy; the host app's Tailwind tokens win for color and type.

## North star

A trading slip, not a dashboard. Three lists answer one question: what does the model say to buy, short, and avoid today, and how much should I trust it.

## Theme / tokens

Host tokens only: `background`, `card`, `border`, `muted`, `muted-foreground`, `foreground`. Semantic accents:

| Role | Class | Use |
|---|---|---|
| Long | `emerald-400/500` | Long list header, long targets |
| Short | `rose-400/500` | Short list header, stops |
| Steer clear | `amber-400/500` | Avoid list, trust warnings |
| Neutral evidence | `muted-foreground` | Votes, reasons |

No other hues. No gradients, glows, pills-everywhere, or emoji.

## Type

Host sans for labels; `font-mono` (tabular) for every price, R, score, and date. Hierarchy by weight and size: symbol `text-sm font-semibold`, numbers `font-mono text-sm`, evidence `text-xs`.

## Layout

1. Trust strip: track record, data as-of, Kronos status. Always first.
2. Board: three columns (Long / Short / Steer clear) ≥1024px; stacked below.
3. Detail for the selected symbol: chart and evidence, with levels.
4. "Desk tools" `<details>`, closed by default: Arena, Committee, paper orders, live ticket, scorecard, ops.

Cards only where they hold interaction (rows are buttons). Max one border level.

## Components

- Board row: button, full width, ≥44px tall. Symbol, score (e.g. `3/4`), entry → target / stop, one-line reasons.
- Vote chips: four fixed slots (T S M K) as `+`, `−`, `·`.

## Motion budget (3D: none — internal tool)

- 3D ambition: **none / poster** (dashboard rule in the FCC doctrine).
- Motion 1: the selected row gets a background shift (opacity/transform only, 150ms, `ease-out`).
- Motion 2: the Scan button spinner while the universe scan runs (the only loading motion; no layout shift).
- `prefers-reduced-motion`: Tailwind `motion-reduce:transition-none`, so there are no transitions.

## Motion storyboard

| Moment | What moves | Why |
|---|---|---|
| Scan running | Spinner on the Scan button only | Shows work without layout shift |
| Board ready | Lists swap in place, "as of" date updates | Freshness is shown by the date, not by animation |
| Row select | Row tint, detail panel swaps content (no animation) | Shows the current selection |

## A11y

Rows are `<button>` elements with `aria-pressed`. Color is never the only signal: side labels and `+`/`−` glyphs carry meaning. Body contrast ≥4.5:1 on host tokens.

## Don't

- Show a "confidence %" that isn't backed by outcomes.
- Show allocation sleeves or MF playbooks on the main view.
- Put live/ops controls above the board.

## `/guide` — My Guide (plain-language page)

Same tokens and bans as the Desk. Audience: someone who doesn't know trading jargon.

- Order: trust card (practice-trade progress to 30, past-test result) → settings (max loss per trade, show/hide lists, funds, "worked in the past" filter, sort, look-up) → idea cards → my practice trades → glossary → not-advice note.
- Copy: no indicator names in the main text. Votes become sentences ("The price trend is pointing up"). R multiples become "₹X per ₹100 risked". Every tradeable card states the rupees gained if right and lost if wrong, sized from the user's max-loss setting.
- Cards hold interaction (the practice button), so cards are allowed here. One border level.
- Motion: toggle background change (150ms, `ease-out`), spinner on "Check prices again". Nothing else. `motion-reduce` disables both.
- Settings persist per browser in `localStorage` (try/catch; defaults if blocked).
- Universe picker (core, Nifty 50/100/200/500, Midcap 150, Smallcap 250), industry filter, name search, and "Show more" paging (24 cards at a time). Wide scans run as background jobs with a thin progress bar (`width` transition, 300ms, `ease-out`, disabled under reduced motion).

## Practice trades: row actions and glance (Guide page)

- Each row is three targets: symbol button (opens "at a glance": key numbers, chart with entry / cut-loss / take-profit, suggested exit), the details button, and an Actions menu (open trades only).
- Actions: Exit now (inline confirm), Move cut-loss (tighten only, applies next session), Exit style (the four existing styles). Menu closes on Escape or outside click.
- Closed trades show "What we learned" (best/worst move, same trade under each exit style). Suggestions are text only; nothing changes without the user's action.
- Motion: none added. The chevron rotation stays the only transition (150ms, `motion-reduce:transition-none`).

## Unified Dashboard (`/dashboard`)

- Replaces the separate My Guide and AlphaDesk pages. `/guide` and `/desk` redirect (`/desk` opens `?tab=chart`). Mode: Operate.
- Order: summary strip (open / finished result, winning count, track-record sentence, "needs a look" banner) → dismissible three-step welcome → tabs: Today's ideas · My practice trades · Chart and tools.
- Tabs are `role=tablist` with arrow-key navigation and the tab in the URL. The Guide instance stays mounted across the first two tabs so state is kept; Chart and tools mounts on first visit.
- The embedded pages keep their titles as screen-reader-only `h2`s so the page has one `h1`.
- Below 768px the app sidebar starts collapsed (unless the user chose otherwise).
- Motion budget unchanged: no new animation.

## At-a-glance analysis panel

Inside the glance panel (symbol click): chart with 20/50-day average lines and a pin for the latest named candle pattern; then "What the analysis says": a bullish / neutral / bearish bar with a one-line verdict, a signal list (short-term trend, long-term position, RSI, MACD, SuperTrend, Bollinger band, daily move, AI forecast), "Candle analysis" (named pattern plus the last three candles in words), and for trades "does it still hold?" (entry votes vs today). Direction is always a glyph plus a word (▲ Bullish, ▼ Bearish, ● Neutral, ⚠ Stretched), never colour alone. Logic lives in `analysisView.ts` (unit-tested).

## Shared analysis: `SymbolInsight`

One component feeds both the practice-trade glance and the "See the analysis" section on every idea card (collapsed, loads only when opened). It adds, on top of the analysis panel:
- Volume: today vs the 20-day average and 10-day buying vs selling volume (a signal row), plus a volume strip under the candles.
- Support and resistance: swing highs and lows merged within 1.5%, the two nearest each side, dashed lines on the chart, and warnings when a level sits before a take-profit or above a cut-loss.
- AI forecast on demand ("Run fresh forecast"): bypasses the cache, draws the dashed forecast line, replaces the earlier reading and lets "does it still hold" recheck the forecast. Errors are shown in plain words.
- Compact mode (idea cards): single column, no chart legend or level labels.
Backend note: Kronos predictions are serialised with a lock because the shared predictor is not safe to call concurrently.
