#!/bin/bash
# Run by launchd on weekdays at 09:00. PAPER trading only; Kite is used for prices only.
# 1) waits (up to 15:30) for today's Kite login, nudging you every 15 min with a Mac notification
# 2) runs the self-test (refuses to start the guard if it fails), 3) runs the Profit Guard loop until 15:40,
#    restarting it after a crash. The page logs/desk_status.html opens once and refreshes itself all day.
# Log: logs/guard-YYYY-MM-DD.log
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/agent" || exit 1
mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/guard-$(date +%F).log"
PY="$ROOT/.venv/bin/python"
export PYTHONDONTWRITEBYTECODE=1
notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Vibe Desk\"" >/dev/null 2>&1; }
fresh() { "$PY" -m src.live.profit_guard.feeds.kite_auth status 2>/dev/null | grep -q '"token_fresh": true'; }
now_hm() { date +%H%M; }
STATUS="$ROOT/logs/desk_status.html"
status() { "$PY" -m src.live.profit_guard.status_page --out "$STATUS" >/dev/null 2>&1; }
show_status() {  # open the at-a-glance page once a day (it refreshes itself every minute)
  local mark="$ROOT/logs/.status-opened-$(date +%F)"
  [ -f "$mark" ] && return
  status; : > "$mark"; /usr/bin/open "$STATUS" >/dev/null 2>&1
}

echo "$(date) guard_daemon start" >> "$LOG"
show_status
# Morning catch-up: if last evening's NSE price update didn't run (Mac asleep, no login), fetch it now.
( "$PY" ../scripts/nse_pull.py --update >> "$ROOT/logs/nse-update-$(date +%F).log" 2>&1 \
    && echo "$(date) NSE price store caught up" >> "$LOG" ) &
n=0
until fresh; do
  if [ "$(now_hm)" -ge 1530 ]; then echo "$(date) no Kite login today; guard not started" >> "$LOG"; notify "No Kite login today: guard did not run"; exit 0; fi
  if [ $((n % 15)) -eq 0 ]; then notify "Log in to Kite to start the paper guard (double-click kite_login.command)"; fi
  n=$((n + 1)); [ $((n % 5)) -eq 0 ] && status; sleep 60
done
# Self-test before any trading: the guard's own tests on this Mac's Python. A failure keeps the guard off today.
SELFTEST="$ROOT/logs/selftest-$(date +%F).txt"
if ! "$PY" -m pytest tests/profit_guard -q -p no:cacheprovider -x -W ignore > "$SELFTEST" 2>&1; then
  echo "$(date) SELF-TEST FAILED: guard not started (see logs/selftest-$(date +%F).txt)" >> "$LOG"
  status
  notify "Self-test FAILED: paper guard not started today. Details on the status page."
  exit 1
fi
echo "$(date) self-test passed: $(grep -E 'passed' "$SELFTEST" | tail -1)" >> "$LOG"
notify "Kite login found, self-test passed: paper guard starting"
# Runs until 15:40. If the guard crashes it is restarted (waits 30s, 60s, 120s ... up to 5 min between tries).
# A crash is reported as a crash, never as a normal stop. The store and exits are crash-safe, so a restart is safe.
BACKOFF=30
CRASHES=0
while [ "$(now_hm)" -lt 1540 ]; do
  "$PY" -m src.live.profit_guard.runner >> "$LOG" 2>&1 &
  PID=$!
  echo "$(date) guard running, pid $PID" >> "$LOG"
  STOPPED_BY_US=0
  TICKS=0
  while kill -0 "$PID" 2>/dev/null; do
    TICKS=$((TICKS + 1)); [ $((TICKS % 6)) -eq 1 ] && status  # every ~3 minutes
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
status
notify "$("$PY" -m src.live.profit_guard.status_page --summary 2>/dev/null || echo 'Paper guard stopped for the day.')"

# Evening: refresh the NSE price store (overnight picks and scans read it). NSE publishes the bhavcopy after ~18:00.
while [ "$(now_hm)" -lt 1915 ]; do sleep 300; done
if "$PY" ../scripts/nse_pull.py --update >> "$ROOT/logs/nse-update-$(date +%F).log" 2>&1; then
  echo "$(date) NSE price store updated" >> "$LOG"
else
  echo "$(date) NSE price store update FAILED (see logs/nse-update-$(date +%F).log)" >> "$LOG"
  notify "NSE price store update failed tonight. Scans will use older prices."
fi
