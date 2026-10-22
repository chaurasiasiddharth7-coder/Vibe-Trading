# AlphaDesk backend on a VPS

1. Copy the repo (plus `third_party/Kronos`) to the server, e.g. `/opt/vibe-trading`.
2. `cd deploy/alphadesk && echo "API_AUTH_KEY=$(openssl rand -hex 32)" > .env && chmod 600 .env`
3. `docker compose up -d --build` (listens on 127.0.0.1:8899 only).
4. Put a TLS reverse proxy in front that forwards only `/api/desk/*`, `/api/analysis/*`, `/api/kronos/*` and `/live`
   (see `Caddyfile.snippet`). Everything else stays unreachable from the internet.
5. Point the frontend host (Vercel) at the proxy with rewrites for those same paths.
