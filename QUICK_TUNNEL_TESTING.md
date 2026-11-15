# Temporary Cloudflare dashboard test without a domain

This is a development preview. The bot and PostgreSQL still run on your computer; Cloudflare provides a temporary URL. Docker Desktop and the tunnel process must stay running. The URL changes when the tunnel restarts.

The local Compose stack requires dashboard sign-in. Only Telegram IDs in the private `app/data/controller_assignments.json` `global_management_user_ids` list can obtain a code with `/dashboard`. Never commit that file or the bot token.

## Start

From PowerShell in the project directory:

```powershell
docker compose up -d --build
docker compose ps
docker run --rm -it --network telegram_sa_location_alert_bot_default cloudflare/cloudflared:latest tunnel --url http://bot:8080
```

Open the new `https://...trycloudflare.com` URL printed by `cloudflared`. In a private chat with the Telegram bot, send `/dashboard` from a management administrator account. Paste the single-use code into the dashboard sign-in form within five minutes. Keep the tunnel PowerShell window open.

Do not add `--allowed-mail` to this testing command. Cloudflare's protected Quick Tunnel sign-in currently has a [reported session-expiration issue](https://github.com/cloudflare/cloudflared/issues/1752). The dashboard itself checks the Telegram administrator code and issues an HTTP-only session cookie lasting eight hours. Opening the link without that code does not show the private roster. Any administrator who has the link and can request a valid Telegram code may sign in; an email address alone cannot.

## Check

```powershell
docker compose ps
docker compose logs --tail 30 bot
```

`bot` and `postgres` should be healthy. Open the tunnel URL and confirm it shows **Workspace sign-in** before entering a code. After sign-in, refresh the roster page and verify it loads. If the bot says access is limited to management administrators, check the private administrator ID configuration. Do not share IDs or codes publicly.

The separately created named Cloudflare tunnel `sa-dashboard` does not supply the Quick Tunnel URL and needs no route for this test. For a stable, supported deployment, use a domain and follow `SERVER_DEPLOYMENT.md`.
