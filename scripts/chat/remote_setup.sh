#!/bin/bash
# Runs ON THE VPS (deploy_chat.command pipes it over ssh). Installs/updates LibreChat in /opt/librechat,
# connected to DeepSeek, Z.ai GLM or a free open model running on this server (CHAT_PROVIDER=ollama),
# listening on 127.0.0.1 only, with Caddy in front for https://$CHAT_DOMAIN.
# Safe to re-run: keeps existing secrets, users and chats; only fills in what's missing.
set -u
CHAT_DOMAIN="${CHAT_DOMAIN:-chat.fairscoreguide.com}"
CHAT_PROVIDER="${CHAT_PROVIDER:-}"   # "ollama" = free model on this server, no API key
DIR=/opt/librechat
SECRETS=/opt/librechat-secrets
PORT=3080
say() { echo "   $*"; }
die() { echo "STOP: $*"; exit 1; }

echo "== Server checks"
command -v docker >/dev/null || die "Docker isn't installed on the server."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing on the server."
cv=$(docker compose version --short 2>/dev/null | sed 's/^v//')
[ "$(printf '%s\n2.24.4\n' "$cv" | sort -V | head -1)" = "2.24.4" ] || die "Docker Compose $cv is too old (need 2.24.4+): run 'apt-get install docker-compose-plugin' on the server."
if ss -ltn 2>/dev/null | grep -q "[:.]$PORT " && ! docker ps --format '{{.Names}}' | grep -qx LibreChat; then
  die "Port $PORT is already used by something else on the server."
fi
say "Docker OK; port $PORT free or already LibreChat."

echo "== LibreChat code (github.com/danny-avila/LibreChat)"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q --depth 1 origin main && git -C "$DIR" reset -q --hard origin/main && say "Updated to the latest version."
else
  git clone -q --depth 1 https://github.com/danny-avila/LibreChat.git "$DIR" || die "Couldn't download LibreChat."
  say "Downloaded."
fi
cd "$DIR" || die "no $DIR"

echo "== Settings (.env)"
[ -f .env ] || cp .env.example .env
chmod 600 .env
setenv() {  # setenv KEY VALUE: set or add, value never printed
  if grep -qE "^#?\s*$1=" .env; then
    python3 - "$1" "$2" <<'PY'
import re, sys
k, v = sys.argv[1], sys.argv[2]
s = open(".env").read()
s = re.sub(rf"(?m)^#?\s*{re.escape(k)}=.*$", lambda m: f"{k}={v}", s, count=1)
open(".env", "w").write(s)
PY
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
current() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
fresh() {  # keep a real secret; replace an empty one or the example default
  local v; v="$(current "$1")"
  if [ -z "$v" ] || grep -qE "^#?\s*$1=$v\s*$" .env.example; then setenv "$1" "$(openssl rand -hex "$2")"; fi
}
fresh CREDS_KEY 32; fresh CREDS_IV 16; fresh JWT_SECRET 32; fresh JWT_REFRESH_SECRET 32; fresh MEILI_MASTER_KEY 16
setenv HOST 0.0.0.0
setenv PORT "$PORT"
setenv DOMAIN_CLIENT "https://$CHAT_DOMAIN"
setenv DOMAIN_SERVER "https://$CHAT_DOMAIN"
setenv ALLOW_REGISTRATION false          # only the accounts we create; no public sign-ups
setenv ALLOW_SOCIAL_LOGIN false
setenv ALLOW_EMAIL_LOGIN true
setenv ENDPOINTS custom,agents
setenv NO_INDEX true
say "Secrets in place (kept if they already existed); public sign-up off."

echo "== AI model key"
mkdir -p "$SECRETS" && chmod 700 "$SECRETS"
PROVIDER=""; KEY=""
[ -n "$CHAT_PROVIDER" ] && printf '%s' "$CHAT_PROVIDER" > "$SECRETS/provider"
[ -s "$SECRETS/provider" ] && [ "$(cat "$SECRETS/provider")" = ollama ] && PROVIDER=ollama
if [ "$PROVIDER" = ollama ]; then
  say "Free model on this server: no key needed."
elif [ -s "$SECRETS/deepseek_key" ]; then
  PROVIDER=deepseek; KEY="$(cat "$SECRETS/deepseek_key")"
elif [ -s "$SECRETS/zai_key" ]; then
  PROVIDER=glm; KEY="$(cat "$SECRETS/zai_key")"
else
  # Reuse the GLM key your agents already use, if it's on this server (searched quietly, never printed).
  KEY="$(grep -hsoE '^(export )?(ZAI_API_KEY|Z_AI_API_KEY|ZHIPU_API_KEY|GLM_API_KEY|BIGMODEL_API_KEY)=.+' \
        /root/.env /root/*.env /opt/*/.env /opt/*/*.env /home/*/.env /root/.hermes/.env /root/.hermes/*/.env 2>/dev/null \
        | head -1 | sed -E 's/^(export )?[A-Z_]+=//; s/^["'\'']//; s/["'\'']$//')"
  if [ -n "$KEY" ]; then PROVIDER=glm; (umask 077; printf '%s' "$KEY" > "$SECRETS/zai_key"); say "Found the GLM key your agents use on this server."; fi
