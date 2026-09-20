#!/usr/bin/env bash
# Starts ONLY the backend (FastAPI/uvicorn on :8001), in the foreground —
# logs stream straight into this console (and are tee'd to backend_run.log
# too, for later lookup). Stop with Ctrl+C.
#
# Run this in its own terminal window alongside ./frontend.sh in another —
# that way each process gets a clean, uninterrupted log instead of the two
# being interleaved or one of them hidden in a background job.
#
# For "both at once in one terminal" use ./dev.sh instead.
#
# Usage (Git Bash / MSYS on Windows): ./backend.sh

set -e

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

stop_port() {
    local port="$1"
    local pids
    pids=$(netstat -ano 2>/dev/null | grep -E "^ *TCP" | grep -E "[:.]${port}[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u)
    for pid in $pids; do
        # //T kills the whole process tree, not just the listening pid.
        # uvicorn --reload is TWO processes (a reloader that owns the
        # socket, plus a worker child that actually loads the GPU models)
        # — killing only the reloader by pid leaves the model-loaded
        # worker running as an orphan that silently keeps eating VRAM
        # across restarts.
        taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    done
}

stop_port 8001
sleep 1

cd "$root/backend"
if [ ! -f ".venv/Scripts/python.exe" ]; then
    echo "backend/.venv не найден, создаю venv и ставлю зависимости (это может занять несколько минут)..."
    python -m venv .venv
    .venv/Scripts/python.exe -m pip install --upgrade pip
    .venv/Scripts/python.exe -m pip install -r requirements.txt
fi

# Local secrets (API keys etc.) — .env is git-ignored, never committed.
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

echo "backend  http://127.0.0.1:8001  (логи ниже, также пишутся в backend_run.log; Ctrl+C — стоп)"
echo ""

# PYTHONIOENCODING=utf-8: on Windows, stdout falls back to the console
# codepage (cp866/cp1251) instead of UTF-8 when not attached to a real
# console (e.g. piped through tee), so Cyrillic in print()/logging output
# turns into "?" mojibake. Force UTF-8 so both the console and the log
# file stay readable.
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8001 --reload --reload-dir app \
    2>&1 | tee "$root/backend_run.log"
