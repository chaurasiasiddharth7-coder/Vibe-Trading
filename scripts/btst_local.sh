#!/usr/bin/env bash
# Run the Overnight (BTST) page locally: API on :8897 (schedule on), page on :5896.
# Then refresh the NSE price store and build the 3-year replay in the background.
# Logs: ~/.vibe-trading/alphadesk/btst/*.log
set -u
cd "$(dirname "$0")/.."
REPO=$(pwd)
PY="$REPO/.venv/bin/python"
LOG="$HOME/.vibe-trading/alphadesk/btst"; mkdir -p "$LOG"

lsof -ti tcp:8897 | xargs kill 2>/dev/null
lsof -ti tcp:5896 | xargs kill 2>/dev/null

(cd agent && BTST_SCHEDULER=1 nohup "$PY" -c "import api_server; raise SystemExit(api_server.serve_main(['--host','127.0.0.1','--port','8897']))" > "$LOG/api.log" 2>&1 &)
(VITE_API_URL=http://127.0.0.1:8897 nohup npm --prefix frontend run dev -- --port 5896 --strictPort > "$LOG/web.log" 2>&1 &)

(
  echo "== updating NSE store $(date)"; "$PY" scripts/nse_pull.py --update
  echo "== replay $(date)"; cd agent && "$PY" -m src.alphadesk.btst replay --years 3
  echo "== done $(date)"
) > "$LOG/setup.log" 2>&1 &

for i in $(seq 1 40); do curl -s -o /dev/null http://127.0.0.1:5896 && break; sleep 1; done
echo "Open: http://localhost:5896/btst"
echo "Setup progress: tail -f $LOG/setup.log"
