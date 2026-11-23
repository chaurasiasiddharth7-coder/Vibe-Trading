#!/bin/bash
# Read-only: how the desk's certificate is made, so the chat can get one the same way. Changes nothing.
cd "$(dirname "$0")/.." || exit 1
H="$(cat scripts/.deploy_host)"
LOG="logs/chat_cert_check-$(date +%F-%H%M).log"
ssh "$H" 'bash -s' <<'EOF' 2>&1 | tee "$LOG"
echo "== Caddyfile lines 44-90"
sed -n '44,90p' /etc/caddy/Caddyfile | grep -viE 'secret|token|password'
echo "== Cert tools"
for t in certbot acme.sh lego; do command -v $t; done; ls /root/.acme.sh 2>/dev/null | head
echo "== Renewal configs"
ls /etc/letsencrypt/renewal 2>/dev/null; for f in /etc/letsencrypt/renewal/*.conf; do [ -f "$f" ] && { echo "-- $f"; grep -E 'authenticator|webroot|installer|renew_hook|deploy_hook|pre_hook|post_hook|webroot_path|\[\[webroot_map' -A1 "$f"; }; done
ls -la /etc/letsencrypt/renewal-hooks/deploy 2>/dev/null
echo "== Cron / timers about certs"
crontab -l 2>/dev/null | grep -iE 'cert|acme|caddy'; systemctl list-timers --no-pager 2>/dev/null | grep -iE 'cert|acme'
ls -la /etc/caddy/certs
EOF
echo; echo "Saved to $LOG"
read -r -p "Press Enter to close."
