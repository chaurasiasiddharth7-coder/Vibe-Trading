# AlphaDesk backend on a VPS

1. Copy the repo (plus `third_party/Kronos`) to the server, e.g. `/opt/vibe-trading`.
2. `cd deploy/alphadesk && echo "API_AUTH_KEY=$(openssl rand -hex 32)" > .env && chmod 600 .env`
3. `docker compose up -d --build` (listens on 127.0.0.1:8899 only).
4. Put a TLS reverse proxy in front that forwards only `/api/desk/*`, `/api/analysis/*`, `/api/kronos/*` and `/live`
   (see `Caddyfile.snippet`). Everything else stays unreachable from the internet.
5. Point the frontend host (Vercel) at the proxy with rewrites for those same paths.

## Redeploy and the intraday jobs

`./deploy.sh` (run from anywhere in the repo) redeploys the committed tree:

```
DEPLOY_HOST=user@your-vps DEPLOY_DIR=/opt/vibe-trading ./deploy/alphadesk/deploy.sh --dry-run   # look first
DEPLOY_HOST=user@your-vps DEPLOY_DIR=/opt/vibe-trading ./deploy/alphadesk/deploy.sh --seed-intraday
```

Order matters: **backend first, then the front end** (`cd frontend && vercel deploy --prod`). The front end's
new Intraday link calls `/api/desk/intraday/*`, which only exists once the new image is running; the Vercel rewrite
`/api/desk/:path*` already forwards it, so no `vercel.json` change is needed.

`INTRADAY_SCHEDULER=1` (set in `docker-compose.yml`) starts the two daily jobs inside the container: opening scan
09:15 IST, closing settle 15:40 IST, on NSE trading days. Times are computed in IST whatever the container's clock.
Filed days live in the `alphadesk-home` volume, so they survive redeploys.

Data on the VPS: with no NSE store (`scripts/nse_pull.py`, not in the image) daily scans use Yahoo and the
"All NSE" universe reports that it needs the store. Yahoo may rate-limit a datacenter address; `--seed-intraday`
gives the page its history either way.
