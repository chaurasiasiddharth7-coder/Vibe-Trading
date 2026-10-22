# PRODUCT.md — AlphaDesk Dashboard

## Product
A practice-trading dashboard for Indian (NSE) stocks inside Vibe-Trading. Fixed rules sort a stock list into buy ideas, short ideas and ones to stay away from. The user tries ideas with fake money, watches them settle, and learns from the result before ever using real money.

## Register
Product / operate. A task surface, not a marketing page. Scanability, plain language and trust come before expression.

## Users
The owner plus people they share it with. Mostly beginners or part-time traders. They know "buy" and "sell" but not R-multiples, ATR or drawdown, so the main surface uses plain words (cut-loss, take-profit, "how much you could lose").

## Purpose
1. Show today's ideas and how much to trust them (track record first).
2. Let the user practise with fake money and manage open trades (exit, move cut-loss, change exit style).
3. Explain every finished trade: what happened, what it taught, what a different exit would have done.

## Principles
- Honest over exciting. Say "too early to trust" when it is. Never imply advice from a registered adviser; real orders are always placed by the user in their own broker app.
- Plain language first, numbers in monospace, color never the only signal.
- Nothing changes without a click: suggestions are text, actions are confirmed.
- Warn about known data and market limits (price jumps that may be splits, same-day rule for NSE cash shorts).

## Anti-references
Casino-style P&L confetti, glow and gradient dashboards, jargon walls, notification-driven urgency.

## Constraints
Existing Vite + React + Tailwind app with host tokens (see `frontend/src/components/desk/DESIGN.md`). Dark and light themes. Works from 375px wide. WCAG AA.
