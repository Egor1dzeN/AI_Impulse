#!/usr/bin/env bash
# Updates a server deployment (Ubuntu, systemd services ai-impulse-backend / ai-impulse-frontend).
#
#   scripts/deploy.sh          — pull master from the repository and apply the changes
#   scripts/deploy.sh --force  — rebuild the frontend and restart everything, even if the code hasn't changed
#
# What it does: git pull → if requirements.txt changed, installs backend dependencies (torch: CPU build),
# if package*.json changed, runs npm ci, if frontend/ changed, builds the frontend; restarts only the
# affected services and waits until the backend answers /api/health. backend/.env is never touched.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
force=0
[ "${1:-}" = "--force" ] && force=1

old=$(git rev-parse HEAD)
git pull --ff-only
new=$(git rev-parse HEAD)

if [ "$old" = "$new" ] && [ "$force" = 0 ]; then
    echo "No new commits ($(git log --oneline -1)). To force a restart: scripts/deploy.sh --force"
    exit 0
fi

changed() {  # whether the paths changed between the old and the new version
    [ "$force" = 1 ] && return 0
    ! git diff --quiet "$old" "$new" -- "$@"
}

echo "== $(git log --oneline -1 "$old") → $(git log --oneline -1 "$new")"
git --no-pager log --oneline "$old..$new" 2>/dev/null | sed 's/^/   /' || true

if changed backend/requirements.txt; then
    echo "== Backend dependencies"
    grep -vE "^--extra-index-url|^torch" backend/requirements.txt > /tmp/req-cpu.txt
    backend/.venv/bin/pip install -q torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
    backend/.venv/bin/pip install -q -r /tmp/req-cpu.txt
fi

frontend_changed=0
if changed frontend/package.json frontend/package-lock.json; then
    echo "== Frontend dependencies"
    (cd frontend && npm ci --no-audit --no-fund --loglevel=error)
fi
if changed frontend/; then
    echo "== Building the frontend"
    (cd frontend && npm run build --silent >/dev/null)
    frontend_changed=1
fi

if changed backend/ memory/; then
    echo "== Restarting the backend"
    sudo systemctl restart ai-impulse-backend
    for i in $(seq 1 40); do
        if curl -s -m 3 http://127.0.0.1:8001/api/health >/dev/null; then ok=1; break; fi
        sleep 3
    done
    if [ "${ok:-0}" != 1 ]; then
        echo "!! The backend didn't respond within 2 minutes. Logs: journalctl -u ai-impulse-backend -n 50"
        echo "   To roll back: git reset --hard $old && scripts/deploy.sh --force"
        exit 1
    fi
    echo "   backend is up: $(curl -s http://127.0.0.1:8001/api/health)"
fi

if [ "$frontend_changed" = 1 ]; then
    echo "== Restarting the frontend"
    sudo systemctl restart ai-impulse-frontend
    sleep 3
fi

echo "== Done: $(git log --oneline -1)"
systemctl is-active ai-impulse-backend ai-impulse-frontend | paste -sd' ' | sed 's/^/   services: /'