fi
[ -n "$KEY" ] || [ "$PROVIDER" = ollama ] || { echo "NEED_KEY"; exit 3; }

works() {  # works BASE MODEL -> 0 if a tiny chat request succeeds
  [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$1/chat/completions" \
       -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
       -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}")" = 200 ]
}

BASE=""; MODELS=()
if [ "$PROVIDER" = ollama ]; then
  echo "== Free open model (Ollama on this server)"
  mem=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  # Qwen 2.5 3B: about 2 GB, runs on CPU. Smaller servers get the 1.5B version.
  OLLAMA_MODEL="${OLLAMA_MODEL:-$( [ "${mem:-0}" -ge 6000 ] && echo qwen2.5:3b || echo qwen2.5:1.5b )}"
  BASE="http://ollama:11434/v1"; MODELS=("$OLLAMA_MODEL"); ENV_NAME=""; LABEL="Free model"
  say "Server memory ${mem} MB -> $OLLAMA_MODEL"
elif [ "$PROVIDER" = deepseek ]; then
  echo "== Which DeepSeek models answer with this key"
  BASE=https://api.deepseek.com/v1
  ids=$(curl -s --max-time 30 "$BASE/models" -H "Authorization: Bearer $KEY" \
        | python3 -c 'import sys,json; print(" ".join(m["id"] for m in json.load(sys.stdin).get("data",[])))' 2>/dev/null)
  [ -n "$ids" ] || ids="deepseek-chat deepseek-reasoner"
  for m in $ids; do works "$BASE" "$m" && MODELS+=("$m"); done
  if [ ${#MODELS[@]} -eq 0 ]; then  # say why (DeepSeek's own message, never the key)
    first=${ids%% *}
    reply=$(curl -s --max-time 60 -w ' [HTTP %{http_code}]' "$BASE/chat/completions" -H "Authorization: Bearer $KEY" \
            -H 'Content-Type: application/json' -d "{\"model\":\"$first\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":5}" 2>&1 | head -c 300)
    [ -n "$reply" ] || reply="no answer: the server could not reach api.deepseek.com"
    say "DeepSeek replied: $reply"
  fi
  [ ${#MODELS[@]} -gt 0 ] || die "The DeepSeek key didn't work (check the key or its balance at platform.deepseek.com). Delete $SECRETS/deepseek_key on the server to enter a new one."
  setenv DEEPSEEK_API_KEY "$KEY"; ENV_NAME=DEEPSEEK_API_KEY; LABEL=DeepSeek
else
  echo "== Which GLM models answer with this key"
  for base in https://api.z.ai/api/paas/v4 https://api.z.ai/api/coding/paas/v4 https://open.bigmodel.cn/api/paas/v4; do
    for m in glm-5.2 glm-5.1 glm-5 glm-4.7 glm-4.6 glm-4.5-air glm-4.7-flash glm-4.5-flash; do
      works "$base" "$m" && MODELS+=("$m")
    done
    if [ ${#MODELS[@]} -gt 0 ]; then BASE="$base"; break; fi
  done
  [ -n "$BASE" ] || die "The GLM key didn't work on any Z.ai endpoint (check the key or its plan). Delete $SECRETS/zai_key on the server to enter a new one."
  setenv ZAI_API_KEY "$KEY"; ENV_NAME=ZAI_API_KEY; LABEL=GLM
fi
say "Provider: $LABEL ($BASE)"
say "Models: ${MODELS[*]}"
list=$(printf '"%s", ' "${MODELS[@]}"); list="[${list%, }]"

if [ "$PROVIDER" = ollama ]; then APIKEY='"ollama"'; EXTRA='      dropParams: ["stop", "user", "frequency_penalty", "presence_penalty"]'
else APIKEY="\"\${$ENV_NAME}\""; EXTRA=""; fi
cat > librechat.yaml <<YAML
# Written by deploy_chat.command. Re-running it rewrites this file.
version: 1.2.1
cache: true
endpoints:
  custom:
    - name: "$LABEL"
      apiKey: $APIKEY
      baseURL: "$BASE"
      models:
        default: $list
        fetch: false
      titleConvo: true
      titleModel: "current_model"
      summarize: false
      modelDisplayLabel: "$LABEL"
$EXTRA
YAML

cat > docker-compose.override.yml <<'YAML'
# Written by deploy_chat.command: reachable only through Caddy (127.0.0.1), with our librechat.yaml.
services:
  api:
    ports: !override
      - "127.0.0.1:3080:3080"
    volumes:
      - type: bind
        source: ./librechat.yaml
        target: /app/librechat.yaml
YAML
if grep -q '^  admin-panel:' docker-compose.yml 2>/dev/null; then
  # LibreChat's optional admin panel wants port 3000, which this server already uses: keep it off.
  cat >> docker-compose.override.yml <<'YAML'
  admin-panel:
    profiles: ["admin-panel"]
YAML
fi
if [ "$PROVIDER" = ollama ]; then
  cat >> docker-compose.override.yml <<'YAML'
  ollama:                      # the free model, reachable only by LibreChat (no port opened)
    image: ollama/ollama:latest
    container_name: LibreChat-Ollama
    restart: always
    environment:
      - OLLAMA_KEEP_ALIVE=24h
      - OLLAMA_NUM_PARALLEL=1
    volumes:
      - ollama:/root/.ollama
volumes:
  ollama:
YAML
fi

echo "== Starting LibreChat (first time downloads a few images)"
docker compose pull -q </dev/null 2>/dev/null || true
docker rm -f admin-panel >/dev/null 2>&1 || true   # left over from a run before the panel was switched off
docker compose up -d --remove-orphans </dev/null || die "docker compose up failed (see above)."
for i in $(seq 1 60); do
  c=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/") ; [ "$c" = 200 ] && break; sleep 3
done
[ "$c" = 200 ] || die "LibreChat didn't answer on 127.0.0.1:$PORT (try: cd $DIR && docker compose logs api | tail -50)."
say "LibreChat is running."
if [ "$PROVIDER" = ollama ]; then
  echo "== Downloading the free model ($OLLAMA_MODEL, about 2 GB the first time)"
  timeout 1800 docker compose exec -T ollama ollama pull "$OLLAMA_MODEL" </dev/null >/tmp/ollama_pull.log 2>&1 \
    || { tail -3 /tmp/ollama_pull.log; die "Couldn't download the model."; }
  ans=$(timeout 300 docker compose exec -T ollama ollama run "$OLLAMA_MODEL" "Reply with the single word: ready" </dev/null 2>/dev/null | head -c 80)
  [ -n "$ans" ] && say "Model answers: $ans" || say "Model downloaded (first answer can take a minute)."
fi

echo "== Your sign-in"
EMAIL="sid@${CHAT_DOMAIN#chat.}"
if [ -s "$SECRETS/sid_created" ]; then
  say "Account $EMAIL already exists."
else
  PW="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)"
  if timeout 90 docker compose exec -T api npm run create-user -- "$EMAIL" "Sid" "sid" "$PW" --email-verified=true </dev/null >/tmp/lc_user.log 2>&1 \
     || timeout 90 docker compose exec -T api sh -c "cd .. && npm run create-user -- '$EMAIL' 'Sid' 'sid' '$PW' --email-verified=true" </dev/null >/tmp/lc_user.log 2>&1; then
    date > "$SECRETS/sid_created"
    echo "SIGNIN $EMAIL $PW"
  else
    tail -5 /tmp/lc_user.log
    say "Couldn't create the account automatically."
  fi
fi

echo "== Web address (Caddy)"
if [ -f /etc/caddy/Caddyfile ] && command -v caddy >/dev/null; then
  CF=/etc/caddy/Caddyfile
  if grep -q "^$CHAT_DOMAIN" "$CF"; then
    say "Caddy already serves $CHAT_DOMAIN."
  else
    TLS=""
    CERT=$(grep -oE 'tls /etc/caddy/certs/[^ ]+\.crt [^ ]+\.key' "$CF" | head -1)
    if [ -n "$CERT" ]; then
      crt=$(echo "$CERT" | awk '{print $2}')
      if openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | grep -qE "DNS:(\*\.${CHAT_DOMAIN#chat.}|$CHAT_DOMAIN)"; then
        TLS="	$CERT"; say "Reusing the desk's certificate (it covers $CHAT_DOMAIN)."
      else
        say "The desk's certificate doesn't cover $CHAT_DOMAIN; Caddy will get one automatically."
      fi
    fi
    cp "$CF" "$CF.bak-chat-$(date +%s)"
    {
      printf '\n%s {\n' "$CHAT_DOMAIN"
      [ -n "$TLS" ] && printf '%s\n' "$TLS"
      printf '\tencode gzip\n\theader X-Robots-Tag "noindex, nofollow"\n\treverse_proxy 127.0.0.1:%s\n}\n' "$PORT"
    } >> "$CF"
    if caddy validate --config "$CF" --adapter caddyfile >/tmp/caddy_v.log 2>&1; then
      systemctl reload caddy && say "Caddy now serves https://$CHAT_DOMAIN."
    else
      tail -5 /tmp/caddy_v.log; cp "$(ls -t "$CF".bak-chat-* | head -1)" "$CF"; die "Caddy didn't accept the new site; I put the old config back."
    fi
  fi
else
  say "No Caddy at /etc/caddy on this server. Found instead:"
  docker ps --format '   {{.Names}}  {{.Image}}  {{.Ports}}' | grep -iE 'caddy|nginx|traefik|proxy' || say "(no proxy container)"
  echo "NO_CADDY"
fi
# This server's Caddy has automatic HTTPS switched off; the desk gets its certificate from certbot
# (webroot /var/www/acme). Do the same for the chat: an HTTP block for the challenge, a certbot
# certificate copied to /etc/caddy/certs/chat.*, renewed by certbot's timer with a hook.
if [ -f /etc/caddy/Caddyfile ] && grep -qE '^\s*auto_https off' /etc/caddy/Caddyfile && command -v certbot >/dev/null; then
  echo "== HTTPS certificate for $CHAT_DOMAIN (certbot, same way as the desk)"
  CF=/etc/caddy/Caddyfile; WEBROOT=/var/www/acme; mkdir -p "$WEBROOT"
  HOOK=/etc/letsencrypt/renewal-hooks/deploy/caddy-chat.sh
  mkdir -p "$(dirname "$HOOK")"
  cat > "$HOOK" <<HK
#!/bin/sh
# Copy the chat certificate where Caddy reads it, after certbot gets or renews it.
case " \$RENEWED_DOMAINS " in *" $CHAT_DOMAIN "*) ;; *) exit 0 ;; esac
install -o caddy -g caddy -m 644 "\$RENEWED_LINEAGE/fullchain.pem" /etc/caddy/certs/chat.crt
install -o caddy -g caddy -m 600 "\$RENEWED_LINEAGE/privkey.pem" /etc/caddy/certs/chat.key
systemctl reload caddy
HK
  chmod 755 "$HOOK"
  changed=no
  if ! grep -q "^http://$CHAT_DOMAIN" "$CF"; then
    cp "$CF" "$CF.bak-chat-http-$(date +%s)"
    printf '\nhttp://%s {\n\thandle /.well-known/acme-challenge/* {\n\t\troot * %s\n\t\tfile_server\n\t}\n\thandle {\n\t\tredir https://%s{uri} permanent\n\t}\n}\n' \
      "$CHAT_DOMAIN" "$WEBROOT" "$CHAT_DOMAIN" >> "$CF"
    changed=yes
  fi
  if [ $changed = yes ]; then
    caddy validate --config "$CF" --adapter caddyfile >/tmp/caddy_v.log 2>&1 \
      && systemctl reload caddy || { tail -5 /tmp/caddy_v.log; cp "$(ls -t "$CF".bak-chat-http-* | head -1)" "$CF"; die "Caddy didn't accept the challenge block; old config put back."; }
  fi
  if [ ! -s /etc/caddy/certs/chat.crt ]; then
    certbot certonly --webroot -w "$WEBROOT" -d "$CHAT_DOMAIN" --non-interactive --keep-until-expiring \
      --deploy-hook "$HOOK" >/tmp/certbot_chat.log 2>&1 \
      || { tail -8 /tmp/certbot_chat.log; die "certbot couldn't get a certificate for $CHAT_DOMAIN (see above)."; }
    [ -s /etc/caddy/certs/chat.crt ] || RENEWED_DOMAINS="$CHAT_DOMAIN" RENEWED_LINEAGE="/etc/letsencrypt/live/$CHAT_DOMAIN" sh "$HOOK"
    say "Certificate issued by Let's Encrypt; certbot renews it with the desk's."
  else
    say "Certificate already in place."
  fi
  if ! awk -v d="$CHAT_DOMAIN {" '$0==d{f=1} f&&/^}/{f=0} f' "$CF" | grep -q 'tls /etc/caddy/certs/chat.crt'; then
    cp "$CF" "$CF.bak-chat-tls-$(date +%s)"
    python3 - "$CF" "$CHAT_DOMAIN" <<'PY'
import sys
cf, dom = sys.argv[1], sys.argv[2]
s = open(cf).read()
head = f"\n{dom} {{\n"
i = s.find(head)
if i >= 0:
    j = i + len(head)
    s = s[:j] + "\ttls /etc/caddy/certs/chat.crt /etc/caddy/certs/chat.key\n" + s[j:]
    open(cf, "w").write(s)
PY
    caddy validate --config "$CF" --adapter caddyfile >/tmp/caddy_v.log 2>&1 \
      && systemctl reload caddy && say "Caddy now serves https://$CHAT_DOMAIN with that certificate." \
      || { tail -5 /tmp/caddy_v.log; cp "$(ls -t "$CF".bak-chat-tls-* | head -1)" "$CF"; die "Caddy didn't accept the certificate; old config put back."; }
  fi
fi
echo "DONE"
