#!/usr/bin/env bash
# Redeploy the AlphaDesk backend on the VPS from the COMMITTED tree, then check it came up.
#
#   DEPLOY_HOST=user@host [DEPLOY_DIR=/opt/vibe-trading] ./deploy.sh [--seed-intraday] [--dry-run]
#
# What it does: rsync agent/ and deploy/alphadesk/ (never .env), `docker compose up -d --build`,
# wait for /live, and confirm the intraday routes exist (an auth error is a pass, a 404 is not).
# --seed-intraday also copies your local filed days and 5-minute store into the container volume,
# as the container user, so the hosted page opens with history. It never overwrites the board's
# practice trades or outcomes. --dry-run shows the rsync changes and stops.
set -euo pipefail

: "${DEPLOY_HOST:?set DEPLOY_HOST=user@host}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/vibe-trading}"
SEED=0; DRY=0
for a in "$@"; do case "$a" in --seed-intraday) SEED=1 ;; --dry-run) DRY=1 ;; *) echo "unknown option $a"; exit 2 ;; esac; done

repo="$(git rev-parse --show-toplevel)"
cd "$repo"
if ! git diff --quiet HEAD -- agent deploy; then echo "Uncommitted changes under agent/ or deploy/: commit first (this deploys what is committed)."; exit 1; fi
[ -d third_party/Kronos ] || echo "note: third_party/Kronos is not in this checkout; the server keeps its own copy."

# Local-only, gitignored state (session transcripts, runs, uploaded portfolios) must never ship.
RS=(rsync -az --exclude '__pycache__' --exclude '*.pyc' --exclude '.env'
    --exclude '/sessions/' --exclude '/runs/' --exclude '/uploads/' --exclude '/.swarm/'
    --exclude '*.egg-info/')
if [ "$DRY" = 1 ]; then RS+=(--dry-run --itemize-changes); fi
"${RS[@]}" agent/ "$DEPLOY_HOST:$DEPLOY_DIR/agent/"
"${RS[@]}" deploy/alphadesk/ "$DEPLOY_HOST:$DEPLOY_DIR/deploy/alphadesk/"
[ "$DRY" = 1 ] && { echo "dry run only: nothing built or restarted."; exit 0; }

ssh "$DEPLOY_HOST" "cd $DEPLOY_DIR/deploy/alphadesk && docker compose up -d --build"

echo "waiting for the API to answer /live ..."
for i in $(seq 1 30); do
  if ssh "$DEPLOY_HOST" "curl -fsS http://127.0.0.1:8899/live >/dev/null"; then ok=1; break; fi; sleep 3
done
[ "${ok:-0}" = 1 ] || { echo "API did not come up; check: ssh $DEPLOY_HOST 'cd $DEPLOY_DIR/deploy/alphadesk && docker compose logs --tail 80'"; exit 1; }

code="$(ssh "$DEPLOY_HOST" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8899/api/desk/intraday/schedule")"
case "$code" in 200|401|403) echo "intraday routes present (HTTP $code)";; *) echo "intraday routes missing (HTTP $code): the old image is still running"; exit 1;; esac

if [ "$SEED" = 1 ]; then
  src="$HOME/.vibe-trading/alphadesk/intraday"
  [ -d "$src/days" ] || { echo "nothing to seed: $src/days not found"; exit 1; }
  tar -C "$src" -cf - days bars | ssh "$DEPLOY_HOST" "cd $DEPLOY_DIR/deploy/alphadesk && docker compose exec -T alphadesk-api sh -c 'mkdir -p /home/vibe/.vibe-trading/alphadesk/intraday && tar -C /home/vibe/.vibe-trading/alphadesk/intraday -xf -'"
  echo "seeded $(ls "$src/days" | wc -l | tr -d ' ') filed days and the 5-minute store"
fi
echo "backend live. Next: deploy the front end (cd frontend && vercel deploy --prod)."
