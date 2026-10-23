@echo off
rem Lets other computers on the same network open the ERP (port 8000). Right-click this file -> Run as administrator.
net session >nul 2>&1
if errorlevel 1 (
  echo This needs administrator rights: right-click ALLOW-NETWORK.cmd and choose "Run as administrator".
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="eSway ERP 8000 (LAN)" >nul 2>&1
netsh advfirewall firewall add rule name="eSway ERP 8000 (LAN)" dir=in action=allow protocol=TCP localport=8000 profile=any remoteip=localsubnet
echo.
echo Done. Other computers on this network can now open  http://%COMPUTERNAME%:8000/erp/login  or  http://THIS-PC-IP:8000/erp/login
ipconfig | findstr /i "IPv4"
pause
