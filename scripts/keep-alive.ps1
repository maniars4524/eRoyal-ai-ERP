# eSway ERP 2.0 - keeps the application running. Scheduled every minute (task "eSwayERP-KeepAlive"); safe to run by hand.
# If the application does not answer: starts Docker + the database when they are off, removes stale server copies, starts ONE server on port 8000,
# and makes sure the public Tailscale link is on.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "keepalive.log"
function Log($m) { Add-Content -Path $log -Value ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $m) }
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Move-Item $log "$log.old" -Force }

# one run at a time (a lock older than 4 minutes is ignored)
$lock = Join-Path $logDir "keepalive.lock"
if ((Test-Path $lock) -and (((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes -lt 4)) { exit 0 }
Set-Content $lock (Get-Date)

function Healthy {
    try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 6 http://127.0.0.1:8000/api/health; return ($r.StatusCode -eq 200 -and $r.Content -match '"database":"healthy"') } catch { return $false }
}
function DbUp {
    $n = docker ps --filter "name=esway-postgres" --format "{{.Status}}" 2>$null
    return ($n -match "Up")
}

if (-not (Healthy)) {
    Log "application not healthy - repairing"
    # 1. Docker and the database
    if (-not (DbUp)) {
        if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
            Log "starting Docker Desktop"
            Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
            Start-Sleep 60
        }
        for ($i = 0; $i -lt 12 -and -not (DbUp); $i++) { Push-Location $root; docker compose up -d postgres 2>$null | Out-Null; Pop-Location; Start-Sleep 8 }
        Log ("database up: " + (DbUp))
    }
    # 2. stale servers out, one clean server in
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "uvicorn" -and $_.Name -match "python" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep 3
    $py = Join-Path $root "backend\.venv\Scripts\python.exe"
    Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden -RedirectStandardOutput (Join-Path $root "live8000.log") -RedirectStandardError (Join-Path $root "live8000.err.log")
    for ($i = 0; $i -lt 30 -and -not (Healthy); $i++) { Start-Sleep 2 }
    Log ("server started, healthy: " + (Healthy))
}
# 3. the public link
$f = tailscale funnel status 2>&1 | Out-String
if ($f -notmatch "Funnel on") { Log "turning the public link on"; tailscale funnel --bg 8000 2>&1 | Out-Null }
Remove-Item $lock -Force
