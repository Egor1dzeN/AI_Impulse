# Impulse

Персональный AI-ассистент с долгосрочной памятью и проактивными действиями
(хакатон «AI-импульс 2.0», трек 2, кейс restore: — условия задачи в
[`competition_task/README.md`](competition_task/README.md)).

Ассистент сам накапливает знание о пользователе: из переписки (почта,
Telegram), календаря и банковских уведомлений он собирает **цифровой
профиль** с источником и периодом действия каждого факта, отвечает на точные
вопросы по этим данным **детерминированно** (одинаково при любой формулировке),
**сам замечает поводы** для действия и предлагает их, **выполняет действия**
во внешних системах (Google Calendar, Gmail, Intervals.icu) только через
подтверждение в UI, **маскирует** критичные данные на каждой границе и
**сопротивляется промпт-инъекциям**, спрятанным в данных.

Стек: **FastAPI** (backend) + **React 19 / Vite** (frontend) + **LangGraph**
(агентный цикл) + **GigaChat** (диалог и LLM-часть памяти) + **cloud.ru**
(эмбеддер, реранкер, Whisper) + самописная модульная **система долгосрочной
памяти** (`memory/`) поверх SQLite и FAISS.

Локальный режим никуда не делся: если ключи API не заданы, backend
откатывается на локальные модели (Qwen3-4B, Qwen3-Embedding/Reranker-0.6B,
Qwen3-0.6B) — удобно для офлайн-разработки, но нужен GPU.

---

## Содержание

