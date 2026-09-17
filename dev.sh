#!/usr/bin/env bash
# Starts backend (FastAPI/uvicorn on :8001) and frontend (Vite on :5173)
# together. Kills whatever is already listening on those ports first, so
# the script is safe to re-run.
#
# Usage (Git Bash / MSYS on Windows):  ./dev.sh

set -e

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stop_port() {
    local port="$1"
    local pids
    pids=$(netstat -ano 2>/dev/null | grep -E "^ *TCP" | grep -E "[:.]${port}[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u)
    for pid in $pids; do
        taskkill //PID "$pid" //F >/dev/null 2>&1 || true
    done
}

stop_port 8001
stop_port 5173
sleep 1

cd "$root/backend"
nohup .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 > "$root/backend_run.log" 2>&1 &
backend_pid=$!

cd "$root/frontend"
nohup npm run dev > "$root/frontend_run.log" 2>&1 &
frontend_pid=$!

cd "$root"
echo "$backend_pid" > backend.pid
echo "$frontend_pid" > frontend.pid

sleep 2

echo "backend  pid=$backend_pid  log=backend_run.log  http://127.0.0.1:8001"
echo "frontend pid=$frontend_pid  log=frontend_run.log http://localhost:5173"
echo ""
echo "Открой фронтенд: http://localhost:5173"
