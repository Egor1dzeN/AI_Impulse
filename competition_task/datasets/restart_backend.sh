#!/usr/bin/env bash
# Перезапуск бэкенда для прогонов оценки: без --reload (правки кода во время прогона не рвут его),
# логи — в backend/server.*.log. .env подхватывает само приложение (app/__init__.py).
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
for pid in $(netstat -ano 2>/dev/null | grep -E "[:.]8001[[:space:]].*LISTENING" | awk '{print $NF}' | sort -u); do
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
done
# multiprocessing-воркеры старого uvicorn --reload могут пережить родителя и держать сокет
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -match 'uvicorn|spawn_main' -and \$_.CommandLine -match 'python' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1
sleep 3
cd "$root/backend"
nohup .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 > server.out.log 2> server.err.log &
until curl -s -m 3 http://127.0.0.1:8001/api/health >/dev/null; do sleep 2; done
echo "backend up, pid $(netstat -ano | grep -E '[:.]8001[[:space:]].*LISTENING' | awk '{print $NF}' | head -1)"
