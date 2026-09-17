# Starts backend (FastAPI/uvicorn on :8001) and frontend (Vite on :5173)
# together. Kills whatever is already listening on those ports first, so
# the script is safe to re-run.
#
# Usage:  powershell -File dev.ps1

$root = $PSScriptRoot

function Stop-Port($port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Stop-Port 8001
Stop-Port 5173
Start-Sleep -Seconds 1

$backend = Start-Process -FilePath "$root\backend\run_backend.cmd" `
    -WorkingDirectory "$root\backend" -WindowStyle Hidden -PassThru

$frontend = Start-Process -FilePath "$root\frontend\run_frontend.cmd" `
    -WorkingDirectory "$root\frontend" -WindowStyle Hidden -PassThru

$backend.Id | Out-File "$root\backend.pid" -Encoding ascii
$frontend.Id | Out-File "$root\frontend.pid" -Encoding ascii

Write-Host "backend  pid=$($backend.Id)  log=backend_run.log  http://127.0.0.1:8001"
Write-Host "frontend pid=$($frontend.Id)  log=frontend_run.log http://localhost:5173"
Write-Host ""
Write-Host "Открой фронтенд: http://localhost:5173"
