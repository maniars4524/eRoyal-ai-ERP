# Daily backup of the live database (esway_erp) from the Docker PostgreSQL into E:\eRoyal-ai-ERP\backups (the folder is git-ignored).
# Keeps the newest 30 dumps. Restore one with:  docker exec -i esway-postgres pg_restore -U esway -d esway_erp --clean < backups\esway_erp_YYYYMMDD_HHMM.dump
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$dir = Join-Path $root "backups"
New-Item -ItemType Directory -Force $dir | Out-Null
$name = "esway_erp_{0}.dump" -f (Get-Date -Format "yyyyMMdd_HHmm")
$tmp = "/tmp/$name"
& $docker exec esway-postgres pg_dump -U esway -d esway_erp -Fc -f $tmp
if ($LASTEXITCODE -eq 0) {
    & $docker cp "esway-postgres:$tmp" (Join-Path $dir $name)
    & $docker exec esway-postgres rm -f $tmp
    Get-ChildItem $dir -Filter "esway_erp_*.dump" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item -Force
    Add-Content (Join-Path $dir "backup.log") ("{0} OK {1}" -f (Get-Date -Format s), $name)
} else {
    Add-Content (Join-Path $dir "backup.log") ("{0} FAILED (is Docker running?)" -f (Get-Date -Format s))
}