- [Быстрый запуск](#быстрый-запуск)
- [Переменные окружения](#переменные-окружения)
- [Общая архитектура](#общая-архитектура)
- [Модели и где они крутятся](#модели-и-где-они-крутятся)
- [Backend: как обрабатывается сообщение](#backend-как-обрабатывается-сообщение)
- [Импорт данных и цифровой профиль](#импорт-данных-и-цифровой-профиль)
- [Детерминированная аналитика по данным](#детерминированная-аналитика-по-данным)
- [Проактивность](#проактивность)
- [Безопасность: маскирование, инъекции, подтверждения](#безопасность-маскирование-инъекции-подтверждения)
- [Инструменты агента (function calling)](#инструменты-агента-function-calling)
- [Интеграции](#интеграции)
- [MCP-серверы](#mcp-серверы)
- [Система памяти — подробно](#система-памяти--подробно)
- [Хранилища данных](#хранилища-данных)
- [API backend](#api-backend)
- [Frontend](#frontend)
- [Самопроверка на эталонных вопросах](#самопроверка-на-эталонных-вопросах)
- [Структура репозитория](#структура-репозитория)
- [Известные ограничения](#известные-ограничения)

---

## Быстрый запуск

Единственный способ запуска «одной командой» — `dev.sh` (Git Bash / MSYS;
работает и на Windows, и на Linux/macOS). Это же **debug-запуск с горячей
перезагрузкой**: backend и frontend подхватывают изменения на лету.

### 0. Ключи

```bash
cp backend/.env.example backend/.env
# и заполнить хотя бы GIGA_CHAT_IDE и GIGA_CHAT_EMBEDDING — см. ниже
```

`backend.sh`/`dev.sh` сами подгружают `backend/.env` перед стартом uvicorn.
Файл в `.gitignore`, в репозиторий не попадает.

### Вариант 1 — одной командой (рекомендуется)

```bash
./dev.sh
```

Убивает всё, что уже слушает порты 8001/5173, при первом запуске сам создаёт
`backend/.venv`, ставит зависимости из `requirements.txt` и делает
`npm install` во `frontend/`, затем поднимает оба процесса:

- **backend** — `uvicorn ... --reload --reload-dir app`: изменения в
  `backend/app/**/*.py` подхватываются автоматически;
- **frontend** — `npm run dev` (Vite) с HMR.

После запуска:
- Backend: http://127.0.0.1:8001 (логи → `backend_run.log`, pid → `backend.pid`)
- Frontend: http://localhost:5173 (логи → `frontend_run.log`, pid → `frontend.pid`)

Открой в браузере **http://localhost:5173**, зарегистрируйся (логин/пароль
или Google) и загрузи данные через панель **«Импорт данных»** (например,
файлы из `competition_task/data/`).

Остановить оба процесса:

```bash
./stop.sh
```

> **Важно про `memory/`:** правки в пакете `memory/` (он лежит в корне
> проекта, вне `backend/app/`) **не** подхватываются автоматически —
> намеренно: там лениво живут модели/клиенты памяти, и полный перезапуск
> uvicorn на каждую правку перезагружал бы их. После правок в `memory/`
> перезапусти `./dev.sh` вручную.

### Вариант 2 — по отдельности, в двух терминалах

```bash
./backend.sh   # первый терминал — FastAPI/uvicorn, порт 8001
```

```bash
./frontend.sh  # второй терминал — Vite dev server, порт 5173
```

Оба работают в foreground (лог дублируется в `*_run.log`), останавливаются
Ctrl+C или `./stop.sh`. Проверка backend'а: http://127.0.0.1:8001/api/health

<details>
<summary>Совсем вручную, без скриптов</summary>

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload --reload-dir app
```

```bash
cd frontend
npm install
npm run dev
```

</details>

### Важно

- Backend **должен** быть на `127.0.0.1:8001` — frontend проксирует `/api`
  туда через Vite dev-proxy (`frontend/vite.config.js`), а CORS в
  `backend/app/main.py` разрешён только для `localhost:5173`/`127.0.0.1:5173`.
- `requirements.txt` по-прежнему тянет тяжёлые ML-зависимости (torch CUDA
  12.8, transformers, bitsandbytes, faiss, sentence-transformers, gliner) —
  они нужны для локального фолбэка и для GLiNER, который пока всегда
  локальный. Первая установка долгая.
- Первое сообщение может быть медленнее остальных: модели/клиенты памяти
  лениво создаются при первом обращении (`memory/model_registry.py`,
  singleton на процесс).
- В репозитории нет CI/CD или деплоя — это локальный dev-проект.

---

## Переменные окружения

Полный актуальный список с комментариями — `backend/.env.example`. Главное:

| Переменная | Что включает |
|---|---|
| `GIGA_CHAT_IDE` | ключ GigaChat. Включает GigaChat для **чата** (`GIGACHAT_MODEL`, по умолчанию `GigaChat-3-Ultra`) и для **LLM-части памяти** — извлечение, coreference, relation extraction, dreaming, валидация, грамматическая нормализация (`GIGACHAT_LITE_MODEL`, по умолчанию `GigaChat-2`). Без него — локальный Qwen3-4B |
| `GIGA_CHAT_EMBEDDING` | на самом деле ключ **cloud.ru** foundation-models (имя историческое). Включает облачные `Qwen3-Embedding-0.6B`, `Qwen3-Reranker-0.6B` (те же чекпоинты, что локально — FAISS не нужно переиндексировать) и Whisper для голосового ввода |
| `GOOGLE_CLIENT_SECRET` | OAuth для Google Calendar и Gmail (тот же OAuth-клиент, что и Google Sign-In; нужны включённые Calendar API/Gmail API и зарегистрированные redirect URI) |
| `AUTH_SECRET_KEY` | секрет для подписи JWT |
| `MAPBOX_TOKEN` | интерактивные карты и маршруты |
| `TELEGRAM_BOT_TOKEN` | токен Telegram-бота от @BotFather (`app/telegram_bot_client.py`) |
| `ASSISTANT_NOW` | (опционально) ISO-дата/время, «замораживает» текущий момент ассистента — для воспроизводимой проверки на датасете с фиксированной датой |
| `GIGACHAT_USE_EMBEDDINGS` | опционально перевести эмбеддер на GigaChat (на тарифе `GIGACHAT_API_PERS` эмбеддинги отдают 402, поэтому по умолчанию выключено) |
| `QWEN_4BIT`, `GLINER_DEVICE`, `GRAMMAR_DEVICE` | тонкая настройка локальных моделей |

Ключ Intervals.icu задаётся не в `.env`, а каждым пользователем в панели
«Настройки» (проверяется сервером перед сохранением).

---

## Общая архитектура

```
┌──────────────────────┐   SSE (/api/chat)   ┌──────────────────────────────────────┐
│  Frontend (Vite)      │ ─────────────────▶ │         FastAPI backend (backend/app) │
│  React + Three.js +   │ ◀───────────────── │                                       │
│  Mapbox               │  token/tool_call/  │  masking на входе/выходе/в логах      │
│  панели: Память,      │  done              └───────┬───────────────────┬──────────┘
│  Финансы, Настройки;  │                            │                   │
│  датасет — в чате     │   POST /api/import         │                   │
└──────────┬───────────┘ ─────────────────────▶ personal_data         LangGraph agent
           │                                    (нормализация, маска,  (agent_graph.py)
           │ кнопки «Отправить»/                injection_guard,          │
           │ «Подтвердить» на карточках         bank_parser)              │
           │ черновиков                              │        ┌──────────┼───────────────┬──────────────┐
           ▼                                         │        │          │               │              │
   /api/gmail/send, /api/calendar/confirm           │   ChatGigaChat  data_tools     action tools   memory tools
   (единственный путь к реальной записи             │   (или ChatQwen)  → personal_   (задачи, траты,  search_memory
    наружу — агенту недоступен)                     │                   analytics    питание, карты,  list_memories
                                                     │                   (без LLM)    погода, календарь, │
                                                     │                                почта, Intervals) │
                                                     ▼                                                  ▼
                                    фоновое извлечение фактов  ─────────────▶  memory/ — MemorySystem
                                    + profile_builder                          (SQLite + FAISS,
                                                                                см. «Система памяти»)
```

Ключевые идеи:

- **Два хранилища знаний с разными задачами.** Сырые импортированные данные
  (`raw_emails`, `raw_calendar_events`, `raw_messages`, транзакции) лежат
  в `app.db` дословно — на них отвечают детерминированные функции (суммы,
  свободные окна, рейтинги). Нечёткая **фактовая память** (`memory/`) хранит
  профиль: предпочтения, отношения, цели — с источником и периодом действия.
- **Чат и запоминание — независимые пути.** Ответ стримится сразу, а
  извлечение фактов из сообщения/импорта идёт в `BackgroundTasks`.
- **Модель формулирует, код считает.** Все числа, даты и списки агент берёт
  из функций, а не вычисляет сам — так ответ воспроизводим.
- **Запись наружу — только через кнопку в UI.** Агент готовит черновики,
  реальные вызовы Gmail/Calendar происходят только по клику пользователя.

---

## Модели и где они крутятся

Все модели/клиенты создаются один раз на процесс через
`memory/model_registry.py` (`ModelRegistry`, потокобезопасный singleton).
Для каждой роли есть облачный бэкенд (используется, если задан ключ) и
локальный фолбэк.

| Роль | По умолчанию (с ключами) | Локальный фолбэк | Файлы |
|---|---|---|---|
| Диалоговая LLM агента | **GigaChat** `GigaChat-3-Ultra` (`GIGA_CHAT_IDE`) | Qwen3-4B, 4-bit NF4 | `backend/app/chat_gigachat.py`, `chat_model.py`, `agent_graph.py::_build_llm` |
| LLM памяти: извлечение, coreference, relations, dreaming, валидация | **GigaChat** `GigaChat-2` | тот же Qwen3-4B | `memory/gigachat_text_model.py`, `memory/qwen_model.py` |
| Грамматическая нормализация фактов | **GigaChat** `GigaChat-2` | Qwen3-0.6B на CPU | `memory/gigachat_grammar_normalizer.py`, `memory_grammar_normalizer.py` |
| Эмбеддер для поиска по памяти | **cloud.ru** `Qwen/Qwen3-Embedding-0.6B` (`GIGA_CHAT_EMBEDDING`) | тот же чекпоинт локально, 8-bit | `memory/cloud_ru_embedder.py`, `memory_embedder.py` |
| Реранкер | **cloud.ru** `Qwen/Qwen3-Reranker-0.6B` (эндпоинт `/score`) | тот же чекпоинт локально, FP16 | `memory/cloud_ru_reranker.py`, `memory_reranker.py` |
| Распознавание речи | **cloud.ru** `openai/whisper-large-v3` | — | `backend/app/transcription.py` |
| NER (сущности для обогащения памяти) | — | **GLiNER** `urchade/gliner_multi-v2.1`, CPU | `memory/entity_enricher.py` |

Особенности GigaChat, которые обработаны в коде:

- содержимое сообщения с ролью `function` должно быть валидным JSON —
  `chat_gigachat.py::_ensure_json_content()` оборачивает обычный текст
  результатов инструментов;
- `GigaChat-2` иногда вместо JSON-массива отдаёт несколько объектов подряд —
  `gigachat_text_model.py::_repair_json_array()` это чинит.

Локальный `ChatQwen` — адаптер `BaseChatModel` над Qwen3-4B: список функций
дописывается в системный промпт, модель отвечает блоком
```` ```tool_call {...}``` ````, который парсится в `AIMessage.tool_calls`.
Для графа оба бэкенда выглядят одинаково (`bind_tools()`/`invoke()`).

---

## Backend: как обрабатывается сообщение

`POST /api/chat` (`backend/app/main.py`) отдаёт `text/event-stream` (SSE) с
событиями `token`, `tool_call`, `done`. Один ход —
`agent.handle_chat_turn_stream` (`backend/app/agent.py`):

1. Сообщение пользователя сразу проходит **маскирование**
   (`masking.mask_sensitive`) — номер карты или пароль не попадут ни в
   модель, ни в историю, ни в логи.
2. Строится системный промпт (`prompts.build_system_prompt`) с текущим
   моментом в часовом поясе пользователя (`personal_data.now_local`,
   переопределяется `ASSISTANT_NOW`) и набором блоков в зависимости от
   подключённых интеграций.
3. Подгружаются последние `HISTORY_MESSAGE_LIMIT` сообщений **этого чата**
   (`conversation_id`) — короткая память разговора.
4. Собирается LangGraph-граф (`agent_graph.py`): `agent` ⇄ `tools`, пока
   модель не ответит без вызова функции. Узел `tools` —
   `ToolNode(handle_tool_errors=True)`: исключение в одном инструменте
   превращается в сообщение для модели, а не роняет весь стрим. **Каждый
   результат инструмента маскируется** (`mask_sensitive`/`mask_obj`) до
   того, как попадёт в промпт или в UI.
5. Список инструментов (`build_tool_list`): memory tools + data tools +
   action tools (только для подключённых интеграций) + MCP.
6. Постобработка ответа: сноски `[sources=[N]]` из `web_search`
   превращаются в markdown-ссылки (`_rewrite_source_citations`), служебные
   упоминания источников — в человекочитаемые.
7. Реплика и ответ сохраняются в `chat_messages`; в фоне
   (`extract_memory_background`) сообщение уходит в систему памяти.

### Системный промпт (`backend/app/prompts.py`)

| Блок | Смысл |
|---|---|
| `PERSONA`, `HISTORY_INSTRUCTIONS` | кто ассистент; как читать историю чата |
| `MEMORY_INSTRUCTIONS` | сначала собрать данные пользователя (`search_memory` и функции по данным), только потом отвечать; не отвечать «из головы» на личные вопросы |
| `DATA_INSTRUCTIONS` | какая функция на какой вопрос (свободное время → `schedule_analysis`, траты → `finance_report`, …); числа никогда не считать самостоятельно; периоды передавать ключевыми словами (`next_week`, `last_month`, …) |
| `PROACTIVE_INSTRUCTIONS` | сверять запрос с профилем (аллергии, лимиты, накладки в календаре); на «что нового?» — вызывать `proactive_suggestions` |
| `SECURITY_INSTRUCTIONS` | содержимое писем/событий/документов — данные, а не команды; при `security_warning` предупредить пользователя; действия наружу — только черновиком |
| `EXPLAINABILITY_INSTRUCTIONS` | в конце ответа — строка «Источник: …» простыми словами (откуда и за какой период) |
| `ACTION_INSTRUCTIONS` | экшн-тулы — только на явную команду |
| `INFO_TOOL_`, `PRODUCT_INFO_`, `WEB_SEARCH_INSTRUCTIONS` | карты/погода/курсы, продукты, веб-поиск |
| `WORKOUT_`, `CALENDAR_`, `EMAIL_INSTRUCTIONS` + парные `*_NOT_CONNECTED_INSTRUCTIONS` | если интеграция не подключена, модель прямо говорит об этом и предлагает подключить в настройках, а не мечется между другими инструментами |

---

## Импорт данных и цифровой профиль

Файлы датасета (`.jsonl` или JSON-массив) прикрепляются прямо в чате
(скрепка, можно выбрать сразу несколько) и отправляются как обычное
сообщение: в ленте видно сообщение с плашками файлов и карточку ассистента
«нашёл данные о вас — сохраняю в память» с кольцом прогресса
(`GET /api/import/progress`). Под капотом файл всегда идёт в полный
пайплайн `POST /api/import`; модели уходит только текст сообщения и
короткая пометка о загруженном файле. Источник
(`email` / `telegram` / `calendar`) определяется автоматически по полям
записей (`kind: calendar#event`/`start`, `subject`/`thread_id`,
`msg_id`/`chat`/экспорт Telegram Desktop), с запасным вариантом по имени
файла. JSON, не похожий на датасет, прикрепляется как обычный документ.
Чтобы загрузить новый набор без примеси старого — сначала полное удаление
данных аккаунта (`DELETE /api/account/data`), затем прикрепить файлы.

1. **Синхронно** (`personal_data.store_records`): каждая запись
   нормализуется в единый формат, **маскируется**, проверяется
   **injection_guard** и сохраняется дословно в `raw_*`-таблицы `app.db`.
   Письма-уведомления банка разбираются `bank_parser.py` в транзакции
   (формат-агностично: ключевое слово операции рядом с суммой, без привязки
   к шаблону конкретного банка). Сразу после ответа на точные вопросы можно
   отвечать.
2. **В фоне**: записи, несущие знание о профиле и прошедшие проверку
   безопасности, идут через пайплайн памяти (`memory_bridge.import_records` →
   `ingest_email` / `ingest_telegram_message` (чанками по беседам) /
   `ingest_calendar_event`) с настоящим `Source` — тип, id, дата, для событий
   начало/конец. Помеченные подозрительными записи в память **не попадают**.
3. **Цифровой профиль** (`profile_builder.py`) строится после импорта и
   хранится в той же памяти как набор фактов с источниками:
   - *вычисляемые* — имя и обращения к пользователю, домашний город (по
     местам транзакций), финансы, распорядок, круг общения, подписки — чистая
     арифметика по `personal_analytics`, одинаковая при каждой пересборке;
   - *синтезируемые* — биография, отношения, спорт, предпочтения, цели —
     один LLM-проход на область по детерминированно отобранным и помеченным
     фрагментам, поэтому каждый факт привязан к конкретным источникам.

   Пересборка заменяет старые профильные факты
   (`memory_bridge.replace_profile_facts`), дубликатов не возникает.

Необязательные параметры API: `source` — задать источник явно; `replace` — стереть данные аккаунта и отменить ещё идущее
фоновое извлечение от прошлого импорта; `sync_calendar` — дополнительно
создать события в подключённом Google Calendar.

Экспорт памяти — `GET /api/memories/export` (замаскированный), полное
удаление всех данных аккаунта — `DELETE /api/account/data`.

---

## Детерминированная аналитика по данным

`backend/app/personal_analytics.py` — вся точная логика по импортированным
данным, **без LLM**: одинаковые данные → одинаковые цифры при любой
формулировке вопроса и в любой сессии. Агент вызывает её через
`backend/app/data_tools.py` и только формулирует ответ.

| Инструмент | Что делает |
|---|---|
| `data_overview` | что загружено: объёмы по источникам, диапазон дат |
| `calendar_events` | события за период (с раскрытием повторяющихся), участники |
| `schedule_analysis` | свободные окна от N минут, первое подходящее окно, самый загруженный день, кандидаты на перенос и то, что трогать нельзя |
| `recurring_schedule` | регулярные события и привычки (в т.ч. из паттернов писем) |
| `search_emails`, `get_email_thread` | поиск писем по теме/человеку/периоду, полный текст |
| `inbox_overview` | входящие: неотвеченные, срочные, дедлайны и их статус |
| `search_messages` | поиск по мессенджеру (чат, отправитель, период) |
| `top_contacts` | с кем чаще общается пользователь (`scope` work/personal) |
| `finance_report` | траты/доходы за период, категории, топ мест, сравнение месяцев |
| `monthly_cashflow` | средний денежный поток по полным месяцам, отдельно — разовые доходы; «сколько могу откладывать» |
| `proactive_suggestions` | см. ниже |

Периоды передаются ключевыми словами (`today`, `tomorrow`, `next_week`,
`last_month`, `month_before_last`, `last_3_days`) или датами —
`resolve_period` переводит их относительно текущего момента пользователя.

---

## Проактивность

`backend/app/proactive.py` — детекторы поводов действовать, которые
ассистент находит в данных сам: письма, требующие ответа, и приближающиеся
дедлайны; накладки/переносы важных встреч в календаре; финансовые аномалии.
Каждый пункт — приоритет, объяснение, источник и конкретный следующий шаг
через интеграцию (черновик ответа, событие в календаре, лимит трат) плюс
готовый промпт для чата.

Детекторы детерминированные. Их используют и стартовый экран
(`GET /api/proactive`, карточки при открытии чата), и агент
(`proactive_suggestions` — на «привет», «что нового?», «на что обратить
внимание?»). Кроме того, `PROACTIVE_INSTRUCTIONS` заставляет агента сверять
любой запрос с профилем: предупреждать о накладках в календаре, аллергенах,
превышении трат.

---

## Безопасность: маскирование, инъекции, подтверждения

**Маскирование** (`backend/app/masking.py`) — номера карт (с проверкой
Луна, остаются последние 4 цифры), CVV, номера счетов, паспорта/документы,
пароли, токены, API-ключи. Применяется архитектурно, на каждой границе:
при импорте (до хранения, эмбеддинга и отправки в LLM), на каждом
результате инструмента (обёртка над `ToolNode`), на сообщении пользователя,
на сохранённом ответе, в ответах API и экспорте и в логах
(`MaskingLogFilter`, `install_log_masking`). Обычные персональные данные
(имя, телефон, адрес) намеренно не маскируются — они нужны для работы.

**Защита от промпт-инъекций и фишинга** (`backend/app/injection_guard.py`):
эвристики по нормализованному тексту (попытки переопределить инструкции,
просьбы переслать/перевести/скрыть, фальшивые системные уведомления,
запросы реквизитов) плюс проверка отправителя — похожие на известные
домены (расстояние Левенштейна, подмена бренда), неустоявшиеся отправители.
Помеченные записи хранятся (пользователь может их увидеть), но **не
попадают в долгосрочную память** и в каждом результате инструмента несут
`security_warning`, который модель обязана передать пользователю, а не
выполнять.

**Документы во вложениях** (`backend/app/documents.py`,
`POST /api/documents/extract`): PDF/DOCX/текст разбираются на сервере;
**скрытый текст** (белый/невидимый, крошечный шрифт, hidden-форматирование
Word) отделяется и в модель не попадает — только предупреждение о нём.
Видимый текст проверяется на инъекции, маскируется, из него детерминированно
извлекаются даты и сроки (счёт — напоминание за 3 дня, договор — за срок
уведомления), и всё это передаётся модели блоком, явно помеченным как данные.

**Подтверждение действий наружу.** У агента **нет** инструментов, которые
сами отправляют письмо или меняют календарь:
- `draft_email` только готовит черновик → карточка в чате с кнопкой
  «Отправить» → `POST /api/gmail/send`;
- `create_calendar_event` / `update_calendar_event` /
  `delete_calendar_event` готовят черновик → кнопка «Подтвердить» →
  `POST /api/calendar/confirm`.

Эти эндпоинты недостижимы из цикла вызова инструментов. Таблицы
`sent_email_drafts` / `calendar_action_drafts` делают повторный клик
(например, по устаревшей карточке после перезагрузки) безопасным no-op.

**Объяснимость**: ответ, основанный на данных пользователя, заканчивается
строкой «Источник: …» (откуда и за какой период), а у каждого факта памяти
хранятся `sources` и `valid_from`/`valid_until` — это видно в панели «Память».

---

## Инструменты агента (function calling)

Все инструменты создаются заново на каждый запрос и замкнуты на `user_id`
этого запроса — модель не видит и не может подставить `user_id` сама.
Инструменты интеграций добавляются только если интеграция подключена.

**Память** (`tools.build_memory_tools`):

| Инструмент | Действие |
|---|---|
| `search_memory(query)` | top-20 релевантных фактов (embed → FAISS → rerank) |
| `list_memories(source?)` | весь профиль без ранжирования, опционально фильтр по источнику (`email`/`telegram`/`calendar`/`text`) |

**Данные пользователя** — см. [Детерминированная аналитика](#детерминированная-аналитика-по-данным).

**Действия и справка** (`tools.build_action_tools`):

| Инструмент | Действие |
|---|---|
| `create_task`, `list_tasks` | задачи/напоминания |
| `add_expense`, `add_income`, `list_transactions`, `update_transaction`, `get_finance_summary` | ручной учёт трат и доходов |
| `log_meal`, `log_weight`, `get_health_summary` | питание (цель 2200 ккал) и вес |
| `get_product_info(barcode)`, `search_product(query)` | Open Food Facts |
| `log_workout`, `get_workouts`, `get_workout_route` | Intervals.icu (если подключён ключ); маршрут тренировки рисуется на карте |
| `create_calendar_event`, `get_calendar_events`, `update_calendar_event`, `delete_calendar_event` | Google Calendar (если подключён); изменения — только черновиком на подтверждение |
| `list_emails`, `get_email`, `draft_email` | Gmail (если подключён); отправка — только кнопкой в UI |
| `web_search(query)` | поиск в интернете через `ddgs`, со ссылками на источники |
| `show_map(place)`, `build_route(origin, destination, profile)` | геокодинг (Nominatim) и интерактивная карта / маршрут (Mapbox) |
| `get_weather(place)` | Open-Meteo |
| `get_exchange_rate(currency)`, `get_exchange_rate_history(currency, days_ago)` | курсы ЦБ РФ |

`GET /api/sandbox` отдаёт «песочницу» — последние задачи/траты/приёмы
пищи/вес для панели «Память».

---

## Интеграции

Все интеграции устроены одинаково: учётные данные хранятся в строке
`users`, инструменты добавляются только при наличии учётных данных, в
промпте есть пара `X_INSTRUCTIONS` / `X_NOT_CONNECTED_INSTRUCTIONS`.

- **Google Calendar** (`google_calendar.py`) и **Gmail** (`gmail.py`) —
  OAuth через общий `google_oauth.py` (authorize URL, обмен кода, refresh,
  revoke; `state` — подписанный JWT с `user_id` и назначением, т.к.
  callback Google приходит без заголовка авторизации). Подключаются в
  панели «Настройки» (`/api/google-calendar/connect`, `/api/gmail/connect`).
- **Intervals.icu** — API-ключ, вставляется в «Настройках», проверяется
  сервером перед сохранением.
- **Голосовой ввод** (`transcription.py`, `POST /api/transcribe`) —
  зажать кнопку микрофона в композере; браузерный webm/opus
  перекодируется в mp3 через `imageio-ffmpeg` (встроенный ffmpeg, ничего
  ставить не нужно) и уходит в Whisper на cloud.ru.
- **Вложения документов** — см. [Безопасность](#безопасность-маскирование-инъекции-подтверждения).
- **Карты** — Mapbox GL (`frontend/src/components/MapboxMap.jsx`), тема
  карты следует теме приложения.

---

## MCP-серверы

`backend/app/mcp_tools.py` — опциональное подключение внешних MCP-серверов
через `langchain-mcp-adapters`. Пока `backend/mcp_servers.json` отсутствует
или пуст — это no-op.

Чтобы подключить: скопировать `backend/mcp_servers.example.json` в
`backend/mcp_servers.json` (в `.gitignore`), поставить серверу
`"enabled": true` (или убрать ключ) и описать его в формате
`MultiServerMCPClient` (`streamable_http` с `url` либо `stdio` с
`command`/`args`). Токены — через `${VAR}` из `backend/.env`. Инструменты
подтягиваются при старте бэкенда (после правки конфига — перезапуск) и
получают имена `mcp_<сервер>_<инструмент>`.

Безопасность:
- каждый сервер подключается отдельно с таймаутом: недоступный сервер виден
  в статусе, но не мешает запуску;
- ответ внешнего инструмента — данные, а не инструкции: он проходит
  маскирование секретов, проверку на инъекции (`injection_guard`) и
  помечается `UNTRUSTED_NOTE`, длина ограничена;
- инструменты, которые что-то меняют (имя с `send/create/delete/update/…`
  или аннотация `destructiveHint`), выполняются только если в сообщении
  самого пользователя есть явная просьба о действии
  (`agent_graph._block_unrequested_writes`) — инъекция из письма или
  документа их не запустит;
- статус серверов: `GET /api/mcp/status`, блок «MCP-серверы» в настройках
  (только просмотр, скрыт, если серверов нет).

---

## Система памяти — подробно

Система памяти живёт в `memory/` — отдельный, не зависящий от FastAPI пакет
(импортируется плоскими путями, поэтому `backend/app/memory_bridge.py`
добавляет `memory/` в `sys.path`). Единая точка входа — `MemorySystem`
(`memory/memory_system.py`), singleton через `memory_bridge.get_memory_system`:

- `update(text, user_id, source=None)` — запомнить; `source` (`Source`:
  тип email/telegram/calendar/text, id, время, для событий — `end_at`)
  определяет источник и период действия извлечённых фактов;
- `retrieve(query, user_id, top_k)` — найти;
- `get_all(user_id)` — всё, включая неактивное;
- `delete_all(user_id)` — удалить всё (с точечным удалением из FAISS).

Ручное добавление факта из панели «Память» идёт через
`memory_bridge.handmade_memory`, профильные факты — через
`replace_profile_facts`.

Каждый текст проходит **пайплайн записи**
(`MemoryPipeline.process`, `memory/memory_pipeline.py`):

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
                                                                        │
                                                                        ▼
                                                        DreamingEngine (по порогу изменений,
                                                        best-effort: сбой не теряет факт)
```

Ниже «LLM памяти» — это `GigaChat-2` через `GigaChatTextModel` или, без
ключа, локальный Qwen3-4B (один и тот же интерфейс `generate()`).

### 1. Дешёвый роутер (`CheapMemoryRouter`)

Первый фильтр, **до** любых моделей (`memory/cheap_memory_router.py`).
Регэкспами отсекает то, что заведомо не память: пустые строки, короткие
подтверждения («спасибо», «ок»), вопросы, явные команды («покажи»,
«напомни», ...). Если сработал — дальше пайплайн не идёт.

### 2. Извлечение кандидатов памяти

`MemoryEnricher.process()` (`memory/enricher.py`) выбирает один из трёх
маршрутов:

**a) Правило-ориентированный роутер (`MemoryInputRouter`,
`memory/memory_input_router.py`)** — deterministic-слой без LLM:
- `DROP` — вопрос/команда/подтверждение → ничего не сохраняется;
- `FAST` — однозначно извлекается regex-правилами
  (`memory/extractor_contextual.py`), включая «цель завершена»
  (`COMPLETION_PATTERN` → `MemoryType.STATE`, `event_type=goal_completion`);
- иначе — эскалация к LLM.

Многосоставные сообщения разбираются по предложениям; если хоть одно не
разбирается правилами или содержит местоимение со ссылкой на другое
предложение — весь текст уходит в LLM целиком.

**b) `UnifiedMemoryAnalyzer`** (`memory/unified_memory_analyzer.py`) —
**один** вызов LLM памяти: извлечение, классификация по 8 типам и отношения
(subject-predicate-object) в одном JSON. Промпт запрещает сохранять
вопросы/команды/разовые действия и требует отличать факты о пользователе от
чужих, гипотетических и шуточных. Ответ разбирается терпимым парсером (умеет
вытащить завершённые объекты из обрезанного JSON); затем удаляются
«командные» предложения, проверяется пересечение с исходным текстом
(`_has_source_evidence` — защита от выдуманных фактов), эвристическая
переклассификация (procedure→preference, preference→goal).

**c) Legacy-пайплайн** (`_legacy_process`, фолбэк) — `MemoryExtractor` +
`MemoryValidator` отдельными вызовами.

**Типы памяти** (`memory/models.py`, `MemoryType`): `fact`, `preference`,
`episode`, `entity`, `relationship`, `goal`, `state`, `procedure`.

### 3. Обогащение (enrichment)

Запускается только когда эвристики решают, что это нужно:

- **Coreference** (`memory/coreference.py`, LLM памяти) — разрешение
  местоимений (legacy-путь, при наличии местоимения).
- **Temporal** (`memory/temporal.py`) — временные выражения, всегда.
- **Entity extraction** (GLiNER, `entity_enricher.py`) — если есть
  `ENTITY`/`RELATIONSHIP` или маркер отношений («мой брат», «коллега»).
- **Grounding** (`CandidateGrounder`) — привязка к сущностям и позиции в тексте.
- **Subject resolution** (`SubjectResolver`) — о ком факт: пользователь или
  третье лицо.
- **Relation extraction/linking** (`RelationExtractor` + `RelationLinker`)
  — для legacy-пути.
- **Grammar normalization** — один батч-вызов на все кандидаты, правит
  только падежи/согласование.

Результат — `EnrichedMemoryCandidate`; всё обогащение складывается в
`metadata` записи.

### 4. Сопоставление с существующей памятью (matching)

`MemoryMatcher.find_matches()` (`memory/memory_matcher.py`) считает `score`
0–1 со всеми **активными** записями пользователя:

- семантическая нормализация в (subject, predicate, object)
  (`SemanticMemoryNormalizer`);
- текстовое сходство (`SequenceMatcher` + Jaccard, 65/35);
- совпадение subject+predicate при другом object → score 0.88 (кандидат на
  `SUPERSEDE`: «переехал из одного города в другой»);
- завершение цели (`goal_completion`) против активной `GOAL` → 0.95.

Порог отсечения по умолчанию `min_score=0.50`.

### 5. Консолидация (что делать с кандидатом)

`MemoryConsolidator.decide()` (`memory/memory_consolidator.py`):

| Действие | Когда |
|---|---|
| `CREATE` | совпадений нет или они слабые |
| `CONFIRM` | тот же смысл — запись «освежается» (`freshness=1.0`, confidence = max) |
| `UPDATE` | score в `[0.70, 0.85)` — та же мысль другими словами → контент перезаписывается; также архивирует завершённую цель |
| `SUPERSEDE` | то же subject+predicate, другое значение — старая запись `SUPERSEDED` (`valid_until` = сейчас), новая `ACTIVE` |
| `CONFLICT` | запись со статусом `CONFLICTING` вместо тихой перезаписи |
| `IGNORE` | ничего не делать |

Пороги по умолчанию: `confirm_threshold=0.85`, `update_threshold=0.70`.

### 6. Применение решения (запись в БД)

`MemoryUpdater.apply()` (`memory/memory_updater.py`) → `MemoryRepository`
(таблица `memories` в `backend/data/memory.db`). В метаданные
(`_build_metadata`) попадают `subject`, `entities`, `relations`,
`temporal`, `grounding`, `source_text`, а для событий календаря —
`event_start`/`event_end`. `valid_from` берётся из времени источника
(письма, сообщения, начала события), а не из момента импорта.

Модель `Memory` (`memory/models.py`): `id`, `user_id`, `type`, `content`,
`confidence`, `importance`, `freshness`, `status`
(`active`/`superseded`/`expired`/`archived`/`conflicting`), `sources`,
`created_at`/`updated_at`/`valid_from`/`valid_until`, `metadata` (JSON).

### 7. Векторный индекс и поиск (retrieval)

SQLite — источник истины; **FAISS — производный индекс**
(`memory/faiss_memory_index.py`, `IndexFlatIP`, L2-нормализованные
эмбеддинги).

- `MemoryIndexManager.ensure_ready()` при старте загружает индекс с диска
  (`backend/data/memory_index/`) и сверяет с базой по SHA-256 сигнатурам;
  при рассинхроне — полная пересборка.
- После записи: `CREATE` — инкрементальное добавление, `UPDATE`/`SUPERSEDE`
  — пересборка, удаление — точечное `remove(ids)`.
- `backend/inspect_memory_index.py [user_id] [--vectors]` — dev-утилита:
  показывает, что лежит в индексе, и находит рассинхрон с `memory.db`.

Поиск (`MemorySystem.retrieve` → `MemoryRetrieverV2`):

1. **FAISS** — запрос кодируется эмбеддером, топ-`candidate_k`, фильтр по
   `user_id` и `status == ACTIVE`.
2. **Реранкинг и пост-фильтры**: кросс-энкодер переранжирует кандидатов;
   временной маркер в запросе становится жёстким фильтром; запросы про
   цели поднимают `GOAL`; сущностные запросы («расскажи о ...») раскрывают
   граф связей от лучшего seed'а (`_connected_memories`); иначе —
   пороговый семантический фолбэк (сигмоида от logit'а, отсечение ниже
   `0.01` и на резком обрыве скора) — `top_k` это максимум, а не
   обязательство.

### 8. Как память попадает в ответ агента

Автоматического впрыскивания памяти в промпт нет: агент сам вызывает
`search_memory` / `list_memories` (иногда несколько раз с разными
формулировками). Найденное форматируется `MemoryContextFormatter` и
приходит модели как результат функции.

### 9. Dreaming (batch-консолидация)

`memory/dreaming_engine.py::DreamingEngine` — запускается автоматически,
когда накопилось достаточно изменений, или вручную кнопкой «Осмыслить»
(`POST /api/memories/dream`). За один LLM-проход сливает явные дубликаты в
обобщённую формулировку и архивирует устаревшее/отменённое/завершённое.
Физически ничего не удаляется — только `status=ARCHIVED`.

---

## Хранилища данных

Всё лежит в `backend/data/` (создаётся автоматически, исключено из git):

| Файл/папка | Что содержит |
|---|---|
| `app.db` | SQLite приложения (`backend/app/db.py`): `users` (в т.ч. учётные данные интеграций), `conversations`, `chat_messages`, `tasks`, `expenses` (ручные траты/доходы и транзакции из банковских писем), `meals`, `weights`, `raw_emails`, `raw_calendar_events`, `raw_messages` (импорт с флагами безопасности), `imported_calendar_events`, `sent_email_drafts`, `calendar_action_drafts` |
| `memory.db` | SQLite: таблица `memories` — вся долгосрочная память |
| `memory_index/` | `memories.faiss` + `memory_ids.json` (id→вектор + SHA-256 сигнатуры) |

Авторизация — JWT (`backend/app/auth.py`), регистрация по логину/паролю
(bcrypt) или через Google Sign-In. `user_id` берётся только из проверенного
токена.

---

## API backend

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/api/health` | проверка живости |
| `POST` | `/api/auth/register`, `/api/auth/login` | `{username, password}` → `{access_token}` |
| `POST` | `/api/auth/google` | `{id_token}` → `{access_token}` |
| `POST`/`GET` | `/api/conversations` | создать чат / список чатов |
| `DELETE` | `/api/conversations/{id}` | удалить чат |
| `POST` | `/api/chat` | `{conversation_id, message, enable_thinking?}` → SSE (`token`/`tool_call`/`done`) + фоновая запись в память |
| `GET` | `/api/history?conversation_id=...` | история чата |
| `POST` | `/api/transcribe` | аудио → текст (Whisper на cloud.ru) |
| `POST` | `/api/documents/extract` | PDF/DOCX/текст → безопасный блок для чата (без скрытого текста, с маскированием и датами) |
| `GET` | `/api/proactive` | проактивные подсказки для стартового экрана |
| `POST` | `/api/import` | импорт `email`/`telegram`/`calendar` (`file`, `source?` — иначе автоопределение, `replace?`, `sync_calendar?`) |
| `GET` | `/api/memories` | все записи памяти (включая неактивные) |
| `GET` | `/api/memories/export` | экспорт памяти (замаскированный) |
| `GET` | `/api/memories/search?q=...` | семантический поиск по памяти |
| `POST` | `/api/memories` | `{content}` — добавить факт вручную |
| `POST` | `/api/memories/dream` | batch-консолидация |
| `DELETE` | `/api/memories/{id}` | удалить запись памяти |
| `DELETE` | `/api/account/data` | удалить все данные аккаунта (память, импорт, записи) |
| `GET` | `/api/sandbox` | последние задачи/траты/приёмы пищи/вес |
| `GET` | `/api/finance/transactions`, `/api/finance/summary` | данные для панели «Финансы» |
| `GET`/`PUT` | `/api/settings` | настройки пользователя (ключ Intervals.icu, статусы интеграций) |
| `GET` | `/api/google-calendar/connect`, `/callback`; `POST` `/disconnect` | OAuth Google Calendar |
| `GET` | `/api/gmail/connect`, `/callback`; `POST` `/disconnect` | OAuth Gmail |
| `POST` | `/api/gmail/send` | отправить черновик — только с кнопки в UI |
| `POST` | `/api/calendar/confirm` | выполнить create/update/delete события — только с кнопки в UI |

Все эндпоинты, кроме health/auth/OAuth-callback, требуют
`Authorization: Bearer <token>`; чужой `conversation_id` → 404.

---

## Frontend

React 19 + Vite, без стейт-менеджера. На стартовом экране чата (`App.jsx`)
показываются проактивные подсказки из `/api/proactive` — клик отправляет
готовый запрос в чат. Интерфейс на русском и английском
(`lib/i18n.jsx`), светлая/тёмная/системная тема (`lib/settings.js`).

- `Login.jsx` — вход/регистрация, Google Sign-In;
- `Chat.jsx` — сообщения с markdown, индикация вызовов инструментов,
  встроенные карты и маршруты (`MapboxMap.jsx`), карточки-черновики письма
  (кнопка «Отправить») и событий календаря (кнопка «Подтвердить»),
  «thinking»-фразы во время ожидания;
- `Composer.jsx` — ввод с авто-расширением, голосовой ввод (зажать и
  говорить), прикрепление документов и датасетов (`.json`/`.jsonl` при
  отправке уходят в импорт с автоопределением источника), переключатель
  «Размышления»;
- `MemoryPanel.jsx` — что ассистент запомнил (с источниками и периодом),
  ручное добавление, семантический поиск, удаление, «Осмыслить»;
- `FinancePanel.jsx` — транзакции и сводка по месяцам/категориям;
- `SettingsPanel.jsx` — тема, язык, подключение Intervals.icu / Google
  Calendar / Gmail, удаление всех данных аккаунта;
- `ChatList.jsx`, `Menu.jsx`, `OrbCanvas.jsx` (Three.js-«орб» состояния).

`lib/api.js` — тонкий клиент с ручным разбором SSE; все запросы идут на
относительный `/api/...` через Vite-прокси. `lib/auth.js` хранит JWT в
`localStorage`.

---

## Самопроверка на эталонных вопросах

`competition_task/eval_reference.py` прогоняет контрольные вопросы из
`competition_task/data/reference_answers.md` через настоящего агента: грузит
датасет в отдельного eval-пользователя, фиксирует «сейчас» на опорный момент
датасета (`ASSISTANT_NOW`), задаёт каждый вопрос **дважды** разными
формулировками в новых чатах (как жюри) и проверяет, что оба ответа содержат
ожидаемые значения.

```bash
python competition_task/eval_reference.py                  # загрузить + спросить всё
python competition_task/eval_reference.py --no-load q11 q14
python competition_task/eval_reference.py --with-memory    # ещё и заполнить фактовую память (медленно)
python competition_task/eval_reference.py --security       # подложить атаки в данные
python competition_task/eval_reference.py --data other_dir --now 2026-10-01T10:00:00+03:00
```

Нужен `backend/.env` с ключами GigaChat — каждый вопрос это реальный вызов
LLM. Открытые эталонные данные — только пример: жюри проверяет на другом,
закрытом датасете, поэтому решения должны обобщаться, а не подгоняться.

`competition_task/test_memory_emails.py` — отдельная проверка извлечения
фактов из писем.

---

## Структура репозитория

```
backend/
  app/
    main.py               — FastAPI роуты, CORS, startup, импорт
    agent.py              — один ход диалога (стрим, маскирование, цитаты, сохранение)
    agent_graph.py        — LangGraph-граф (agent ⇄ masked tools), выбор LLM
    chat_gigachat.py      — LangChain-адаптер над GigaChat
    chat_model.py         — LangChain-адаптер над локальным Qwen3-4B (фолбэк)
    prompts.py            — сборка системного промпта
    tools.py              — экшн-тулы, интеграции, memory tools
    data_tools.py         — инструменты по импортированным данным
    personal_data.py      — нормализация и хранение импорта, часовой пояс, «сейчас»
    personal_analytics.py — детерминированная аналитика (календарь, почта, финансы, контакты)
    profile_builder.py    — цифровой профиль → факты памяти
    proactive.py          — детекторы проактивных подсказок
    bank_parser.py        — банковские уведомления → транзакции
    injection_guard.py    — детектор инъекций и фишинга
    masking.py            — маскирование секретов (данные, API, логи)
    documents.py          — разбор вложений PDF/DOCX, удаление скрытого текста
    google_oauth.py, google_calendar.py, gmail.py — Google-интеграции
    transcription.py      — голос → текст (cloud.ru Whisper)
    memory_bridge.py      — мост backend ↔ memory/ (ingest, профиль, удаление)
    auth.py, conversations.py, user_settings.py, db.py, schemas.py
    mcp_tools.py          — опциональные MCP-серверы
  inspect_memory_index.py — dev-утилита для FAISS-индекса
  .env.example            — все переменные окружения с комментариями
  mcp_servers.example.json
  requirements.txt

frontend/src/
  App.jsx, main.jsx, styles.css
  components/  Chat, Composer, ChatList, Login, Menu, OrbCanvas, MapboxMap,
               MemoryPanel, ImportPanel, FinancePanel, SettingsPanel
  lib/         api.js, auth.js, i18n.jsx, markdown.js, mapbox.js, settings.js

memory/                     — пакет долгосрочной памяти
  memory_system.py          — фасад (update/retrieve/get_all/delete_all)
  model_registry.py         — singleton-ы моделей, выбор облако/локально
  gigachat_client.py, gigachat_text_model.py, gigachat_grammar_normalizer.py,
    gigachat_embedder.py    — GigaChat-бэкенды
  cloud_ru_embedder.py, cloud_ru_reranker.py — cloud.ru-бэкенды
  qwen_model.py, memory_embedder.py, memory_reranker.py,
    memory_grammar_normalizer.py, entity_enricher.py — локальные модели
  models.py                 — Memory/Source/MemoryType/MemoryStatus
  cheap_memory_router.py, memory_input_router.py, candidate_router.py
  unified_memory_analyzer.py, extractor_contextual.py, memory_validator.py
  enricher.py, coreference.py, temporal.py, candidate_grounder.py,
    subject_resolver.py, relation_extractor.py, relation_linker.py
  memory_pipeline.py, memory_matcher.py, semantic_normalizer.py,
    memory_consolidator.py, memory_updater.py
  memory_repository.py, persistent_memory_store.py, memory_store.py
  faiss_memory_index.py, memory_index_manager.py, retrieval_model_manager.py
  memory_retriever.py, memory_retriever_v2.py
  memory_context.py, memory_context_formatter.py
  dreaming_engine.py

competition_task/           — условия кейса, данные, eval_reference.py

dev.sh                      — debug-запуск обоих сразу
backend.sh / frontend.sh    — запуск по отдельности
stop.sh                     — остановка (по портам 8001/5173)
```

---

## Известные ограничения

- GLiNER пока всегда локальный (CPU) — у него нет API-аналога; поэтому
  `requirements.txt` по-прежнему тянет torch/transformers.
- `GigaChat-2` иногда игнорирует шкалу `confidence` (всегда 0.0) и формат
  JSON-массива — формат чинится автоматически, качество стоит
  перепроверять; при необходимости — `GigaChat-2-Pro/Max` через
  `GIGACHAT_LITE_MODEL`.
- Эмбеддинги GigaChat на тарифе `GIGACHAT_API_PERS` недоступны (402), поэтому
  эмбеддер и реранкер — на cloud.ru.
- Фоновое извлечение фактов при большом импорте занимает время: точные
  вопросы по данным работают сразу, а профиль в памяти дозаполняется
  постепенно.
- Детекторы инъекций и банковский парсер — эвристические: хорошо обобщаются
  на типовые атаки и формулировки, но не гарантируют 100% покрытия.
- В локальном режиме (без ключей) нужен GPU с запасом VRAM; на 8 ГБ
  Qwen3-4B + эмбеддер + реранкер занимают почти всё (GLiNER и нормализатор
  поэтому по умолчанию на CPU).
- Автоматического пересчёта `importance`/`freshness`/`expired` во времени
  нет — поля в модели `Memory` есть, фонового процесса нет.
