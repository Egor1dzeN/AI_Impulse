#!/usr/bin/env bash
# Обновление стенда на сервере (Ubuntu, systemd-сервисы ai-impulse-backend / ai-impulse-frontend).
#
#   ./deploy.sh          — подтянуть master из репозитория и применить изменения
#   ./deploy.sh --force  — пересобрать фронтенд и перезапустить всё, даже если код не менялся
#
# Что делает: git pull → при изменении requirements.txt ставит зависимости бэкенда (torch — CPU-сборка),
# при изменении package*.json — npm ci, при изменении frontend/ — сборка фронтенда; перезапускает только
# затронутые сервисы и ждёт, пока бэкенд ответит на /api/health. backend/.env скрипт не трогает.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"
force=0
[ "${1:-}" = "--force" ] && force=1

old=$(git rev-parse HEAD)
git pull --ff-only
new=$(git rev-parse HEAD)

if [ "$old" = "$new" ] && [ "$force" = 0 ]; then
    echo "Нет новых коммитов ($(git log --oneline -1)). Для принудительного перезапуска: ./deploy.sh --force"
    exit 0
fi

changed() {  # есть ли изменения в путях между старой и новой версией
    [ "$force" = 1 ] && return 0
    ! git diff --quiet "$old" "$new" -- "$@"
}

echo "== $(git log --oneline -1 "$old") → $(git log --oneline -1 "$new")"
git --no-pager log --oneline "$old..$new" 2>/dev/null | sed 's/^/   /' || true

if changed backend/requirements.txt; then
    echo "== Зависимости бэкенда"
    grep -vE "^--extra-index-url|^torch" backend/requirements.txt > /tmp/req-cpu.txt
    backend/.venv/bin/pip install -q torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
    backend/.venv/bin/pip install -q -r /tmp/req-cpu.txt
fi

frontend_changed=0
if changed frontend/package.json frontend/package-lock.json; then
    echo "== Зависимости фронтенда"
    (cd frontend && npm ci --no-audit --no-fund --loglevel=error)
fi
if changed frontend/; then
    echo "== Сборка фронтенда"
    (cd frontend && npm run build --silent >/dev/null)
    frontend_changed=1
fi

if changed backend/ memory/; then
    echo "== Перезапуск бэкенда"
    sudo systemctl restart ai-impulse-backend
    for i in $(seq 1 40); do
        if curl -s -m 3 http://127.0.0.1:8001/api/health >/dev/null; then ok=1; break; fi
        sleep 3
    done
    if [ "${ok:-0}" != 1 ]; then
        echo "!! Бэкенд не ответил за 2 минуты. Логи: journalctl -u ai-impulse-backend -n 50"
        echo "   Откатиться: git reset --hard $old && ./deploy.sh --force"
        exit 1
    fi
    echo "   бэкенд отвечает: $(curl -s http://127.0.0.1:8001/api/health)"
fi

if [ "$frontend_changed" = 1 ]; then
    echo "== Перезапуск фронтенда"
    sudo systemctl restart ai-impulse-frontend
    sleep 3
fi

echo "== Готово: $(git log --oneline -1)"
systemctl is-active ai-impulse-backend ai-impulse-frontend | paste -sd' ' | sed 's/^/   сервисы: /'
