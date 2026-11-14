# Track SA Alert Bot — Production V5

Build: `20260809-telegram-id-authoritative-v6`

## Identity guarantees

- Telegram numeric User ID is the permanent identity.
- Name and username refresh automatically whenever a bound user sends any
  group activity.
- Region, dealer, position and working schedule remain controlled by the
  official roster/management commands.
- Current alerts use the newest profile name and numeric-ID mention.
- Old username/name rows are merged into the canonical ID and cannot remain as
  a second missing-alert user.
- No-username initial binding uses a unique group-scoped identity core; an
  ambiguous name is rejected and requires `/bind_sa`.
- Allowed positions remain `SA`, `Commando`, and `SA Team Leader` only.
- Bound-row lookup and missing-reason authorization use numeric Telegram User
  ID only; a changed or reassigned username cannot select another user's row.
- Startup migration merges duplicate rows sharing one Telegram ID before the
  database unique index is enforced and preserves their form/GPS references.

## Production operation

- PostgreSQL and bot run under Docker Compose.
- Bot starts automatically after PostgreSQL is healthy.
- Both services restart automatically unless intentionally stopped.
- Database advisory lock prevents two schedulers using the same database.
- Scheduler blocks alerts when Telegram polling is unhealthy.
- Docker health checks and rotating Docker logs are enabled.
- HTTP transport INFO logs are disabled so Telegram tokens are not printed.
- Scheduler and database lock shut down cleanly.

## Commands

Start/update:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

Stop:

```powershell
docker compose down
```

Do not also run `python -m app.main` when the Docker bot service is running.
