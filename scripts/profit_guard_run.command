#!/bin/bash
# Starts the Profit Guard loop: live Kite PRICES, PAPER trades only. Leave this window open
# during market hours; close it (or press Ctrl+C) to stop. Run kite_login.command first each day.
cd "$(dirname "$0")/../agent" || exit 1
echo "Profit Guard · paper trading · Kite used for prices only"
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m src.live.profit_guard.runner
echo
read -r -p "Guard stopped. Press Enter to close."
