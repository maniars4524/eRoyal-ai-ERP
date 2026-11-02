@echo off
rem Installs the watchdog (run once). Every minute it checks the application and repairs it: Docker + database, one clean server, and the public link.
schtasks /create /tn "eSwayERP-KeepAlive" /sc minute /mo 1 /tr "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%~dp0scripts\keep-alive.ps1\"" /f
echo.
echo Done. The application is now checked every minute, also when Visual Studio Code is closed (while this PC is on and you are signed in).
echo To remove it:  schtasks /delete /tn "eSwayERP-KeepAlive" /f
pause
