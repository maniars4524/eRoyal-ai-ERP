@echo off
rem eSway ERP 2.0 - start it so everybody can reach it from anywhere (this PC must stay on).
rem  1 starts the database  2 stops any old copy of the server  3 updates the database  4 starts ONE server on port 8000  5 makes sure the public Tailscale link is on
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
docker info >nul 2>&1 || ( echo Starting Docker Desktop... & start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" & timeout /t 45 >nul )
docker compose up -d postgres
timeout /t 8 >nul
rem stop every old copy of the server (two copies fight for port 8000 and the old code keeps answering)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn' -and $_.Name -match 'python' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
cd /d "%ROOT%backend"
.venv\Scripts\python.exe -m alembic upgrade head
tailscale funnel --bg 8000
echo.
echo  Public link (any network, no install):  https://desktop-touf7i0.tailf957ab.ts.net/erp/login
echo  Tailscale only:                         http://desktop-touf7i0:8000/erp/login
echo  This PC:                                http://127.0.0.1:8000/erp/login
echo  Leave this window open. Ctrl+C stops the application.
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
