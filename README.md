# Impulse

Персональный ассистент с чатом, долгосрочной памятью о пользователе и набором
инструментов для реальных действий (задачи, траты, питание, вес). Всё работает
**локально**: и диалоговая LLM, и вся система памяти крутятся на своей
машине (GPU настоятельно рекомендуется), без обращений к внешним API.

Стек: **FastAPI** (backend) + **React/Vite** (frontend) + **LangGraph**
(агентный цикл) + локальный **Qwen3-4B** (диалог и извлечение памяти) +
самописная модульная **система долгосрочной памяти** (`memory/`) поверх
SQLite и FAISS.

---

## Содержание

- [Быстрый запуск](#быстрый-запуск)
- [Общая архитектура](#общая-архитектура)
- [Модели и где они крутятся](#модели-и-где-они-крутятся)
- [Backend: как обрабатывается сообщение](#backend-как-обрабатывается-сообщение)
- [Инструменты агента (function calling)](#инструменты-агента-function-calling)
- [MCP-серверы](#mcp-серверы)
- [Система памяти — подробно](#система-памяти--подробно)
  - [1. Дешёвый роутер (что не стоит обрабатывать)](#1-дешёвый-роутер-cheapmemoryrouter)
  - [2. Извлечение кандидатов памяти](#2-извлечение-кандидатов-памяти)
  - [3. Обогащение (enrichment)](#3-обогащение-enrichment)
  - [4. Сопоставление с существующей памятью (matching)](#4-сопоставление-с-существующей-памятью-matching)
  - [5. Консолидация (что делать с кандидатом)](#5-консолидация-что-делать-с-кандидатом)
  - [6. Применение решения (запись в БД)](#6-применение-решения-запись-в-бд)
  - [7. Векторный индекс и поиск (retrieval)](#7-векторный-индекс-и-поиск-retrieval)
  - [8. Как память попадает в ответ агента](#8-как-память-попадает-в-ответ-агента)
- [Хранилища данных](#хранилища-данных)
- [API backend](#api-backend)
- [Frontend](#frontend)
- [Структура репозитория](#структура-репозитория)
- [Известные ограничения](#известные-ограничения)

---

## Быстрый запуск

### Вариант 1 — одной командой (рекомендуется)

В корне проекта есть `dev.ps1` (PowerShell) и его аналог `dev.sh` (Git Bash) —
оба поднимают backend и frontend, убивают старые процессы на портах
8001/5173, а при первом запуске сами создают `backend/.venv`, ставят
зависимости из `requirements.txt` и делают `npm install` во `frontend/`.

```powershell
powershell -File dev.ps1
```

```bash
./dev.sh
```

Если PowerShell ругается на execution policy, используй `dev.sh` в Git Bash
либо `powershell -ExecutionPolicy Bypass -File dev.ps1`.

**Первый запуск после клонирования занимает много времени и места** — ставятся
тяжёлые ML-зависимости (torch, transformers, bitsandbytes, faiss,
sentence-transformers, gliner), а при первом обращении к моделям Hugging Face
Hub скачает несколько гигабайт весов (Qwen3-4B, два Qwen3-0.6B, GLiNER) в
локальный кэш (`~/.cache/huggingface`).

После запуска:
- Backend: http://127.0.0.1:8001 (логи → `backend_run.log`, pid → `backend.pid`)
- Frontend: http://localhost:5173 (логи → `frontend_run.log`, pid → `frontend.pid`)

Открой в браузере **http://localhost:5173**.

Остановить оба процесса:

```powershell
powershell -File stop.ps1
```
```bash
./stop.sh
```

### Вариант 2 — вручную, по отдельности

#### Backend (FastAPI + uvicorn, порт 8001)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Проверка: http://127.0.0.1:8001/api/health

#### Frontend (Vite + React, порт 5173)

```powershell
cd frontend
npm install
npm run dev
```

### Важно

- Backend **должен** быть запущен на `127.0.0.1:8001` — frontend (через Vite
  dev-proxy, см. `frontend/vite.config.js`) обращается именно туда, а CORS в
  `backend/app/main.py` разрешён только для `localhost:5173` / `127.0.0.1:5173`.
- `.venv` и `node_modules` не коммитятся — их нужно ставить заново на новой
  машине.
- Первое сообщение в чат будет медленным: все модели (см. ниже) лениво
  загружаются в память процесса при первом обращении (`model_registry.py`,
  singleton на процесс) и остаются там до перезапуска backend.
- Без CUDA всё тоже работает, но на CPU (fp32) — очень медленно. Квантование
  (4-bit/8-bit) применяется только при наличии CUDA.

---

## Общая архитектура

```
┌──────────────────┐      SSE (/api/chat)        ┌─────────────────────────────┐
│  Frontend (Vite)  │ ─────────────────────────▶ │      FastAPI backend        │
│  React + Three.js │ ◀───────────────────────── │      (backend/app)          │
└──────────────────┘     token/tool_call/done    └───────────────┬─────────────┘
                                                                  │
                                            LangGraph agent loop  │
                                          (agent_graph.py, agent.py)
                                                                  │
                     ┌────────────────────────────────────────────┼────────────────────────┐
                     │                       │                    │                         │
             ChatQwen (chat_model.py)   Action tools         search_memory tool        MCP tools
             — рендерит system+user      (tools.py: задачи,   (tools.py → memory_bridge  (mcp_tools.py,
             промпт под Qwen3-4B,        траты, питание,      → MemorySystem.retrieve)   опционально)
             парсит ```tool_call блок    вес; SQLite app.db)
                     │
                     ▼
           memory_bridge.get_qwen_model()
           — тот же общий QwenModel,
           что и у системы памяти
                                                                  │
                                                                  ▼
                                          BackgroundTasks: memory_bridge.update_memory()
                                          (после ответа, асинхронно, не блокирует чат)
                                                                  │
                                                                  ▼
                                                  ┌───────────────────────────────┐
                                                  │     memory/ — MemorySystem     │
                                                  │  (см. раздел "Система памяти") │
                                                  └───────────────────────────────┘
```

Ключевая идея: **чат и запоминание — разные, независимые пути**.
- Ответ пользователю генерируется в `agent.handle_chat_turn_stream`, и агент
  сам решает, нужно ли ему сходить в память (`search_memory`) прежде чем
  отвечать.
- Запоминание нового (`memory_bridge.update_memory`) запускается как
  `BackgroundTasks` **после** того, как ответ уже начал стримиться —
  пользователь не ждёт извлечения памяти.

---

## Модели и где они крутятся

Все модели грузятся один раз на процесс через
`memory/model_registry.py` (`ModelRegistry`, потокобезопасный singleton) и
переиспользуются между чатом и памятью — это критично, т.к. GPU-память
ограничена.

| Модель | Роль | Квантование (CUDA) | Файл |
|---|---|---|---|
| **Qwen/Qwen3-4B** | Диалоговая LLM агента **и** LLM извлечения памяти (`UnifiedMemoryAnalyzer`, coreference, relation extraction, validation) — один и тот же экземпляр, чтобы не грузить модель дважды | 4-bit NF4 + double quant (`bitsandbytes`), можно отключить через `QWEN_4BIT=0` | `memory/qwen_model.py` |
| **Qwen/Qwen3-Embedding-0.6B** | Би-энкодер для векторного поиска по памяти (эмбеддинги запроса и содержимого memory) | 8-bit LLM.int8() | `memory/memory_embedder.py` |
| **Qwen/Qwen3-Reranker-0.6B** | Кросс-энкодер, переранжирует кандидатов из FAISS перед выдачей агенту | FP16 (без квантования — стабильность важнее для 0.6B модели) | `memory/memory_reranker.py` |
| **Qwen/Qwen3-0.6B** | Грамматическая нормализация уже извлечённых формулировок памяти (падежи, согласование) — отдельный маленький вызов пакетом | 8-bit LLM.int8() (кроме `lm_head`) | `memory/memory_grammar_normalizer.py` |
| **urchade/gliner_multi-v2.1 (GLiNER)** | NER: извлечение сущностей (person/location/organization/…) для обогащения кандидатов памяти | FP16 | `memory/entity_enricher.py` |

`ChatQwen` (`backend/app/chat_model.py`) — это адаптер `langchain_core.BaseChatModel`
вокруг того же `QwenModel`: Qwen3 нативно не поддерживает structured tool
calling LangChain, поэтому:
1. В системный промпт дописывается список доступных функций
   (`_tools_block`) в удобном для маленькой модели формате.
2. Модель просят ответить строго блоком ```` ```tool_call\n{"name": ..., "arguments": {...}}\n``` ````
   либо обычным текстом.
3. `_parse_tool_call` регэкспом достаёт JSON из ответа (поддерживает и
   ```` ```tool_call ```` , и нативный `<tool_call>` формат Qwen3) и превращает
   в `AIMessage.tool_calls` — дальше по коду (LangGraph `ToolNode`, агентный
   цикл) всё выглядит как обычный LangChain tool call, без спец-кейсов под
   конкретную модель.
4. Стриминг (`_stream`) буферизует первые символы, чтобы понять — это
   tool_call блок (тогда токены в чат не льются, ждём весь блок и парсим) или
   обычный текст (тогда льём токены во фронт по мере генерации).

Модель Qwen3-4B грузится **без** `enable_thinking` (thinking-режим отключён)
для скорости.

---

## Backend: как обрабатывается сообщение

`POST /api/chat` (`backend/app/main.py`) отдаёт `text/event-stream` (SSE) с
событиями `{"type": "token"}`, `{"type": "tool_call"}`, `{"type": "done"}`.

Обработка одного хода диалога — `agent.handle_chat_turn_stream`
(`backend/app/agent.py`):

1. Строится системный промпт (`prompts.build_system_prompt`, см. ниже) с
   сегодняшней датой.
2. Собирается **LangGraph**-граф из двух узлов (`backend/app/agent_graph.py`):
   `agent` (вызов `ChatQwen.bind_tools(...)`) → условный переход → `tools`
   (`ToolNode`) → обратно в `agent`, пока модель не ответит текстом без
   вызова функции (`END`). Лимит рекурсии — 20 супер-шагов (~10 раундов
   вызовов инструментов), после чего отдаётся заглушка "не получилось за
   отведённое число шагов".
3. Список инструментов на этот запрос (`agent_graph.build_tool_list`):
   `search_memory` + экшн-тулы (`tools.build_action_tools`, привязаны к
   конкретному `user_id`) + все MCP-инструменты (если настроены).
4. Стрим событий графа (`stream_mode=["messages", "updates"]`) построчно
   переводится в SSE-события: токены текста от узла `agent`, и
   `tool_call`/результат для каждого вызова инструмента (в `tool_call` не
   попадает `search_memory` — это внутренний ход агента, не "действие").
5. Если сгенерированный ответ на первой попытке абсолютно пуст (и не было ни
   текста, ни tool call — известная особенность 4-bit модели на холодном
   старте, иногда сразу даёт EOS), делается **одна** тихая повторная попытка
   (`MAX_ATTEMPTS = 2`), прежде чем показать пользователю заглушку.
6. И реплика пользователя, и финальный ответ ассистента сохраняются в
   `chat_messages` (SQLite, `backend/app/db.py`) — это история диалога,
   отдельная от долгосрочной памяти.
7. Параллельно (`BackgroundTasks`, не блокируя ответ) вызывается
   `agent.extract_memory_background` → `memory_bridge.update_memory` —
   независимый прогон текста пользователя через систему памяти.

### Системный промпт (`backend/app/prompts.py`)

Промпт собирается из 4 блоков + список функций, который дописывает
`ChatQwen`:
- **PERSONA** — кто есть ассистент, отвечать по-русски, кратко.
- **MEMORY_INSTRUCTIONS** — объясняет модели, что у неё есть
  `search_memory`, что решение "нужна ли память" и сколько раз её вызывать
  принимает **сама модель за один ход** (никакого автоматического
  предзагруженного контекста памяти в промпт не кладётся), и что всё
  сказанное пользователем и так само сохранится в фоне.
- **ACTION_INSTRUCTIONS** — жёсткое правило: экшн-тулы (создающие реальные
  записи) вызываются только на явную, недвусмысленную команду; рассказ о
  себе — это не команда, даже если технически похоже на данные для записи.
- **TOOL_CALL_FORMAT** — формат ```` ```tool_call ```` блока и сегодняшняя дата.

---

## Инструменты агента (function calling)

`backend/app/tools.py`, три тематических блока, каждый со своей SQLite-таблицей
в `backend/data/app.db` (`backend/app/db.py`):

| Инструмент | Действие |
|---|---|
| `create_task(title, due_date?, notes?)` | создать задачу/напоминание |
| `list_tasks()` | последние 20 задач |
| `add_expense(amount, category, date, note?)` | записать трату |
| `get_expense_summary()` | сводка трат по месяцам/категориям |
| `log_meal(name, kcal, date, protein?, fat?, carbs?)` | записать приём пищи |
| `log_weight(value_kg, date)` | записать вес |
| `get_health_summary()` | калории за сегодня относительно цели (`DAILY_KCAL_GOAL = 2200`) и история веса |
| `search_memory(query)` | (`MEMORY_TOOL_NAME`) — единственный "тул чтения", подключён к `memory_bridge.retrieve_memory_context`; не считается "действием" (не попадает в `done.action`) |

Все инструменты создаются заново на каждый запрос (`build_action_tools(user_id)`)
и замкнуты на `user_id` этого запроса — модель никогда не видит и не может
подставить `user_id` сама.

`GET /api/sandbox/{user_id}` отдаёт "песочницу" — сырой снепшот последних
задач/трат/приёмов пищи/веса для панели "Память" во фронтенде.

---

## MCP-серверы

`backend/app/mcp_tools.py` — опциональное подключение внешних MCP-серверов
через `langchain-mcp-adapters`. Пока `backend/mcp_servers.json` отсутствует
или пуст — это no-op, агент работает как описано выше, без изменений.

Чтобы подключить сервер: скопировать `backend/mcp_servers.example.json` в
`backend/mcp_servers.json` (он в `.gitignore`, не коммитится) и описать
серверы (ключ — имя, значение — конфиг в формате `MultiServerMCPClient`:
`streamable_http` с `url`, либо `stdio` с `command`/`args`). Инструменты
подтягиваются один раз при старте (`on_startup` в `main.py`) и добавляются в
общий список тулов агента наравне с локальными — без изменений в коде.

---

## Система памяти — подробно

Система памяти живёт в `memory/` (написана как отдельный, не зависящий от
FastAPI пакет — импортируется плоскими путями типа `from enricher import
...`, поэтому `backend/app/memory_bridge.py` вручную добавляет `memory/` в
`sys.path`). Единая точка входа — `MemorySystem` (`memory/memory_system.py`),
собирается один раз (`memory_bridge.get_memory_system`, тоже singleton с
блокировкой) и предоставляет 3 операции: `update(text, user_id)` (запомнить),
`retrieve(query, user_id, top_k)` (найти) и `get_all(user_id)` (всё, включая
неактивное — для панели "Память" во фронтенде).

Каждое сообщение пользователя проходит **пайплайн записи**
(`MemoryPipeline.process`, `memory/memory_pipeline.py`) через пять стадий:

```
текст ──▶ CheapMemoryRouter ──▶ MemoryEnricher.process() ──▶ для каждого кандидата:
          (skip / extract)      │                              MemoryMatcher.find_matches()
                                 │  (Router → Unified LLM /            │
                                 │   Rule-based fast-path /             ▼
                                 │   Legacy pipeline)             MemoryConsolidator.decide()
                                 ▼                                     │
                        список EnrichedMemoryCandidate                ▼
                                                                MemoryUpdater.apply()
                                                                (пишет в SQLite)
                                                                        │
                                                                        ▼
                                                        MemoryIndexManager.sync_after_pipeline()
                                                              (инкрементально обновляет FAISS)
```

### 1. Дешёвый роутер (`CheapMemoryRouter`)

Первый фильтр, **до** любых моделей (`memory/cheap_memory_router.py`).
Регэкспами отсекает то, что заведомо не память: пустые строки, короткие
реплики-подтверждения ("спасибо", "ок", "понял"), вопросы (по `?` или
вопросительному слову в начале), явные команды ("покажи", "включи",
"напомни", ...). Если сработал — `pipeline.empty_result()`, дальше пайплайн
не идёт вообще, ни один запрос к модели не тратится.

### 2. Извлечение кандидатов памяти

`MemoryEnricher.process()` (`memory/enricher.py`) выбирает один из трёх
маршрутов извлечения (в порядке приоритета):

**a) Правило-ориентированный роутер (`MemoryInputRouter`,
`memory/memory_input_router.py`)** — быстрый deterministic-слой без LLM.
Классифицирует сообщение как:
- `DROP` — вопрос/команда/короткое подтверждение → ничего не сохраняется;
- `FAST` — можно однозначно извлечь через regex-правила
  (`MemoryExtractor._extract_rule_based` /`_extract_context_rule_based`,
  `memory/extractor_contextual.py`), включая отдельное распознавание "цель
  завершена" (`COMPLETION_PATTERN`: "отчёт я уже закончил" → `MemoryType.STATE`
  с `event_type=goal_completion`) — без единого обращения к Qwen;
- иначе — эскалация к LLM.

Для многосоставных сообщений (несколько предложений) применяет эти же
правила **по предложению**; если хоть одно предложение не разбирается
правилами или содержит местоимение, которое может ссылаться на другое
предложение — весь текст уходит в LLM-путь целиком, чтобы не потерять связь
между предложениями.

**b) `UnifiedMemoryAnalyzer`** (`memory/unified_memory_analyzer.py`) —
**один** вызов Qwen3-4B, который делает сразу извлечение, классификацию по
8 типам памяти и извлечение отношений (subject-predicate-object) в одном
структурированном JSON-ответе. Промпт жёстко ограничивает модель: не
сохранять вопросы/команды/разовые операционные действия, каждое независимое
утверждение — отдельный candidate, командная часть смешанного сообщения
полностью игнорируется. Ответ парсится терпимым JSON-парсером с
восстановлением частично обрезанного вывода (модель иногда обрывается на
`max_new_tokens=320` — парсер умеет вытащить уже завершённые объекты массива
`candidates` даже если JSON в целом не закрыт). После парсинга: убираются
"командные" предложения внутри контента, проверяется, что в содержании
кандидата есть токены-пересечения с исходным текстом (`_has_source_evidence`
— защита от того, что модель "придумает" факт вне текста), переклассификация
эвристиками (procedure→preference при явном "нравится", preference→goal при
явном "хочу/планирую").

**c) Legacy-пайплайн** (`_legacy_process`, только фолбэк, если
`UnifiedMemoryAnalyzer` не сконфигурирован либо упал с исключением) —
отдельный `MemoryExtractor.extract()` + отдельный `MemoryValidator` (тоже
Qwen3-4B, отдельным вызовом валидирует уже извлечённые кандидаты) на
`CandidateRoute.FULL`-кандидатах.

**Типы памяти** (`memory/models.py`, `MemoryType`): `fact`, `preference`,
`episode`, `entity`, `relationship`, `goal`, `state`, `procedure`.

### 3. Обогащение (enrichment)

После получения кандидатов enricher добавляет к ним контекст (только когда
эвристики решают, что это нужно — не на каждое сообщение):

- **Coreference** (`memory/coreference.py`, Qwen3-4B) — разрешение
  местоимений ("он", "её" и т.п.), запускается только если в тексте есть
  местоимение (`PRONOUN_PATTERN`) и путь — legacy.
- **Temporal** (`memory/temporal.py`) — извлечение временных выражений
  ("на следующей неделе", "до конца месяца") — всегда, деревенски дёшево.
- **Entity extraction** (GLiNER, `entity_enricher.py`) — только если среди
  кандидатов есть `ENTITY`/`RELATIONSHIP` или в тексте есть маркер сложных
  отношений ("мой брат", "коллега" и т.п.).
- **Grounding** (`CandidateGrounder`, `memory/candidate_grounder.py`) —
  привязка кандидата к найденным сущностям и позиции в исходном тексте.
- **Subject resolution** (`SubjectResolver`, `memory/subject_resolver.py`) —
  определяет, о ком именно кандидат ("Пользователь" или конкретное третье
  лицо), используя coreference-кластеры и текущий контекст памяти.
- **Relation extraction/linking** (`RelationExtractor` + `RelationLinker`) —
  для legacy-пути с `FULL`-кандидатами дополнительно извлекает и
  привязывает graph-отношения (subject–predicate–object), которые
  Unified-путь уже отдаёт напрямую в JSON.
- **Grammar normalization** (`MemoryGrammarNormalizer`, Qwen3-0.6B) — один
  батч-вызов на все кандидаты сразу, исправляет только падежи/согласование,
  не трогая смысл и факты (жёстко ограничено промптом и few-shot примерами).

Результат — список `EnrichedMemoryCandidate` (кандидат + grounding +
сущности + coreference-кластеры + temporal-выражения + subject + relations),
всё это в дальнейшем складывается в `metadata` записи памяти.

### 4. Сопоставление с существующей памятью (matching)

`MemoryMatcher.find_matches()` (`memory/memory_matcher.py`) сравнивает
каждого нового кандидата со всеми **активными** записями памяти того же
пользователя и считает `score` от 0 до 1 на основе:

- **семантической нормализации** (`SemanticMemoryNormalizer`,
  `memory/semantic_normalizer.py`) — извлекает из кандидата/памяти пару
  (subject, predicate, object), чтобы сравнивать не сырой текст, а структуру
  ("Пользователь — живёт — в Москве" vs "Пользователь — живёт — в
  Санкт-Петербурге" — одно и то же *свойство*, разное *значение*);
- **текстового сходства** (`SequenceMatcher` + Jaccard по словам, 65/35);
- **совпадения subject+predicate при разном object** → это специально
  распознаётся как валидный матч с высоким score (0.88) — модель может
  предложить `SUPERSEDE` (переехал из одного города в другой), а не создать
  дублирующийся, конфликтующий факт;
- отдельного жёсткого правила для **завершения цели** — если кандидат
  помечен `event_type=goal_completion`, ищется активная `GOAL`-память с
  пересекающимися по смыслу словами (или явным `target_goal`) — совпадение
  сразу даёт score 0.95.

Возвращается отсортированный по score список совпадений; порог отсечения по
умолчанию `min_score=0.50`.

### 5. Консолидация (что делать с кандидатом)

`MemoryConsolidator.decide()` (`memory/memory_consolidator.py`) на основе
лучшего матча выбирает действие:

| Действие | Когда |
|---|---|
| `CREATE` | нет совпадений вообще, либо совпадения слабые — новая запись |
| `CONFIRM` | контент буквально совпадает, либо семантическое значение то же (subject+predicate+object совпали) — существующая память "освежается" (`freshness=1.0`, confidence — максимум старого/нового), контент не переписывается |
| `UPDATE` | средняя схожесть (score в диапазоне `[0.70, 0.85)`) — по-видимому, тот же факт другой формулировкой → контент существующей памяти перезаписывается на новую формулировку; также используется для завершения цели (`goal_completion` → память архивируется, `MemoryStatus.ARCHIVED`) |
| `SUPERSEDE` | то же subject+predicate, но другое значение object, схожесть ≥ `update_threshold` (0.70) — старая запись помечается `SUPERSEDED` (`valid_until` = сейчас), создаётся новая `ACTIVE` запись |
| `CONFLICT` | (доступно в модели `ConsolidationAction`, применяется точечно) — создаёт запись со статусом `CONFLICTING` вместо того, чтобы тихо перезаписать |
| `IGNORE` | ничего не делать |

Пороги по умолчанию: `confirm_threshold=0.85`, `update_threshold=0.70`.

### 6. Применение решения (запись в БД)

`MemoryUpdater.apply()` (`memory/memory_updater.py`) превращает решение в
конкретную мутацию `MemoryRepository` (SQLite, `memory/memory_repository.py`,
таблица `memories` в `backend/data/memory.db`):

- `CREATE`/`CONFLICT` — новая строка, `status=ACTIVE`/`CONFLICTING`,
  `importance=0.5`, `freshness=1.0`, метаданные (`subject`, `entities`,
  `relations`, `temporal`, `grounding`, `source_text`) собираются в один
  JSON-блок (`_build_metadata`);
- `CONFIRM` — та же строка, `confidence = max(старая, новая)`,
  `freshness = 1.0`, добавляется новый `Source` (если такого источника ещё
  не было), метаданные мёржатся;
- `UPDATE` — контент перезаписывается на формулировку кандидата, либо (для
  `goal_completion`) память архивируется без изменения текста;
- `SUPERSEDE` — старая запись переводится в `SUPERSEDED` + `valid_until`,
  затем создаётся новая `ACTIVE` запись (как `CREATE`).

Модель `Memory` (`memory/models.py`) хранит: `id`, `user_id`, `type`,
`content`, `confidence`, `importance`, `freshness`, `status`
(`active`/`superseded`/`expired`/`archived`/`conflicting`), список `sources`
(откуда взят факт), `created_at`/`updated_at`/`valid_from`/`valid_until` и
свободный `metadata` (JSON).

### 7. Векторный индекс и поиск (retrieval)

Хранилище (SQLite) — источник истины; **FAISS-индекс — это только
производный поисковый индекс**, который в любой момент можно перестроить из
базы (`memory/faiss_memory_index.py`, `IndexFlatIP` — точный поиск по
косинусной близости, эмбеддинги L2-нормализуются).

- `MemoryIndexManager.ensure_ready()` при старте процесса пытается загрузить
  сохранённый индекс с диска (`backend/data/memory_index/`) и проверяет его
  консистентность с базой по SHA-256 сигнатурам контента (`is_in_sync`);
  при рассинхроне или отсутствии файла — полная пересборка.
- После каждой транзакции записи (`sync_after_pipeline`) — если было хоть
  одно `UPDATE`/`SUPERSEDE` (могло исчезнуть активное содержимое), индекс
  **полностью перестраивается**; если были только `CREATE`, новые векторы
  **добавляются инкрементально** без пересборки; `CONFIRM`/`IGNORE`/`CONFLICT`
  индекс не трогают вообще.

Поиск при ответе агента идёт в два этапа
(`MemorySystem.retrieve` → `MemoryRetrieverV2`, `memory/memory_retriever_v2.py`):

1. **Первый этап** (`MemoryRetriever`, `memory/memory_retriever.py`) — запрос
   кодируется `Qwen3Embedder.encode_query()` (с инструкцией-промптом под
   ретривал), ищется по FAISS топ-`candidate_k` (по умолчанию `max(top_k*3, 12)`
   из вызова в `search_memory`-tool), результат фильтруется по `user_id` и
   `status == ACTIVE`.
2. **Второй этап — реранкинг и пост-фильтры** (`MemoryRetrieverV2`):
   - все кандидаты первого этапа переранжируются кросс-энкодером
     `Qwen3Reranker` (пары запрос-документ, один батч-вызов);
   - если запрос содержит **временной маркер** ("на следующей неделе", "до
     конца месяца", "в следующем году") — это становится **жёстким
     фильтром**: возвращаются только записи, у которых есть совпадающее
     `temporal`-метаданное или похожий текст, никакого смешивания с
     нерелевантным по времени;
   - если запрос **про тип "цель/план"** (глаголы "хочу", "планирую",
     "собираюсь") — аналогично, приоритет отдаётся `MemoryType.GOAL`;
   - если запрос **сущностно-ориентированный** ("расскажи о...", "что
     известно о...") — берётся лучший семантический "seed", а дальше граф
     раскрывается через `_connected_memories`: ищутся другие активные записи
     с тем же `subject`, пересекающимися `entities` или упоминанием seed'а в
     собственных `relations` — то есть у памяти есть неявный **граф связей**
     поверх плоского хранилища, используемый только для entity-запросов,
     чтобы не заливать модель нерелевантными фактами;
   - иначе — **пороговый семантический фолбэк**: идём по переранжированному
     списку, переводим logit реранкера в вероятность через сигмоиду,
     отбрасываем всё ниже `min_semantic_probability=0.01`, а также
     останавливаемся, если между соседними по рангу результатами происходит
     резкий обрыв скора (`semantic_score_drop_threshold=1.0`) — так `top_k`
     остаётся **максимумом, а не обязательством**: на вопрос с одним
     релевантным фактом система вернёт один факт, а не `top_k` случайных.

### 8. Как память попадает в ответ агента

Никакого автоматического впрыскивания контекста памяти в промпт при каждом
сообщении **нет**. Вместо этого агенту дан tool `search_memory(query)`
(`tools.build_memory_tool`, `MEMORY_TOOL_NAME = "search_memory"`), и решение
"нужна ли память для ответа, и с каким запросом" модель принимает сама
(инструкция в `MEMORY_INSTRUCTIONS`, см. промпт выше) — вплоть до нескольких
вызовов с разными формулировками за один ход, если первый результат не
устроил. Найденные `Memory` форматируются в компактный список
(`MemoryContextFormatter`, `memory/memory_context_formatter.py`:
`"Relevant memories:\n- ..."`) и возвращаются как `ToolMessage` — дальше
модель видит их как обычный результат вызова функции.

---

## Хранилища данных

Все данные приложения лежат в `backend/data/` (создаётся автоматически,
исключено из git):

| Файл/папка | Что содержит | Кто пишет |
|---|---|---|
| `app.db` | SQLite: `chat_messages` (история диалога), `tasks`, `expenses`, `meals`, `weights` | `backend/app/db.py`, `tools.py` |
| `memory.db` | SQLite: таблица `memories` — вся долгосрочная память по всем пользователям | `memory/memory_repository.py` |
| `memory_index/` | `memories.faiss` (FAISS-индекс) + `memory_ids.json` (маппинг id→вектор + SHA-256 сигнатуры содержимого для проверки консистентности) | `memory/faiss_memory_index.py` |

`user_id` — случайный UUID, который фронтенд генерирует один раз и хранит в
`localStorage` (`frontend/src/lib/api.js: getUserId`) — отдельного логина/
регистрации нет, это однопользовательский локальный ассистент на клиент.

---

## API backend

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/health` | проверка живости |
| `POST` | `/api/chat` | `{user_id, message}` → SSE-поток (`token`/`tool_call`/`done`); один ход диалога + фоновая запись в память |
| `GET` | `/api/history/{user_id}` | вся история чата пользователя |
| `GET` | `/api/memories/{user_id}` | все записи памяти пользователя (включая неактивные — superseded/archived/conflicting) |
| `GET` | `/api/sandbox/{user_id}` | последние 20 задач/трат/приёмов пищи/замеров веса |

---

## Frontend

React 19 + Vite, без стейт-менеджера — состояние чата в `App.jsx` (плоский
`useState`). Отдельно:

- `components/Chat.jsx` — рендер сообщений, включая markdown-разбор
  (`lib/markdown.js`) и индикацию tool-call действий;
- `components/Composer.jsx` — поле ввода с авто-расширением и стриминговым
  индикатором;
- `components/OrbCanvas.jsx` — анимированный "орб"-индикатор состояния на
  Three.js (реагирует на активность через `setEnergy`);
- `components/MemoryPanel.jsx` — боковая панель "Память": тянет
  `/api/memories` и `/api/sandbox` и показывает, что ассистент запомнил, и
  последние записанные действия.

`lib/api.js` — тонкий клиент: ручной парсинг SSE (`text/event-stream`) по
`\n\n`-разделителям, без сторонних SSE-библиотек. Все запросы идут на
относительный `/api/...` — Vite dev-сервер проксирует их на backend (см.
`vite.config.js`), поэтому работает и на localhost, и через туннель.

---

## Структура репозитория

```
backend/
  app/
    main.py            — FastAPI роуты, CORS, startup (init_db + MCP)
    agent.py            — цикл одного хода диалога (стриминг, ретраи, сохранение)
    agent_graph.py       — сборка LangGraph-графа (agent ⇄ tools)
    chat_model.py         — LangChain-адаптер над локальным Qwen3-4B
    tools.py               — экшн-тулы (задачи/траты/питание/вес) + memory tool
    mcp_tools.py             — опциональная загрузка внешних MCP-серверов
    prompts.py                — сборка системного промпта
    memory_bridge.py            — мост backend ↔ пакет memory/ (sys.path, singletons)
    db.py                         — SQLite схема приложения (app.db)
    schemas.py                     — Pydantic-модели запросов/ответов
  mcp_servers.example.json  — шаблон конфига MCP (копировать в mcp_servers.json)
  requirements.txt
  run_backend.cmd

frontend/
  src/
    App.jsx, components/*, lib/api.js, lib/markdown.js, styles.css

memory/                — самостоятельный пакет системы долгосрочной памяти
  memory_system.py          — публичный фасад (update/retrieve/get_all)
  model_registry.py           — singleton-и тяжёлых моделей на процесс
  models.py                     — Memory/MemoryCandidate/MemoryType/MemoryStatus
  cheap_memory_router.py         — regex-фильтр "не память" до любых моделей
  memory_input_router.py           — regex-роутер DROP/FAST/UNIFIED
  candidate_router.py                — маршрутизация кандидата FAST/FULL
  unified_memory_analyzer.py           — один LLM-вызов: извлечение+типизация+relations
  extractor_contextual.py                — legacy-экстрактор (fallback)
  enricher.py                              — оркестратор обогащения кандидата
  coreference.py, temporal.py,               — модули обогащения
    candidate_grounder.py, subject_resolver.py,
    relation_extractor.py, relation_linker.py,
    entity_enricher.py (GLiNER), memory_grammar_normalizer.py
  memory_matcher.py                          — сопоставление со старой памятью
  semantic_normalizer.py                       — (subject, predicate, object) из текста
  memory_consolidator.py                         — CREATE/CONFIRM/UPDATE/SUPERSEDE/...
  memory_updater.py                                — применение решения к SQLite
  memory_repository.py, persistent_memory_store.py,  — хранилище (SQLite)
    memory_store.py
  memory_embedder.py (Qwen3-Embedding), memory_reranker.py (Qwen3-Reranker),
    retrieval_model_manager.py
  faiss_memory_index.py, memory_index_manager.py       — векторный индекс
  memory_retriever.py, memory_retriever_v2.py            — поиск (FAISS→rerank→фильтры)
  memory_context.py, memory_context_formatter.py           — форматирование для LLM
  qwen_model.py                                              — обёртка над Qwen3-4B

dev.ps1 / dev.sh / stop.ps1 / stop.sh   — скрипты запуска/остановки
```

---

## Известные ограничения

- Однопользовательская локальная система: авторизации нет, `user_id` —
  анонимный UUID в `localStorage` браузера.
- Диалоговая модель — маленькая локальная Qwen3-4B в 4-бит квантовании;
  качество function calling ощутимо ниже, чем у топовых облачных моделей —
  отсюда специальный парсер tool-call блока с фолбэками и ретраем на пустой
  ответ (см. `agent.py`, `chat_model.py`).
- Вся тяжёлая часть (5 моделей) держится в GPU-памяти одновременно на время
  жизни backend-процесса — нужен GPU с достаточным запасом VRAM (ориентир:
  Qwen3-4B в 4-бит + два Qwen3-0.6B + GLiNER + FAISS CPU-индекс).
- Явного управления `importance`/`freshness`/протухания (`expired`) во
  времени пока нет — поля в модели `Memory` существуют, но автоматического
  фонового процесса, который бы их пересчитывал, в коде нет.
