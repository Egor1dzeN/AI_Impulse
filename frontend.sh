#!/usr/bin/env bash
# Starts ONLY the frontend (Vite dev server on :5173), in the foreground —
# logs stream straight into this console (and are tee'd to frontend_run.log
# too, for later lookup). Stop with Ctrl+C.
#
# Run this in its own terminal window alongside ./backend.sh in another —
# that way each process gets a clean, uninterrupted log instead of the two
# being interleaved or one of them hidden in a background job.
#
# For "both at once in one terminal" use ./dev.sh instead.
#
# Usage (Git Bash / MSYS on Windows): ./frontend.sh

set -e

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stop_port() {
    local port="$1"
    local pids
    pids=$(netstat -ano 2>/dev/null | grep -E "^ *TCP" | grep -E "[:.]${port}[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u)
    for pid in $pids; do
        taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    done
}

stop_port 5173
sleep 1

cd "$root/frontend"
if [ ! -d "node_modules" ]; then
    echo "frontend/node_modules не найден, ставлю зависимости..."
    npm install
fi

echo "frontend  http://localhost:5173  (логи ниже, также пишутся в frontend_run.log; Ctrl+C — стоп)"
echo ""

npm run dev 2>&1 | tee "$root/frontend_run.log"
