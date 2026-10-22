@echo off
rem eSway ERP 2.0 - run it by hand on this computer (works from any folder).
rem  1. starts the PostgreSQL database in Docker   2. updates the database   3. starts the app on http://127.0.0.1:8000/erp/login
rem  Optional: RUN-LOCAL.cmd 8002   (a different port, listening on all network cards so a port forward / dev tunnel can reach it)
setlocal
set ROOT=%~dp0
set PORT=%1
if "%PORT%"=="" set PORT=8000
set HOST=127.0.0.1
if not "%PORT%"=="8000" set HOST=0.0.0.0
cd /d "%ROOT%"
if not exist "%ROOT%.env" (
  echo No .env file found. Copy .env.example to .env and fill it in first - see docs\RUN-AND-DEPLOY-GUIDE.md
  pause & exit /b 1
)
docker info >nul 2>&1 || ( echo Starting Docker Desktop... & start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" & timeout /t 40 >nul )
docker compose up -d postgres
timeout /t 8 >nul
cd /d "%ROOT%backend"
if not exist ".venv\Scripts\python.exe" ( python -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt )
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m app.seed
echo.
echo Open  http://127.0.0.1:%PORT%/erp/login   (Ctrl+C stops the app)
.venv\Scripts\python.exe -m uvicorn app.main:app --host %HOST% --port %PORT%
