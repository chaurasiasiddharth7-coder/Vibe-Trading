#!/bin/bash
# One double-click deploy of the merged desk: server (VPS) first, then the website (Vercel), then checks.
# Deploys from a clean copy of alphadesk/snapshot on your fork (../Vibe-Trading-deploy), never from your
# working folder, so uncommitted work is never shipped and never touched.
# The first run asks for your server login (e.g. root@2.25.236.103) once and remembers it in scripts/.deploy_host.
set -u
cd "$(dirname "$0")/.." || exit 1
MAIN="$(pwd)"
WT="$MAIN/../Vibe-Trading-deploy"
HOST_FILE="$MAIN/scripts/.deploy_host"
fail() { echo; echo "Stopped: $1"; read -r -p "Press Enter to close."; exit 1; }
step() { echo; echo "== $1"; }

if [ ! -s "$HOST_FILE" ]; then
  read -r -p "Server login for the VPS (user@2.25.236.103): " H
  [ -n "$H" ] || fail "no server login given"
  echo "$H" > "$HOST_FILE"; chmod 600 "$HOST_FILE"
fi
DEPLOY_HOST="$(cat "$HOST_FILE")"; export DEPLOY_HOST
export DEPLOY_DIR="${DEPLOY_DIR:-/opt/vibe-trading}"

step "Getting the merged code (fork/alphadesk/snapshot)"
git fetch fork alphadesk/snapshot || fail "couldn't fetch from your fork"
if [ -d "$WT/.git" ] || [ -f "$WT/.git" ]; then
  git -C "$WT" checkout -q --detach fork/alphadesk/snapshot || fail "couldn't update the deploy copy"
else
  git worktree add -q --detach "$WT" fork/alphadesk/snapshot || fail "couldn't make the deploy copy"
fi
echo "Deploying $(git -C "$WT" log --oneline -1)"

step "Checking the server answers (ssh $DEPLOY_HOST)"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$DEPLOY_HOST" true || {
  rm -f "$HOST_FILE"
  fail "can't sign in to $DEPLOY_HOST without a password. Check the login (it'll ask again next time) or add your SSH key to the server."
}

step "Server: rsync + docker compose up --build (takes a few minutes)"
(cd "$WT" && bash deploy/alphadesk/deploy.sh --seed-nse) || fail "server deploy failed (see above). The website was NOT updated."

step "Owner sign-in for the website"
if ssh "$DEPLOY_HOST" "cd $DEPLOY_DIR/deploy/alphadesk && docker compose exec -T alphadesk-api python -m src.alphadesk.users list" 2>/dev/null | grep -q " owner "; then
  echo "An owner sign-in already exists."
else
  PW_FILE="$HOME/Desktop/vibe-desk-owner-password.txt"
  ssh "$DEPLOY_HOST" "cd $DEPLOY_DIR/deploy/alphadesk && docker compose exec -T alphadesk-api python -m src.alphadesk.users create-owner sid --name Sid" > "$PW_FILE" 2>&1 \
    && chmod 600 "$PW_FILE" && echo "Created user 'sid'. The one-time password is in $PW_FILE (change it at first sign-in, then delete the file)." \
    || echo "Couldn't create the owner sign-in automatically; you can still use your server key."
fi

step "Website: vercel deploy --prod"
[ -d "$MAIN/frontend/.vercel" ] && cp -R "$MAIN/frontend/.vercel" "$WT/frontend/"
(cd "$WT/frontend" && npx --yes vercel deploy --prod --yes) || fail "Vercel deploy failed (run 'npx vercel login' once if it asked to sign in). The server is already updated."

step "Checks"
code=$(curl -s -o /dev/null -w '%{http_code}' https://desk.fairscoreguide.com/live)
echo "Server /live: $code"
code2=$(curl -s -o /dev/null -w '%{http_code}' https://desk.fairscoreguide.com/api/desk/strategies)
echo "Strategies route: $code2 (401/403 = up and protected; 404 = old server)"
echo
echo "Done. Open your dashboard and sign in as 'sid'."
read -r -p "Press Enter to close."
