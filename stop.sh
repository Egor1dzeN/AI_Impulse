#!/usr/bin/env bash
# Stops backend (:8001) and frontend (:5173) started by dev.sh.
#
# Usage (Git Bash / MSYS on Windows):  ./stop.sh

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stop_port() {
    local port="$1"
    local pids
    pids=$(netstat -ano 2>/dev/null | grep -E "^ *TCP" | grep -E "[:.]${port}[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u)
    for pid in $pids; do
        echo "stopping pid=$pid (and its process tree) on port $port"
        # //T kills the whole process tree, not just the listening pid.
        # This matters because the backend runs with uvicorn --reload,
        # which is TWO processes (a reloader that owns the socket, plus a
        # worker child that actually loads the GPU models) — killing only
        # the reloader by pid leaves the model-loaded worker running as an
        # orphan, invisible here and still holding VRAM.
        taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    done
}

stop_port 8001
stop_port 5173

rm -f "$root/backend.pid" "$root/frontend.pid"

echo "done"
