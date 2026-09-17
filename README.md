# Impulse

## Запуск (backend + frontend)

### Вариант 1 — одной командой (рекомендуется)

В корне проекта есть скрипт `dev.ps1`, который поднимает и backend, и frontend, убивая старые процессы на портах 8001/5173:

```powershell
powershell -File dev.ps1
```

После этого:
- Backend: http://127.0.0.1:8001 (логи → `backend_run.log`, pid → `backend.pid`)
- Frontend: http://127.0.0.1:5173 (логи → `frontend_run.log`, pid → `frontend.pid`)

Открой в браузере **http://127.0.0.1:5173**.

Чтобы остановить оба процесса:

```powershell
powershell -File stop.ps1
```

(либо просто перезапусти `dev.ps1` — он сам убивает старые процессы перед запуском новых).

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
