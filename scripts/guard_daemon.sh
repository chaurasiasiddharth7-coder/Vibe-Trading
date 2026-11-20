#!/bin/bash
# Run by launchd on weekdays at 09:00. PAPER trading only; Kite is used for prices only.
# 1) waits (up to 15:30) for today's Kite login, nudging you every 15 min with a Mac notification
# 2) runs the Profit Guard loop, 3) stops it at 15:40. Log: logs/guard-YYYY-MM-DD.log
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/agent" || exit 1
mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/guard-$(date +%F).log"
PY="$ROOT/.venv/bin/python"
export PYTHONDONTWRITEBYTECODE=1
notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Vibe Desk\"" >/dev/null 2>&1; }
fresh() { "$PY" -m src.live.profit_guard.feeds.kite_auth status 2>/dev/null | grep -q '"token_fresh": true'; }
now_hm() { date +%H%M; }

echo "$(date) guard_daemon start" >> "$LOG"
n=0
until fresh; do
  if [ "$(now_hm)" -ge 1530 ]; then echo "$(date) no Kite login today; guard not started" >> "$LOG"; notify "No Kite login today: guard did not run"; exit 0; fi
  if [ $((n % 15)) -eq 0 ]; then notify "Log in to Kite to start the paper guard (double-click kite_login.command)"; fi
  n=$((n + 1)); sleep 60
done
notify "Kite login found: paper guard starting"
# Runs until 15:40. If the guard crashes it is restarted (waits 30s, 60s, 120s ... up to 5 min between tries).
# A crash is reported as a crash, never as a normal stop. The store and exits are crash-safe, so a restart is safe.
BACKOFF=30
CRASHES=0
while [ "$(now_hm)" -lt 1540 ]; do
  "$PY" -m src.live.profit_guard.runner >> "$LOG" 2>&1 &
  PID=$!
  echo "$(date) guard running, pid $PID" >> "$LOG"
  STOPPED_BY_US=0
  while kill -0 "$PID" 2>/dev/null; do
    if [ "$(now_hm)" -ge 1540 ]; then
      STOPPED_BY_US=1
      kill -INT "$PID"
      for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      kill "$PID" 2>/dev/null
      break
    fi
    sleep 30
  done
  wait "$PID" 2>/dev/null
  CODE=$?
  if [ "$STOPPED_BY_US" = 1 ]; then break; fi
  CRASHES=$((CRASHES + 1))
  echo "$(date) guard EXITED UNEXPECTEDLY (code $CODE, crash $CRASHES); restarting in ${BACKOFF}s" >> "$LOG"
  notify "Paper guard crashed (code $CODE). Restarting in ${BACKOFF}s. Check logs/guard-$(date +%F).log"
  sleep "$BACKOFF"
  BACKOFF=$((BACKOFF * 2)); [ "$BACKOFF" -gt 300 ] && BACKOFF=300
  if ! fresh; then
    echo "$(date) Kite login is no longer fresh; not restarting" >> "$LOG"
    notify "Paper guard stopped: the Kite login expired. Log in again with kite_login.command."
    exit 1
  fi
done
echo "$(date) guard stopped for the day ($CRASHES crash(es))" >> "$LOG"
notify "Paper guard stopped for the day. Summary in logs/guard-$(date +%F).log"
