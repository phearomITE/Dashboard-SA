# Server deployment for dashboard.SA.com

The dashboard runs on the server in Docker. PostgreSQL and port 8080 are not published on the server host. `cloudflared` is the only route into the dashboard. Do not start the tunnel until the Access application and its allow policy exist.

## Cloudflare account setup

1. Confirm you control `SA.com` in your Cloudflare account. Use `dashboard.SA.com` as the application hostname.
2. In Zero Trust, create a **Self-hosted** Access application for `dashboard.SA.com`. Add an **Allow** policy for `raphearom098@gmail.com` only, unless the project owner approves more staff. Test that an unapproved account is denied.
3. Create a remotely managed Cloudflare Tunnel. Add a published application route for `dashboard.SA.com` pointing to `http://bot:8080` (the Docker service name, not `127.0.0.1`). Enable **Protect with Access** on that route so `cloudflared` validates Access tokens before forwarding requests.
4. Copy the tunnel token into `.env.server` on the server. Keep it private.

Cloudflare setup reference: [self-hosted Access application](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/), [Tunnel token](https://developers.cloudflare.com/tunnel/reference/run-parameters/).

## Server files and start

Install Docker Compose on the server. Copy this project and its existing bot `.env` to the server. Copy the private roster JSON, dealer coordinates, and controller assignments into `private-data/master_sa_20261114.json`, `private-data/dealer_stations.csv`, and `private-data/controller_assignments.json` on the server. They are excluded from Git and the image and mounted read-only at runtime. Keep the PostgreSQL volume from the previous deployment or restore a verified backup; a fresh volume creates a new, empty database.

Copy `.env.server.example` to `.env.server` and set a strong database password and tunnel token. The database password must match the existing PostgreSQL volume if one is reused. Do not put secrets in Git.

```bash
docker compose --env-file .env.server -f compose.server.yml config --quiet
docker compose --env-file .env.server -f compose.server.yml up -d --build
docker compose --env-file .env.server -f compose.server.yml ps
```

Check `https://dashboard.SA.com` using an approved staff identity. The dashboard has no separate Telegram sign-in in this deployment; Cloudflare Access is the gate. Do not expose port 8080 or PostgreSQL to the public network. If Access is bypassed or misconfigured, the dashboard has no application-level sign-in.

## Backups and restore checks

The `backup` service starts with an immediate custom-format `pg_dump`, restores it into a temporary database to verify it, then repeats every 24 hours. Successful dumps are written to `./backups` on the server, with a default 14-day retention. Back up that directory to storage outside the server as well; a server or disk failure can otherwise destroy both the database and its local dumps.

```bash
docker compose --env-file .env.server -f compose.server.yml logs --tail=30 backup
ls -lh backups/
```

To manually restore a selected dump, stop the bot, create a **new** PostgreSQL database, and use `pg_restore --exit-on-error --no-owner --no-acl`. Verify its content before switching the bot to that database. Do not restore over the live database.

## Dashboard browser checks

From a trusted test machine with access to the local dashboard, run `pnpm --dir dashboard install --frozen-lockfile`, `pnpm --dir dashboard exec playwright install chromium`, then `pnpm --dir dashboard test:e2e`. Tests cover desktop, tablet, and phone widths. Set `DASHBOARD_TEST_URL` to the approved staging URL when testing remotely.

## Optional tracing

Set `OTEL_EXPORTER_OTLP_ENDPOINT` in `.env.server` to an OTLP HTTP collector URL to send dashboard request duration and error traces. No Telegram IDs, names, search queries, or profile photos are added as span attributes. If unset, tracing is disabled. Redis is not part of this single-instance deployment.
