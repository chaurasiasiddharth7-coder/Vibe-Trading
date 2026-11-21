#!/bin/bash
# Read-only: copies the desk's trade records (intraday days, signal-board paper trades, strategy tests, BTST days,
# per-profile folders) from the VPS into logs/audit/ on this Mac, for the money-leak audit.
# Leaves out sign-ins, sessions, keys, tokens, caches and model files. Changes nothing on the server.
set -u
cd "$(dirname "$0")/.." || exit 1
MAIN="$(pwd)"
H="$(cat "$MAIN/scripts/.deploy_host" 2>/dev/null)"
OUT="$MAIN/logs/audit"
mkdir -p "$OUT"
[ -n "$H" ] || { echo "No server login (scripts/.deploy_host)."; read -r -p "Press Enter to close."; exit 1; }
echo "== Copying trade records from $H (read-only)"
ssh -o BatchMode=yes "$H" "cd /opt/vibe-trading/deploy/alphadesk && docker compose exec -T alphadesk-api sh -c \
  'cd /home/vibe/.vibe-trading && tar czf - --exclude=\"*cache*\" --exclude=\"*models*\" --exclude=\"*huggingface*\" \
   --exclude=\"*user*\" --exclude=\"*session*\" --exclude=\"*token*\" --exclude=\"*secret*\" --exclude=\"*.key\" \
   --exclude=\"*.env\" --exclude=\"nse\" --exclude=\"*.parquet\" --exclude=\"backups\" alphadesk 2>/dev/null'" \
  > "$OUT/server-alphadesk.tgz"
SZ=$(wc -c < "$OUT/server-alphadesk.tgz" | tr -d ' ')
if [ "$SZ" -gt 1000 ]; then
  echo "   Saved logs/audit/server-alphadesk.tgz ($((SZ / 1024)) KB)."
  date > "$OUT/pulled_at.txt"
else
  echo "   Nothing came back (size $SZ bytes)."
fi
read -r -p "Done. Press Enter to close."
