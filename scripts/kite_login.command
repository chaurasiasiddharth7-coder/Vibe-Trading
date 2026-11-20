#!/bin/bash
# Daily Kite login + live DATA check. Double-click in Finder once each trading day, after 07:35 IST.
# Kite is used for market data only. Nothing here can place an order.
cd "$(dirname "$0")/../agent" || exit 1
PY=../.venv/bin/python
$PY -m pip install -q "kiteconnect>=5.0" 2>&1 | grep -v "notice" | tail -2
REDIR_SETTING=$(grep '^KITE_REDIRECT_URL=' .env | tail -1 | cut -d= -f2-)

if [[ "$REDIR_SETTING" == http://127.0.0.1* || "$REDIR_SETTING" == http://localhost* ]]; then
  # One-click: the browser comes back to this Mac and the token is caught automatically.
  $PY -m src.live.profit_guard.feeds.login_server || { read -r -p "Press Enter to close."; exit 1; }
else
  # Kite's login page expires after a few minutes, so the link is only made when you're ready.
  read -r -p "Press Enter when you're ready to log in to Kite (the page must be used within a few minutes): " _
  URL=$($PY -m src.live.profit_guard.feeds.kite_auth url) || { echo "Could not build the login link. Is KITE_API_KEY set in agent/.env?"; read -r -p "Press Enter to close."; exit 1; }
  echo "== Log in to Kite in the browser window that just opened =="
  open "$URL"
  echo "After you log in, the page may show an error. That's fine."
  echo "If Kite says \"Invalid or expired connect session\", close this window and double-click kite_login.command again."
  read -r -p "Copy the WHOLE address bar from that page, paste it here, press Enter: " REDIR
  $PY -m src.live.profit_guard.feeds.kite_auth exchange "$REDIR" || { read -r -p "Login failed. Press Enter to close."; exit 1; }
fi
echo
echo "== Checking live prices (15 seconds) =="
PYTHONDONTWRITEBYTECODE=1 $PY -m src.live.profit_guard.feeds.check_live
echo
echo "Done. You can close this window."
