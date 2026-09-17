# Stops backend (:8001) and frontend (:5173) started by dev.ps1.
#
# Usage:  powershell -File stop.ps1

$root = $PSScriptRoot

function Stop-Port($port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Write-Host "stopping pid=$($c.OwningProcess) on port $port"
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

Stop-Port 8001
Stop-Port 5173

Remove-Item "$root\backend.pid" -ErrorAction SilentlyContinue
Remove-Item "$root\frontend.pid" -ErrorAction SilentlyContinue

Write-Host "done"
