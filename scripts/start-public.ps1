# Starts the public eSway ERP 2.0 stack: PostgreSQL (Docker), the app on port 8001 (its own database),
# and a Cloudflare quick tunnel. The public URL is written to PUBLIC_URL.txt in the project root.
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

Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2

$env:ENV_FILE = ".env.demo"
$env:PUBLIC_PORTALS = "demo,erp,developer"
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
Push-Location (Join-Path $root "backend")
& $py -m alembic upgrade head *> (Join-Path $logs "public-migrate.log")
& $py -m app.seed *> (Join-Path $logs "public-seed.log")
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001" `
    -RedirectStandardOutput (Join-Path $logs "public-app.log") -RedirectStandardError (Join-Path $logs "public-app.err.log")
Pop-Location

for ($i = 0; $i -lt 30; $i++) {
    try { if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8001/api/health" -TimeoutSec 3).StatusCode -eq 200) { break } } catch { }
    Start-Sleep 2
}

$cf = Join-Path $root "tools\cloudflared.exe"
$tunnelLog = Join-Path $logs "tunnel.log"
Remove-Item $tunnelLog -ErrorAction SilentlyContinue
Start-Process -WindowStyle Hidden -FilePath $cf -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8001" -RedirectStandardError $tunnelLog

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep 2
    if (Test-Path $tunnelLog) {
        $m = Select-String -Path $tunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $m.Matches[0].Value | Set-Content -Path (Join-Path $root "PUBLIC_URL.txt") -Encoding ascii; break }
    }
}
