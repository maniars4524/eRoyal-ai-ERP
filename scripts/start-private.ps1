# Starts the live eSway ERP 2.0 app on port 8000, listening on all network cards (so other computers on the network / Tailscale can open it).
# Safe to run repeatedly: it stops any previous copy first.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null

if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
}
for ($i = 0; $i -lt 72; $i++) { & $docker info *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 5 }
& $docker compose -f (Join-Path $root "docker-compose.yml") up -d postgres | Out-Null
Start-Sleep 8

Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep 2

$env:ENV_FILE = ".env"
$env:PUBLIC_PORTALS = ""
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
Push-Location (Join-Path $root "backend")
& $py -m alembic upgrade head *> (Join-Path $logs "private-migrate.log")
& $py -m app.seed *> (Join-Path $logs "private-seed.log")
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000" `
    -RedirectStandardOutput (Join-Path $logs "private-app.log") -RedirectStandardError (Join-Path $logs "private-app.err.log")
Pop-Location
