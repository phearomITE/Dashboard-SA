$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Resolve-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c "import sys; print(sys.executable)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @("py", "-3.12")
        }
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return @($candidate)
        }
    }

    throw "Python 3.12 was not found. Install it from python.org and enable the 'py launcher' option."
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    $pythonCommand = @(Resolve-Python312)
    Write-Host "Creating Windows virtual environment..."
    if ($pythonCommand.Count -eq 2) {
        & $pythonCommand[0] $pythonCommand[1] -m venv .venv
    } else {
        & $pythonCommand[0] -m venv .venv
    }
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment creation failed: $venvPython does not exist."
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed." }
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
    pnpm --dir dashboard install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw "Installing dashboard dependencies failed." }
    pnpm --dir dashboard run build
    if ($LASTEXITCODE -ne 0) { throw "Building the dashboard failed." }
} elseif (-not (Test-Path "dashboard/dist/index.html")) {
    throw "Install Node.js 22.12+ and pnpm 10 to build the dashboard, or run docker compose up -d --build instead."
}
& $venvPython -m compileall -q app scripts

$validationScripts = @(
    "validate_20_minute_schedules.py",
    "validate_project.py",
    "validate_new_features.py",
    "validate_all_groups_no_wrong_mentions.py",
    "validate_atomic_photo_gps.py",
    "test_management_command_router.py",
    "validate_dashboard.py",
    "audit_management_commands.py"
)
foreach ($validationScript in $validationScripts) {
    & $venvPython (Join-Path "scripts" $validationScript)
    if ($LASTEXITCODE -ne 0) { throw "Validation failed in $validationScript." }
}

# Run one polling bot on Windows with PostgreSQL in Docker.
docker compose stop bot 2>$null
docker compose up -d postgres
if ($LASTEXITCODE -ne 0) {
    throw "Starting PostgreSQL failed."
}

$databaseReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    docker compose exec -T postgres pg_isready *> $null
    if ($LASTEXITCODE -eq 0) {
        $databaseReady = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $databaseReady) {
    throw "PostgreSQL did not become ready within 60 seconds."
}

Write-Host "Starting Track_SA_alert and its dashboard..." -ForegroundColor Green
& $venvPython -m app.main
