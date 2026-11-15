# SA Location Alert Bot dashboard

Telegram field reporting bot with a PostgreSQL backed operations dashboard. It includes a live map, SA roster, annual leave, missing report analysis, and controller management.

## Local development

1. Copy `.env.server.example` to `.env` and supply your own Telegram bot token and database password.
2. Place the private roster, dealer coordinates, and controller assignments in `app/data/`. Their filenames and formats are described in `SERVER_DEPLOYMENT.md`. They are excluded from Git and Docker images.
3. Run `docker compose up -d --build` and open `http://127.0.0.1:8080`. Send `/dashboard` to the bot in a private Telegram chat from a configured management administrator account to receive a single-use sign-in code.

## Temporary Cloudflare testing without a domain

Use [QUICK_TUNNEL_TESTING.md](QUICK_TUNNEL_TESTING.md). This test route uses the dashboard's Telegram administrator sign-in, since Cloudflare's protected Quick Tunnel email sessions currently have a reported expiration issue. Do not remove dashboard authentication when sharing a Quick Tunnel link.

## Server deployment

See [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md) for PostgreSQL backups, Cloudflare Tunnel and Access configuration, health checks, and restore testing. Set up Cloudflare Access before making the tunnel route public.

## Verification

- Dashboard checks: `docker run --rm --network none -e TELEGRAM_BOT_TOKEN=123456:offline-validation -v <private-roster-path>:/app/app/data/master_sa_20261114.json:ro -v <private-controller-config-path>:/app/app/data/controller_assignments.json:ro telegram_sa_location_alert_bot-bot python -m scripts.validate_dashboard`
- Browser checks: `cd dashboard && pnpm test:e2e`
