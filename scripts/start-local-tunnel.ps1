param()

$ErrorActionPreference = 'Stop'

$secureCommand = Read-Host 'Paste the Cloudflare tunnel token or full Docker command (input is hidden)' -AsSecureString
$command = [System.Net.NetworkCredential]::new('', $secureCommand).Password
$secureCommand.Dispose()
$match = [regex]::Match($command.Trim(), '(?i)(?:^|--token\s+["'']?)(eyJ[A-Za-z0-9_-]{80,})["'']?\s*$')
if (-not $match.Success) {
    throw 'The pasted text does not contain a full tunnel token. Rotate the exposed token and copy a fresh token from Cloudflare.'
}

$token = $match.Groups[1].Value
$network = 'telegram_sa_location_alert_bot_default'
$container = 'sa-dashboard-tunnel'

try {
    docker network inspect $network *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Dashboard Docker network is missing. Run docker compose up -d in this project first.'
    }

    $existing = docker ps -a --filter "name=^/${container}$" --format '{{.Names}}'
    if ($existing) {
        throw "Container $container already exists. Check it with: docker logs --tail 20 $container"
    }

    docker run -d --name $container --restart unless-stopped --network $network --env "TUNNEL_TOKEN=$token" cloudflare/cloudflared:latest tunnel --no-autoupdate run | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker could not start the tunnel container.'
    }

    Write-Output "Tunnel container started. Check Cloudflare for an active replica."
    docker logs --tail 10 $container
} finally {
    $token = $null
    $command = $null
    # Windows PowerShell rejects an empty clipboard value; a space replaces the secret.
    Set-Clipboard -Value ' '
}
