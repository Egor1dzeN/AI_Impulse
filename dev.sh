#!/usr/bin/env bash
# Debug launch: starts backend (FastAPI/uvicorn on :8001) and frontend
# (Vite on :5173) together, both with hot reload on file changes:
#   - backend:  uvicorn --reload watches backend/app/**/*.py and restarts
#               the server automatically (scoped to that folder only —
#               see the --reload-dir flag below).
#   - frontend: Vite's dev server has HMR built in, no flag needed.
# Kills whatever is already listening on those ports first, so the script
# is safe to re-run.
#
# Frontend runs detached in the background (its own process, logged to
# frontend_run.log). Backend runs in THIS process's foreground, streamed
# straight to the console (and tee'd to backend_run.log) — that's the one
# you're usually watching/debugging, so its logs shouldn't be hidden in a
# file. Stop everything with Ctrl+C (or ./stop.sh from another terminal).
#
# Want full live logs for BOTH instead of frontend's being backgrounded?
# Run ./backend.sh and ./frontend.sh in two separate terminals instead.
#
# Usage (Git Bash / MSYS on Windows):  ./dev.sh
#
# Note: the memory/ package (project root, outside backend/app) is NOT
# watched, so editing it needs a manual re-run of this script. That's
# intentional — the heavy local models (Qwen3-4B, embedder, reranker,
# GLiNER, ...) are loaded lazily into that same process on first use, and
# an uvicorn --reload restart drops them, so an unrelated backend/app edit
# should not force a slow model reload the next time you send a message.

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
        # across restarts (see stop.sh for the full explanation).
        taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    done
}

stop_port 8001
stop_port 5173
sleep 1

# --- frontend: install deps if missing, launch detached in background ---
cd "$root/frontend"
if [ ! -d "node_modules" ]; then
    echo "frontend/node_modules не найден, ставлю зависимости..."
    npm install
fi

nohup npm run dev > "$root/frontend_run.log" 2>&1 &
frontend_pid=$!

cd "$root"
echo "$frontend_pid" > frontend.pid

echo "frontend pid=$frontend_pid  log=frontend_run.log  http://localhost:5173"
echo ""

# --- backend: create venv + install deps if missing ---
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
# Runs in the foreground (no `&`/nohup) so backend logs stream straight to
# this console; `tee` also keeps them in backend_run.log for later lookup.
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8001 --reload --reload-dir app \
    2>&1 | tee "$root/backend_run.log"
