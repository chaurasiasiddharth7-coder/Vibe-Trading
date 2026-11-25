#!/bin/bash
# One double-click: 9router on the VPS, connected to your chat (chat.fairscoreguide.com).
# Provider keys: put them in scripts/.router_providers.txt first, one per line as  provider = key
# (provider = the name 9router uses, e.g. deepseek, openrouter, groq, gemini). The file is sent to the
# server, added to 9router, then deleted here and there. Safe to run again.
set -u
cd "$(dirname "$0")/.." || exit 1
MAIN="$(pwd)"
H="$(cat "$MAIN/scripts/.deploy_host" 2>/dev/null)"
KEYS="$MAIN/scripts/.router_providers.txt"
LOGIN="$MAIN/scripts/.router_login"
LOG="$MAIN/logs/deploy_router-$(date +%F-%H%M).log"
mkdir -p "$MAIN/logs"
fail() { echo; echo "Stopped: $1"; read -r -p "Press Enter to close."; exit 1; }
[ -n "$H" ] || fail "no server login yet. Run deploy_all.command once first."

echo "== Checking the server answers (ssh $H)"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$H" true || fail "can't sign in to $H without a password."

if [ -s "$KEYS" ]; then
  echo "== Sending your provider keys (not shown)"
  ssh "$H" "umask 077; mkdir -p /opt/9router-secrets && cat > /opt/9router-secrets/providers.txt" < "$KEYS" \
    && rm -f "$KEYS" && echo "   Sent, and removed from this Mac." || fail "couldn't send the key file."
fi

echo "== Installing / updating 9router on the server"
TMP="$(mktemp)"; chmod 600 "$TMP"; trap 'rm -f "$TMP"' EXIT
ssh "$H" 'cat > /tmp/router_setup.sh' < "$MAIN/scripts/chat/router_setup.sh" || fail "couldn't copy the setup script."
ssh "$H" 'bash /tmp/router_setup.sh; rc=$?; rm -f /tmp/router_setup.sh; exit $rc' 2>&1 | tee "$TMP" | grep -v '^LOGIN '
RC=${PIPESTATUS[0]}
grep -v '^LOGIN ' "$TMP" >> "$LOG"
[ "$RC" -eq 0 ] || fail "server setup stopped (see above, saved in $LOG)."
PW="$(grep '^LOGIN ' "$TMP" | tail -1 | cut -d' ' -f2-)"
if [ -n "$PW" ]; then
  umask 077
  printf '9router dashboard (open with scripts/router_dashboard.command)\nPassword: %s\n' "$PW" > "$LOGIN"
  echo "   Dashboard password saved in scripts/.router_login (in your Vibe-Trading folder)."
fi
echo
echo "Done. In the chat, pick 9router in the model menu. Log: $LOG"
read -r -p "Press Enter to close."
