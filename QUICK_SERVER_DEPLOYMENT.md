# Always-on server test without a domain

This runs the existing bot, PostgreSQL, dashboard, backup service, and a Cloudflare Quick Tunnel on a Linux VPS. Cloudflare does not host the bot or database. A VPS that stays powered on is required. Quick Tunnels are for testing, have no uptime guarantee, and receive a new `trycloudflare.com` URL whenever the tunnel restarts.

The Quick Tunnel URL is public, but the dashboard requires a one-time code from `/dashboard` in a private Telegram chat with a configured management administrator. Do not disable `DASHBOARD_AUTH_REQUIRED` on this server. The protected Quick Tunnel `--allowed-mail` option currently has a [reported session issue](https://github.com/cloudflare/cloudflared/issues/1752), so this setup uses the application's own sign-in.

## 1. Prepare the VPS

Use a Linux VPS with SSH and Docker Engine plus the Docker Compose plugin. Follow [Docker's installation instructions](https://docs.docker.com/engine/install/) for your distribution. Allow outbound HTTPS and Cloudflare Tunnel connectivity; no inbound dashboard or PostgreSQL port is needed. Keep only SSH open to your administrators.

Clone the public repository on the VPS. Do **not** put private files in GitHub:

```sh
git clone https://github.com/phearomITE/Dashboard-SA.git sa-dashboard
cd sa-dashboard
mkdir -p private-data backups
cp .env.quick-server.example .env.quick-server
```

Copy your existing bot `.env` and these files from the Windows project to the VPS using WinSCP or SFTP:

| Windows project file | VPS destination |
| --- | --- |
| `.env` | `sa-dashboard/.env` |
| `app/data/master_sa_20261114.json` | `sa-dashboard/private-data/master_sa_20261114.json` |
| `app/data/dealer_stations.csv` | `sa-dashboard/private-data/dealer_stations.csv` |
| `app/data/controller_assignments.json` | `sa-dashboard/private-data/controller_assignments.json` |

Set a strong `DB_PASSWORD` in `.env.quick-server`. It must match the PostgreSQL credentials used by the bot. Keep `.env`, `.env.quick-server`, and `private-data` readable only by the VPS administrator. If you restore an existing PostgreSQL data volume rather than a dump, use its existing password.

## 2. Copy the existing database

On Windows PowerShell, from `D:\Bot\telegram_sa_location_alert_bot`, create a consistent PostgreSQL dump while the local database is running. If your `.env` uses a different DB user or name, replace `postgres` or `alert_message` below.

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker exec telegram_sa_alert_postgres pg_dump -U postgres -d alert_message -Fc -f /tmp/sa-migrate.dump
docker cp telegram_sa_alert_postgres:/tmp/sa-migrate.dump .\backups\sa-migrate.dump
```

Transfer `backups/sa-migrate.dump` to the VPS `sa-dashboard/backups/sa-migrate.dump` with WinSCP or SFTP. Keep the dump private. On the VPS, start only PostgreSQL, then restore the dump **before** starting the bot:

```sh
docker compose --env-file .env.quick-server -f compose.quick-server.yml config --quiet
docker compose --env-file .env.quick-server -f compose.quick-server.yml up -d postgres
docker cp backups/sa-migrate.dump "$(docker compose --env-file .env.quick-server -f compose.quick-server.yml ps -q postgres)":/tmp/sa-migrate.dump
docker compose --env-file .env.quick-server -f compose.quick-server.yml exec -T postgres pg_restore --exit-on-error --no-owner --no-acl -U postgres -d alert_message /tmp/sa-migrate.dump
```

The restore commands assume a **new, empty** VPS database. If data already exists there, stop and choose a separate restore database rather than overwriting it. Confirm the restore succeeded before moving the bot.

## 3. Move the bot and start the server stack

Stop the local Windows bot first so two instances do not poll the same Telegram bot token:

```powershell
docker compose stop bot
```

Then on the VPS:

```sh
docker compose --env-file .env.quick-server -f compose.quick-server.yml up -d --build
docker compose --env-file .env.quick-server -f compose.quick-server.yml ps
docker compose --env-file .env.quick-server -f compose.quick-server.yml logs --tail=50 cloudflared
```

Look for `Your quick Tunnel has been created!` and copy the new `https://...trycloudflare.com` URL from the `cloudflared` logs. Open it. The dashboard should show **Workspace sign-in**. In a private Telegram chat with the bot, send `/dashboard` from an account whose ID is in the private `global_management_user_ids` list, then enter its one-time code on the dashboard. Verify the roster and reports load.

Check backup logs and copy verified dumps to separate storage:

```sh
docker compose --env-file .env.quick-server -f compose.quick-server.yml logs --tail=50 backup
ls -lh backups/
```

## Limits and later production setup

The URL changes if `cloudflared` restarts, even when the VPS stays online. Check `cloudflared` logs for the new URL after any reboot. A Quick Tunnel has no uptime guarantee. For a stable hostname and Cloudflare Access, move to a named tunnel with a domain you control and follow `SERVER_DEPLOYMENT.md`.

Never reuse an old exposed tunnel token. This Quick Tunnel setup does not use a token or the existing `sa-dashboard` named tunnel.
