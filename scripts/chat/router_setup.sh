#!/bin/bash
# Runs ON THE VPS (deploy_router.command copies it up). Installs/updates 9router (decolua/9router) in Docker
# next to LibreChat, adds the provider keys Sid left in /opt/9router-secrets/providers.txt, makes a client
# key for LibreChat and adds "9router" as a model source in the chat. Safe to re-run.
# The dashboard listens on 127.0.0.1 only; reach it from the Mac with router_dashboard.command (ssh tunnel).
set -u
SECRETS=/opt/9router-secrets
DATA=/opt/9router/data
PORT=20128
LC=/opt/librechat
LC_SECRETS=/opt/librechat-secrets
say() { echo "   $*"; }
die() { echo "STOP: $*"; exit 1; }
mkdir -p "$SECRETS" "$DATA" && chmod 700 "$SECRETS"

echo "== 9router container"
[ -s "$SECRETS/password" ] || (umask 077; openssl rand -base64 18 | tr -d '/+=' | cut -c1-20 > "$SECRETS/password")
PW="$(cat "$SECRETS/password")"
NET="$(docker inspect LibreChat -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | awk '{print $1}')"
[ -n "$NET" ] || die "LibreChat isn't running; run deploy_chat.command first."
if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT " && ! docker ps --format '{{.Names}}' | grep -qx 9router; then
  die "Port $PORT is already used by something else on the server."
fi
docker pull -q decolua/9router:latest >/dev/null || die "Couldn't download the 9router image."
docker rm -f 9router >/dev/null 2>&1 || true
docker run -d --name 9router --restart unless-stopped \
  --network "$NET" -p "127.0.0.1:$PORT:20128" \
  -v "$DATA:/app/data" \
  -e DATA_DIR=/app/data -e PORT=20128 -e HOSTNAME=0.0.0.0 -e NEXT_TELEMETRY_DISABLED=1 \
  -e INITIAL_PASSWORD="$PW" \
  decolua/9router:latest >/dev/null || die "docker run failed."
for i in $(seq 1 60); do
  c=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/health"); [ "$c" = 200 ] && break; sleep 2
done
[ "$c" = 200 ] || die "9router didn't start (docker logs 9router | tail -40)."
say "Running ($(curl -s "http://127.0.0.1:$PORT/api/version" | head -c 60))."

echo "== Sign in to the router (on the server)"
TOK=$(curl -s -D - -o /tmp/9r_login.json -H 'Content-Type: application/json' \
      -d "{\"password\":\"$PW\"}" "http://127.0.0.1:$PORT/api/auth/login" \
      | tr -d '\r' | sed -n 's/^[Ss]et-[Cc]ookie: auth_token=\([^;]*\).*/\1/p' | head -1)
[ -n "$TOK" ] || { head -c 300 /tmp/9r_login.json; echo; die "Couldn't sign in to the router. If you changed its password in the dashboard, put the new one in $SECRETS/password."; }
api() {  # api METHOD PATH [JSON]
  curl -s -X "$1" -H "Cookie: auth_token=$TOK" -H 'Content-Type: application/json' \
       ${3:+-d "$3"} "http://127.0.0.1:$PORT$2"
}
api PATCH /api/settings '{"requireLogin":true,"requireApiKey":true}' >/dev/null
say "Dashboard login and API keys required."

echo "== Key for the chat"
if [ ! -s "$SECRETS/librechat_key" ]; then
  key=$(api POST /api/keys '{"name":"librechat"}' | python3 -c 'import sys,json; print(json.load(sys.stdin).get("key",""))' 2>/dev/null)
  [ -n "$key" ] || die "Couldn't create a router key for the chat."
  (umask 077; printf '%s' "$key" > "$SECRETS/librechat_key")
  say "Created."
else
  say "Already there."
fi
KEY="$(cat "$SECRETS/librechat_key")"

echo "== Provider keys"
if [ -s "$SECRETS/providers.txt" ]; then
  have=$(api GET /api/providers | python3 -c 'import sys,json; print(" ".join(sorted({c.get("provider","") for c in json.load(sys.stdin).get("connections",[])})))' 2>/dev/null)
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"; line="$(echo "$line" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    prov="$(echo "${line%%[=:]*}" | tr 'A-Z' 'a-z' | tr -d ' ')"; k="$(echo "${line#*[=:]}" | sed 's/^[[:space:]]*//')"
    [ -n "$prov" ] && [ -n "$k" ] && [ "$prov" != "$line" ] || { say "Skipped a line that isn't 'provider = key'."; continue; }
    if echo " $have " | grep -q " $prov "; then say "$prov: already connected (kept)."; continue; fi
    body=$(python3 -c 'import json,sys; print(json.dumps({"provider":sys.argv[1],"apiKey":sys.argv[2],"name":sys.argv[1]}))' "$prov" "$k")
    res=$(api POST /api/providers "$body")
    if echo "$res" | grep -q '"error"'; then
      say "$prov: not added ($(echo "$res" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("error","?"))' 2>/dev/null))."
    else
      say "$prov: connected."
    fi
  done < "$SECRETS/providers.txt"
  shred -u "$SECRETS/providers.txt" 2>/dev/null || rm -f "$SECRETS/providers.txt"
  say "Key file removed from the server (the keys now live only inside 9router)."
else
  say "No provider file sent; add providers later (put keys in scripts/.router_providers.txt and run this again, or use the dashboard)."
fi

echo "== Models the router offers"
MODELS=$(curl -s -H "Authorization: Bearer $KEY" "http://127.0.0.1:$PORT/v1/models" \
         | python3 -c 'import sys,json; print(" ".join(m["id"] for m in json.load(sys.stdin).get("data",[])))' 2>/dev/null)
n=$(echo "$MODELS" | wc -w); say "$n models${n:+: $(echo "$MODELS" | cut -c1-200)}"

echo "== Adding 9router to the chat"
(umask 077; mkdir -p "$LC_SECRETS"; cp "$SECRETS/librechat_key" "$LC_SECRETS/router_key")
cd "$LC" || die "no $LC"
python3 - "$KEY" <<'PY'
import re, sys
k = sys.argv[1]; s = open(".env").read()
s = re.sub(r"(?m)^ROUTER_API_KEY=.*$\n?", "", s) + f"ROUTER_API_KEY={k}\n"
open(".env", "w").write(s)
PY
chmod 600 .env
if ! grep -q 'name: "9router"' librechat.yaml; then
  cat >> librechat.yaml <<'YAML'
    - name: "9router"
      apiKey: "${ROUTER_API_KEY}"
      baseURL: "http://9router:20128/v1"
      models:
        default: ["auto"]
        fetch: true
      titleConvo: true
      titleModel: "current_model"
      summarize: false
      modelDisplayLabel: "9router"
YAML
fi
docker compose up -d --force-recreate api </dev/null >/dev/null 2>&1 || die "LibreChat didn't restart."
for i in $(seq 1 40); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3080/); [ "$c" = 200 ] && break; sleep 3; done
[ "$c" = 200 ] && say "Chat restarted with 9router as a model source." || die "LibreChat didn't come back (docker compose logs api | tail -40)."
if [ "$n" -gt 0 ]; then
  first=$(echo "$MODELS" | awk '{print $1}')
  code=$(docker exec LibreChat sh -c "wget -q -O- --header='Authorization: Bearer $KEY' --header='Content-Type: application/json' --post-data='{\"model\":\"$first\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}' http://9router:20128/v1/chat/completions >/dev/null 2>&1 && echo ok || echo fail")
  say "Test answer from the chat's side via $first: $code"
fi
echo "LOGIN $PW"
echo "DONE"
