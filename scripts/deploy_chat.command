#!/bin/bash
# One double-click: your own AI chat (LibreChat) at https://chat.fairscoreguide.com on the VPS.
# The AI: a free open model on the server when scripts/chat/provider says "ollama", else DeepSeek / Z.ai GLM.
# Uses the same server login as deploy_all.command (scripts/.deploy_host). Safe to run again: it updates
# LibreChat and keeps your account, chats and settings.
set -u
cd "$(dirname "$0")/.." || exit 1
MAIN="$(pwd)"
HOST_FILE="$MAIN/scripts/.deploy_host"
REMOTE="$MAIN/scripts/chat/remote_setup.sh"
DOMAIN="chat.fairscoreguide.com"
PW_FILE="$HOME/Desktop/chat-signin.txt"
PROVIDER="$(tr -d ' \n' < "$MAIN/scripts/chat/provider" 2>/dev/null)"
LOG="$MAIN/logs/deploy_chat-$(date +%F-%H%M).log"
mkdir -p "$MAIN/logs"
fail() { echo; echo "Stopped: $1"; read -r -p "Press Enter to close."; exit 1; }
step() { echo; echo "== $1"; }

[ -s "$HOST_FILE" ] || fail "no server login yet. Run deploy_all.command once first."
H="$(cat "$HOST_FILE")"
SERVER_IP="${H#*@}"

step "Checking the server answers (ssh $H)"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$H" true || fail "can't sign in to $H without a password."

step "Checking the address $DOMAIN"
IP_NOW="$(dig +short "$DOMAIN" A 2>/dev/null | tail -1)"
DESK_IP="$(dig +short desk.fairscoreguide.com A 2>/dev/null | tail -1)"
if [ -z "$IP_NOW" ]; then
  DNS_OK=no
  echo "   $DOMAIN doesn't exist yet. Add this DNS record where fairscoreguide.com's DNS is managed:"
  echo "      Type A   Name chat   Value $SERVER_IP   (same setting as the 'desk' record, e.g. proxied or not)"
  echo "   The chat gets installed now; the web address starts working a few minutes after the record."
else
  DNS_OK=yes
  echo "   $DOMAIN -> $IP_NOW (desk -> ${DESK_IP:-?})"
fi

TMP="$(mktemp)"; chmod 600 "$TMP"; trap 'rm -f "$TMP"' EXIT
# Copy the setup script up, then run it (piping it into bash would let docker eat the rest of it).
# The one-time password line is kept off the screen and out of the log.
run_remote() {
  ssh "$H" 'cat > /tmp/librechat_setup.sh' < "$REMOTE" || return 1
  ssh "$H" "CHAT_DOMAIN=$DOMAIN CHAT_PROVIDER=$PROVIDER bash /tmp/librechat_setup.sh; rc=\$?; rm -f /tmp/librechat_setup.sh; exit \$rc" 2>&1 \
    | tee "$TMP" | grep -v '^SIGNIN '
  local rc=${PIPESTATUS[0]}
  grep -v '^SIGNIN ' "$TMP" >> "$LOG"
  return $rc
}

# A key left for this script (scripts/.chat_deepseek_key or .chat_zai_key): send it to the server, then delete it here.
for prov in deepseek zai; do
  KF="$MAIN/scripts/.chat_${prov}_key"
  if [ -s "$KF" ]; then
    step "Sending your ${prov} key to the server (not shown)"
    ssh "$H" "umask 077; mkdir -p /opt/librechat-secrets && rm -f /opt/librechat-secrets/deepseek_key /opt/librechat-secrets/zai_key && cat > /opt/librechat-secrets/${prov}_key" < "$KF" \
      && rm -f "$KF" && echo "   Saved on the server and removed from this Mac." || fail "couldn't save the key on the server."
  fi
done

step "Installing / updating LibreChat on the server"
run_remote; RC=$?; OUT="$(cat "$TMP")"
if echo "$OUT" | grep -q "^NEED_KEY"; then
  echo
  echo "   No AI key on the server yet. Paste a DeepSeek key (starts with sk-) or a Z.ai GLM key."
  echo "   It stays hidden as you paste, is stored only on the server (readable by root), and is never shown."
  read -r -s -p "   API key: " K; echo
  [ -n "$K" ] || fail "no key given."
  case "$K" in sk-*) prov=deepseek ;; *) prov=zai ;; esac
  printf '%s' "$K" | ssh "$H" "umask 077; mkdir -p /opt/librechat-secrets && cat > /opt/librechat-secrets/${prov}_key" || fail "couldn't save the key on the server."
  unset K
  run_remote; RC=$?; OUT="$(cat "$TMP")"
fi
[ $RC -eq 0 ] || fail "server setup stopped (see above, saved in $LOG)."

SIGNIN="$(echo "$OUT" | grep '^SIGNIN ' | tail -1)"
if [ -n "$SIGNIN" ]; then
  EMAIL="$(echo "$SIGNIN" | awk '{print $2}')"; PW="$(echo "$SIGNIN" | awk '{print $3}')"
  umask 077
  printf 'Your AI chat: https://%s\nEmail: %s\nPassword: %s\n\nChange the password in Settings after you sign in, then delete this file.\n' "$DOMAIN" "$EMAIL" "$PW" > "$PW_FILE"
  echo "   Sign-in saved to your Desktop: chat-signin.txt"
fi

step "Checks"
if [ "$DNS_OK" = yes ]; then
  sleep 5
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$DOMAIN/")
  echo "   https://$DOMAIN -> $code"
  if [ "$code" = 200 ]; then
    open "https://$DOMAIN"
    echo
    echo "Done. Your chat is open in the browser. Sign in with the email and password in chat-signin.txt on your Desktop."
  else
    echo "   Not answering yet (a new certificate can take a minute). Try https://$DOMAIN shortly."
  fi
else
  echo "   LibreChat is installed and running on the server. Add the DNS record above, then run this again to finish."
fi
echo "   Log: $LOG"
read -r -p "Press Enter to close."
