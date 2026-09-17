# Impulse

## Запуск (backend + frontend)

### Вариант 1 — одной командой (рекомендуется)

В корне проекта есть скрипт `dev.ps1` (PowerShell) и его аналог `dev.sh` (Git Bash) — оба поднимают backend и frontend, убивают старые процессы на портах 8001/5173, а при первом запуске (или после клонирования репозитория) сами создают `backend/.venv`, ставят зависимости из `requirements.txt` и делают `npm install` во `frontend/`, если этого ещё не было.

```powershell
powershell -File dev.ps1
```

```bash
./dev.sh
```

Если PowerShell ругается на execution policy ("running scripts is disabled on this system"), используй `dev.sh` в Git Bash, либо `powershell -ExecutionPolicy Bypass -File dev.ps1`.

Первый запуск после клонирования может занять несколько минут — ставятся тяжёлые зависимости backend (torch, transformers и т.д.).

После этого:
- Backend: http://127.0.0.1:8001 (логи → `backend_run.log`, pid → `backend.pid`)
- Frontend: http://localhost:5173 (логи → `frontend_run.log`, pid → `frontend.pid`)

Открой в браузере **http://localhost:5173**.

Чтобы остановить оба процесса:

```powershell
powershell -File stop.ps1
```
```bash
./stop.sh
```

(либо просто перезапусти `dev.ps1`/`dev.sh` — он сам убивает старые процессы перед запуском новых).

### Вариант 2 — вручную, по отдельности

#### Backend (FastAPI + uvicorn, порт 8001)

Первый раз (если `backend/.venv` ещё нет):

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Запуск:

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Проверка: http://127.0.0.1:8001/api/health

#### Frontend (Vite + React, порт 5173)

Первый раз (если нет `node_modules`):

```powershell
cd frontend
npm install
```

Запуск:

```powershell
cd frontend
npm run dev
```

Откроется на http://127.0.0.1:5173 (CORS в backend уже настроен именно на этот адрес).

### Важно

- Backend **должен** быть запущен на `127.0.0.1:8001` — frontend обращается именно туда, а CORS в `backend/app/main.py` разрешён только для `localhost:5173` / `127.0.0.1:5173`.
- `.venv` и `node_modules` не коммитятся в git (уже в `.gitignore`) — их нужно создавать/ставить заново на новой машине.
