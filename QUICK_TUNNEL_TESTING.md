# Temporary Cloudflare dashboard test without a domain

This is a development preview. The bot and PostgreSQL still run on your computer; Cloudflare provides a temporary URL. Docker Desktop and the tunnel process must stay running. The URL changes when the tunnel restarts.

The local Compose stack has dashboard sign-in disabled for this test. Anyone with the temporary URL can view and edit the roster, locations, reports, and settings. Share the URL only with your test group. Never commit the private roster, controller configuration, or bot token.

## Start

From PowerShell in the project directory:

```powershell
docker compose up -d --build
docker compose ps
docker run --rm -it --network telegram_sa_location_alert_bot_default cloudflare/cloudflared:latest tunnel --url http://bot:8080
```

Open the new `https://...trycloudflare.com` URL printed by `cloudflared`. It opens the dashboard directly. Keep the tunnel PowerShell window open.

Do not add `--allowed-mail` to this testing command. Cloudflare's protected Quick Tunnel sign-in currently has a [reported session-expiration issue](https://github.com/cloudflare/cloudflared/issues/1752). This test URL has no login gate, so possession of the link grants dashboard access. The separate server deployment described in `SERVER_DEPLOYMENT.md` uses Cloudflare Access instead.

## Check

```powershell
docker compose ps
docker compose logs --tail 30 bot
```

`bot` and `postgres` should be healthy. Open the tunnel URL and confirm it goes straight to the dashboard. Refresh the roster page and verify it loads. To end the test, stop the Quick Tunnel container or press Ctrl+C in its PowerShell window. The old URL stops working after the tunnel stops.

The separately created named Cloudflare tunnel `sa-dashboard` does not supply the Quick Tunnel URL and needs no route for this test. For a stable, supported deployment, use a domain and follow `SERVER_DEPLOYMENT.md`.
