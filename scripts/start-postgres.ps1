$ErrorActionPreference = "Stop"
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"

if (-not (Test-Path $docker)) {
  throw "Docker Desktop is not installed at $docker"
}

$service = Get-Service -Name com.docker.service -ErrorAction SilentlyContinue
if ($service -and $service.Status -ne "Running") {
  Write-Host "Docker Desktop service is stopped. Run this script from an elevated PowerShell window."
  Start-Service com.docker.service
}

Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
& $docker compose -f (Join-Path $PSScriptRoot "..\docker-compose.yml") up -d postgres
& $docker compose -f (Join-Path $PSScriptRoot "..\docker-compose.yml") ps postgres
