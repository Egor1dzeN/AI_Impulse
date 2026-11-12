# Impulse — architecture

> Product overview (how to load data and what to try) is in [README.md](../README.md).

A personal AI assistant with long-term memory and proactive actions
(**AI-Impulse 2.0** hackathon, track 2, Restore case; task description, in Russian:
[`competition_task/README.md`](../competition_task/README.md)).

The assistant builds up knowledge about its user on its own. From messages
(email, Telegram), the calendar and bank notifications it assembles a **digital
profile** where every fact has a source and a validity period. It answers precise
questions about this data **deterministically** (the same way regardless of
wording), **notices reasons to act** and suggests them, **performs actions** in
external systems (Google Calendar, Gmail, Intervals.icu) only after confirmation
in the UI, **masks** sensitive data at every boundary and **resists prompt
injections** hidden in the data.

Stack: **FastAPI** (backend) + **React 19 / Vite** (frontend) + **LangGraph**
(agent loop) + **GigaChat** (dialogue and the LLM part of memory) + **cloud.ru**
(embedder, reranker, Whisper) + a custom modular **long-term memory system**
(`memory/`) on top of SQLite and FAISS.

Local mode is still there: if no API keys are set, the backend falls back to
local models (Qwen3-4B, Qwen3-Embedding/Reranker-0.6B, Qwen3-0.6B). Handy for
offline development, but it needs a GPU.

---

## Contents

- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Server deployment](#server-deployment)
- [Architecture](#architecture)
- [Models and where they run](#models-and-where-they-run)
- [Backend: how a message is handled](#backend-how-a-message-is-handled)
- [Data import and the digital profile](#data-import-and-the-digital-profile)
- [Deterministic data analytics](#deterministic-data-analytics)
- [Proactivity](#proactivity)
- [Security: masking, injections, confirmations](#security-masking-injections-confirmations)
- [Agent tools (function calling)](#agent-tools-function-calling)
- [Integrations](#integrations)
- [MCP servers](#mcp-servers)
- [The memory system in detail](#the-memory-system-in-detail)
- [Data storage](#data-storage)
- [Backend API](#backend-api)
- [Frontend](#frontend)
- [Self-check on reference questions](#self-check-on-reference-questions)
- [Repository layout](#repository-layout)
- [Known limitations](#known-limitations)

---

## Quick start

The one-command way to start everything is `scripts/dev.sh` (Git Bash / MSYS; works on
Windows, Linux and macOS). It is also the **debug run with hot reload**: backend
and frontend pick up changes on the fly.

### 0. Keys

```bash
cp backend/.env.example backend/.env
# fill in at least GIGA_CHAT_IDE and GIGA_CHAT_EMBEDDING — see below
```

`scripts/backend.sh`/`scripts/dev.sh` load `backend/.env` before starting uvicorn.
The file is in `.gitignore` and never goes into the repository.

### Option 1 — one command (recommended)

```bash
scripts/dev.sh
```

It kills anything already listening on ports 8001/5173, creates `backend/.venv`
on first run, installs dependencies from `requirements.txt`, runs `npm install`
in `frontend/`, then starts both processes:

- **backend** — `uvicorn ... --reload --reload-dir app`: changes in
  `backend/app/**/*.py` are picked up automatically;
- **frontend** — `npm run dev` (Vite) with HMR.

After start:
- Backend: http://127.0.0.1:8001 (logs → `backend_run.log`, pid → `backend.pid`)
- Frontend: http://localhost:5173 (logs → `frontend_run.log`, pid → `frontend.pid`)

Open **http://localhost:5173**, register (username/password or Google) and
attach dataset files in the chat (for example, files from
`competition_task/train_dataset/`).

Stop both processes:

```bash
scripts/stop.sh
```

> **Note on `memory/`:** changes in the `memory/` package (it lives in the
> project root, outside `backend/app/`) are **not** reloaded automatically. This
> is intentional: memory models and clients are created lazily there, and a full
> uvicorn restart on every edit would reload them. After editing `memory/`,
> restart `scripts/dev.sh` manually.

### Option 2 — separately, in two terminals

```bash
scripts/backend.sh   # first terminal — FastAPI/uvicorn, port 8001
```

```bash
scripts/frontend.sh  # second terminal — Vite dev server, port 5173
```

Both run in the foreground (output is also written to `*_run.log`) and stop with
Ctrl+C or `scripts/stop.sh`. Backend health check: http://127.0.0.1:8001/api/health

<details>
<summary>Fully manual, without scripts</summary>

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

### Notes

- The backend **must** run on `127.0.0.1:8001`: the frontend proxies `/api`
  there via the Vite dev proxy (`frontend/vite.config.js`), and CORS in
  `backend/app/main.py` only allows `localhost:5173`/`127.0.0.1:5173`.
- `requirements.txt` still pulls heavy ML dependencies (torch CUDA 12.8,
  transformers, bitsandbytes, faiss, sentence-transformers, gliner). They are
  needed for the local fallback and for GLiNER, which is always local for now.
  The first install takes a while.
- The first message can be slower than the rest: memory models and clients are
  created lazily on first use (`memory/model_registry.py`, one singleton per
  process).
- For a server deployment, see [Server deployment](#server-deployment).

---

## Environment variables

The full, current list with comments is in `backend/.env.example`. The main ones:

| Variable | What it enables |
|---|---|
| `GIGA_CHAT_IDE` | GigaChat key. Enables GigaChat for **chat** (`GIGACHAT_MODEL`, default `GigaChat-3-Ultra`) and for the **LLM part of memory**: extraction, coreference, relation extraction, dreaming, validation, grammar normalization (`GIGACHAT_LITE_MODEL`, default `GigaChat-2`). Without it, local Qwen3-4B is used |
| `GIGA_CHAT_EMBEDDING` | actually a **cloud.ru** foundation-models key (the name is historical). Enables cloud `Qwen3-Embedding-0.6B`, `Qwen3-Reranker-0.6B` (the same checkpoints as locally, so FAISS doesn't need reindexing) and Whisper for voice input |
| `GOOGLE_CLIENT_SECRET` | OAuth for Google Calendar and Gmail (the same OAuth client as Google Sign-In; requires the Calendar API/Gmail API to be enabled and the redirect URIs registered) |
| `AUTH_SECRET_KEY` | secret for signing JWTs. **Must** be set on any non-local deployment: without it an insecure development default is used |
| `MAPBOX_TOKEN` | interactive maps and routes |
| `REGISTRATION_CODE` | invite code: without it no new account (username/password or Google) can be registered. Empty means open registration |
| `FRONTEND_URL`, `GOOGLE_CALENDAR_REDIRECT_URI`, `GMAIL_REDIRECT_URI` | public address and OAuth callbacks for deployments not on localhost |
| `GIGACHAT_FALLBACK_MODEL` | model the chat switches to when the main one runs out of quota (402), default `GigaChat-2` |
| `MCP_SERVERS_FILE`, `MCP_ALLOW_PRIVATE_URLS` | path to the system MCP servers config; allow `http`/private addresses for user MCP servers (development only) |
| `ASSISTANT_NOW` | (optional) ISO date/time that "freezes" the assistant's current moment, for reproducible checks on a dataset with a fixed date |
| `GIGACHAT_USE_EMBEDDINGS` | optionally switch the embedder to GigaChat (on the `GIGACHAT_API_PERS` plan embeddings return 402, so it is off by default) |
| `QWEN_4BIT`, `GLINER_DEVICE`, `GRAMMAR_DEVICE` | fine-tuning of local models |

The Intervals.icu key is not set in `.env`: each user enters their own in the
"Settings" panel (the server validates it before saving).

---

## Server deployment

Reference setup: Ubuntu 24.04, two systemd services and Caddy:

```
browser ──HTTPS──▶ Caddy (:443, Let's Encrypt, your domain, gzip, SSE without buffering)
                     └─▶ ai-impulse-frontend  vite preview (built frontend/dist), :5173
                            └─ /api/* ──▶ ai-impulse-backend  uvicorn app.main:app, 127.0.0.1:8001
```

The backend listens only on `127.0.0.1` and is reachable from outside only
through the `/api` proxy (`frontend/vite.config.js`).

Initial setup:

```bash
git clone <repository> ~/ai_impulse && cd ~/ai_impulse
python3 -m venv backend/.venv
backend/.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU build, no GPU needed
backend/.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env    # GigaChat/cloud.ru keys, AUTH_SECRET_KEY, REGISTRATION_CODE,
                                        # FRONTEND_URL and Google redirect URIs for your domain
(cd frontend && npm ci && npm run build)
# + systemd unit files for both services and a Caddyfile with reverse_proxy, as in the diagram
```

Updating — one command on the server:

```bash
cd ~/ai_impulse && scripts/deploy.sh          # git pull, dependencies/build only when changed,
                                        # restart affected services, check /api/health
scripts/deploy.sh --force                     # rebuild and restart everything
```

**Audit log** (`backend/app/audit.py`): a full history of who asked what and
how the system handled it, in `backend/audit/audit-YYYY-MM-DD.jsonl` (one JSON
line per event; the folder is outside `backend/data/`, so neither "Delete all
memory" nor wiping the databases removes it; everything is masked):
- `http` — every `/api` request: account (from the JWT), IP, method, path,
  request body without passwords, status, timing, JSON response;
- `chat_turn_start` / `tool_call` / `model_note` / `chat_retry` / `chat_error` /
  `chat_turn_done` — the whole chat turn: the question, the system prompt hash
  (the prompt itself is in `audit/prompts/<sha>.txt`), every tool call with
  arguments and result, guard refusals, retries, the final answer and duration;
- `memory_update` — what memory created, updated or discarded.

```bash
backend/.venv/bin/python backend/scripts/audit_view.py                        # summary per account
backend/.venv/bin/python backend/scripts/audit_view.py --user <username>      # account timeline
backend/.venv/bin/python backend/scripts/audit_view.py --user <username> --full  # untruncated
backend/.venv/bin/python backend/scripts/audit_view.py --prompt <sha>         # system prompt of a turn
```

Backups of the databases, the index and the audit log: `backend/scripts/backup_db.py`
(SQLite online backup, safe on a running server). A typical setup runs it
hourly via cron into `~/backups/db/`, keeping the last 72.

Logs: `journalctl -u ai-impulse-backend -f` (secrets in logs are masked), Caddy
access log: `/var/lib/caddy/access.log`. All data lives in `backend/data/`
(see [Data storage](#data-storage)); to wipe a server completely, stop the
backend and remove that folder, and the databases are recreated on start.

---

## Architecture

```
┌──────────────────────┐   SSE (/api/chat)   ┌──────────────────────────────────────┐
│  Frontend (Vite)      │ ─────────────────▶ │         FastAPI backend (backend/app) │
│  React + Three.js +   │ ◀───────────────── │                                       │
│  Mapbox               │  token/tool_call/  │  masking on input/output/in logs      │
│  panels: Memory,      │  done              └───────┬───────────────────┬──────────┘
│  Finance, Settings;   │                            │                   │
│  datasets via chat    │   POST /api/import         │                   │
└──────────┬───────────┘ ─────────────────────▶ personal_data         LangGraph agent
           │                                    (normalization, mask,  (agent_graph.py)
           │ "Send"/"Confirm" buttons           injection_guard,          │
           │ on draft cards                     bank_parser)              │
           │                                         │        ┌──────────┼───────────────┬──────────────┐
           ▼                                         │        │          │               │              │
   /api/gmail/send, /api/calendar/confirm           │   ChatGigaChat  data_tools     action tools   memory tools
   (the only path to real outward writes —          │   (or ChatQwen)  → personal_   (tasks, spending, search_memory
    not available to the agent)                     │                   analytics    food, maps,      list_memories
                                                     │                   (no LLM)     weather, calendar, │
                                                     │                                email, Intervals) │
                                                     ▼                                                  ▼
                                    background fact extraction  ─────────────▶  memory/ — MemorySystem
                                    + profile_builder                          (SQLite + FAISS,
                                                                                see "The memory system")
```

Key ideas:

- **Two knowledge stores with different jobs.** Raw imported data
  (`raw_emails`, `raw_calendar_events`, `raw_messages`, transactions) is stored
  verbatim in `app.db` and answered by deterministic functions (sums, free
  slots, rankings). Fuzzy **fact memory** (`memory/`) holds the profile:
  preferences, relationships, goals, each with a source and validity period.
- **Chatting and remembering are independent paths.** The answer is streamed
  immediately, while fact extraction from the message/import runs in
  `BackgroundTasks`.
- **The model phrases, the code computes.** The agent takes all numbers, dates
  and lists from functions instead of computing them, so answers are
  reproducible.
- **Outward writes only via a UI button.** The agent prepares drafts; real
  Gmail/Calendar calls happen only when the user clicks.

---

## Models and where they run

All models and clients are created once per process via
`memory/model_registry.py` (`ModelRegistry`, a thread-safe singleton). Every role
has a cloud backend (used when its key is set) and a local fallback.

| Role | Default (with keys) | Local fallback | Files |
|---|---|---|---|
| Agent dialogue LLM | **GigaChat** `GigaChat-3-Ultra` (`GIGA_CHAT_IDE`) | Qwen3-4B, 4-bit NF4 | `backend/app/chat_gigachat.py`, `chat_model.py`, `agent_graph.py::_build_llm` |
| Memory LLM: extraction, coreference, relations, dreaming, validation | **GigaChat** `GigaChat-2` | the same Qwen3-4B | `memory/gigachat_text_model.py`, `memory/qwen_model.py` |
| Grammar normalization of facts | **GigaChat** `GigaChat-2` | Qwen3-0.6B on CPU | `memory/gigachat_grammar_normalizer.py`, `memory_grammar_normalizer.py` |
| Memory search embedder | **cloud.ru** `Qwen/Qwen3-Embedding-0.6B` (`GIGA_CHAT_EMBEDDING`) | the same checkpoint locally, 8-bit | `memory/cloud_ru_embedder.py`, `memory_embedder.py` |
| Reranker | **cloud.ru** `Qwen/Qwen3-Reranker-0.6B` (`/score` endpoint) | the same checkpoint locally, FP16 | `memory/cloud_ru_reranker.py`, `memory_reranker.py` |
| Speech recognition | **cloud.ru** `openai/whisper-large-v3` | — | `backend/app/transcription.py` |
| NER (entities for memory enrichment) | — | **GLiNER** `urchade/gliner_multi-v2.1`, CPU | `memory/entity_enricher.py` |

GigaChat quirks handled in code:

- the content of a message with the `function` role must be valid JSON;
  `chat_gigachat.py::_ensure_json_content()` wraps plain-text tool results;
- `GigaChat-2` sometimes returns several objects in a row instead of a JSON
  array; `gigachat_text_model.py::_repair_json_array()` fixes that.

The local `ChatQwen` is a `BaseChatModel` adapter over Qwen3-4B: the function
list is appended to the system prompt, and the model replies with a
```` ```tool_call {...}``` ```` block that is parsed into `AIMessage.tool_calls`.
To the graph both backends look the same (`bind_tools()`/`invoke()`).

---

## Backend: how a message is handled

`POST /api/chat` (`backend/app/main.py`) returns `text/event-stream` (SSE) with
`token`, `tool_call` and `done` events. One turn is
`agent.handle_chat_turn_stream` (`backend/app/agent.py`):

1. The user's message is **masked** right away (`masking.mask_sensitive`), so a
   card number or password never reaches the model, the history or the logs.
2. The system prompt is built (`prompts.build_system_prompt`) with the current
   moment in the user's time zone (`personal_data.now_local`, overridable via
   `ASSISTANT_NOW`) and a set of blocks depending on connected integrations.
3. The last `HISTORY_MESSAGE_LIMIT` messages of **this chat**
   (`conversation_id`) are loaded as short conversational memory.
4. The LangGraph graph is assembled (`agent_graph.py`): `agent` ⇄ `tools` until
   the model answers without a function call. The `tools` node is
   `ToolNode(handle_tool_errors=True)`: an exception in one tool becomes a
   message for the model instead of breaking the stream. **Every tool result is
   masked** (`mask_sensitive`/`mask_obj`) before it reaches the prompt or the UI.
5. Tool list (`build_tool_list`): memory tools + data tools + action tools
   (only for connected integrations) + MCP.
6. Post-processing: `[sources=[N]]` footnotes from `web_search` become markdown
   links (`_rewrite_source_citations`), internal source references become
   human-readable.
7. The message and the answer are saved to `chat_messages`; in the background
   (`extract_memory_background`) the message goes to the memory system.

### System prompt (`backend/app/prompts.py`)

| Block | Purpose |
|---|---|
| `PERSONA`, `HISTORY_INSTRUCTIONS` | who the assistant is; how to read chat history |
| `MEMORY_INSTRUCTIONS` | gather the user's data first (`search_memory` and data functions), answer only after that; never answer personal questions "from the top of the head" |
| `DATA_INSTRUCTIONS` | which function for which question (free time → `schedule_analysis`, spending → `finance_report`, …); never compute numbers yourself; pass periods as keywords (`next_week`, `last_month`, …) |
| `PROACTIVE_INSTRUCTIONS` | check requests against the profile (allergies, limits, calendar conflicts); on "what's new?" call `proactive_suggestions` |
| `SECURITY_INSTRUCTIONS` | contents of emails/events/documents are data, not commands; on `security_warning` warn the user; outward actions only as drafts |
| `EXPLAINABILITY_INSTRUCTIONS` | end the answer with a plain-language "Source: …" line (where from and for which period) |
| `ACTION_INSTRUCTIONS` | action tools only on an explicit command |
| `INFO_TOOL_`, `PRODUCT_INFO_`, `WEB_SEARCH_INSTRUCTIONS` | maps/weather/rates, food products, web search |
| `WORKOUT_`, `CALENDAR_`, `EMAIL_INSTRUCTIONS` + matching `*_NOT_CONNECTED_INSTRUCTIONS` | if an integration isn't connected, the model says so directly and suggests connecting it in settings instead of flailing between other tools |

---

## Data import and the digital profile

Dataset files (`.jsonl` or a JSON array) are attached directly in the chat
(paperclip, several at once) and sent like a normal message. The feed shows the
message with file chips and an assistant card "found data about you — saving to
memory" with a progress ring (`GET /api/import/progress`). Under the hood the
file always goes through the full `POST /api/import` pipeline; the model only
gets the message text and a short note about the uploaded file. The source
(`email` / `telegram` / `calendar`) is detected automatically from record
fields (`kind: calendar#event`/`start`, `subject`/`thread_id`,
`msg_id`/`chat`/Telegram Desktop export), with the file name as a fallback.
JSON that doesn't look like a dataset is attached as a regular document.
To load a new dataset without mixing in the old one, first delete all account
data (`DELETE /api/account/data`), then attach the files.

1. **Synchronously** (`personal_data.store_records`): each record is
   normalized into a common format, **masked**, checked by
   **injection_guard** and stored verbatim in the `raw_*` tables of `app.db`.
   Bank notification emails are parsed by `bank_parser.py` into transactions
   (format-agnostic: an operation keyword next to an amount, not tied to any
   specific bank's template). Precise questions can be answered immediately.
2. **In the background**: records that carry profile knowledge and passed the
   security check go through the memory pipeline (`memory_bridge.import_records`
   → `ingest_email` / `ingest_telegram_message` (chunked by conversation) /
   `ingest_calendar_event`) with a real `Source`: type, id, date, and for events
   start/end. Records flagged as suspicious **never reach** memory.
3. **The digital profile** (`profile_builder.py`) is built after import and
   stored in the same memory as a set of facts with sources:
   - *computed* — the user's name and how people address them, home city
     (from transaction locations), finances, routine, social circle,
     subscriptions: plain arithmetic over `personal_analytics`, identical on
     every rebuild;
   - *synthesized* — biography, relationships, sport, preferences, goals: one
     LLM pass per area over deterministically selected and labelled fragments,
     so every fact is tied to concrete sources.

   A rebuild replaces the old profile facts
   (`memory_bridge.replace_profile_facts`), so no duplicates appear.

Optional API parameters: `source` — set the source explicitly; `replace` — wipe
the account's data and cancel any background extraction still running from a
previous import; `sync_calendar` — also create the events in the connected
Google Calendar.

Memory export: `GET /api/memories/export` (masked). Full deletion of all account
data: `DELETE /api/account/data`.

---

## Deterministic data analytics

`backend/app/personal_analytics.py` holds all precise logic over imported data,
**without an LLM**: the same data gives the same numbers regardless of wording
and session. The agent calls it through `backend/app/data_tools.py` and only
phrases the answer.

| Tool | What it does |
|---|---|
| `data_overview` | what is loaded: volume per source, date range |
| `calendar_events` | events for a period (recurring ones expanded), participants |
| `schedule_analysis` | free slots of N minutes or more, the first suitable slot, the busiest day, candidates for rescheduling and what must not be touched |
| `recurring_schedule` | recurring events and habits (including patterns from emails) |
| `search_emails`, `get_email_thread` | search emails by topic/person/period, full text |
| `inbox_overview` | inbox: unanswered, urgent, deadlines and their status |
| `search_messages` | messenger search (chat, sender, period) |
| `top_contacts` | who the user talks to most (`scope` work/personal) |
| `finance_report` | spending/income for a period, categories, top places, month comparison |
| `monthly_cashflow` | average cash flow over complete months, one-off income separately; "how much can I save" |
| `proactive_suggestions` | see below |

Periods are passed as keywords (`today`, `tomorrow`, `next_week`,
`last_month`, `month_before_last`, `last_3_days`) or dates; `resolve_period`
resolves them relative to the user's current moment.

---

## Proactivity

`backend/app/proactive.py` contains detectors of reasons to act that the
assistant finds in the data on its own: emails that need a reply and upcoming
deadlines; conflicts and reschedules of important calendar meetings; financial
anomalies. Each item has a priority, an explanation, a source and a concrete
next step via an integration (a reply draft, a calendar event, a spending
limit), plus a ready prompt for the chat.

The detectors are deterministic. They are used by the start screen
(`GET /api/proactive`, cards when a chat opens) and by the agent
(`proactive_suggestions` — on "hi", "what's new?", "what should I pay attention
to?"). In addition, `PROACTIVE_INSTRUCTIONS` makes the agent check every request
against the profile: warn about calendar conflicts, allergens, overspending.

---

## Security: masking, injections, confirmations

**Masking** (`backend/app/masking.py`) covers card numbers (with a Luhn check;
the last 4 digits stay), CVV, account numbers, passports/IDs, passwords, tokens
and API keys. It is applied architecturally at every boundary: on import
(before storage, embedding and sending to the LLM), on every tool result (a
wrapper around `ToolNode`), on the user's message, on the saved answer, in API
responses and exports, and in logs (`MaskingLogFilter`, `install_log_masking`).
Ordinary personal data (name, phone, address) is intentionally not masked: the
assistant needs it to work.

**Prompt injection and phishing protection** (`backend/app/injection_guard.py`):
heuristics over normalized text (attempts to override instructions, requests to
forward/transfer/hide something, fake system notices, requests for payment
details) plus sender checks: look-alike domains (Levenshtein distance, brand
spoofing) and unestablished senders. Flagged records are stored (the user can
see them) but **never reach long-term memory**, and every tool result carrying
them includes a `security_warning` that the model must relay to the user rather
than act on.

**Document attachments** (`backend/app/documents.py`,
`POST /api/documents/extract`): PDF/DOCX/text are parsed on the server;
**hidden text** (white/invisible, tiny font, Word hidden formatting) is
separated and never reaches the model, only a warning about it does. Visible
text is checked for injections and masked; dates and deadlines are extracted
from it deterministically (an invoice gets a reminder 3 days ahead, a contract
one notice period ahead), and all of this is passed to the model in a block
explicitly marked as data.

**Confirmation of outward actions.** The agent has **no** tools that send an
email or change the calendar by themselves:
- `draft_email` only prepares a draft → a card in the chat with a "Send" button
  → `POST /api/gmail/send`;
- `create_calendar_event` / `update_calendar_event` / `delete_calendar_event`
  prepare a draft → "Confirm" button → `POST /api/calendar/confirm`.

These endpoints are unreachable from the tool-calling loop. The
`sent_email_drafts` / `calendar_action_drafts` tables make a repeated click (for
example on a stale card after a reload) a safe no-op.

**Deterministic guards in the graph** (`agent_graph.py`; they fire before a tool
runs, and the model gets an explanation instead of a result):
- `_block_unrequested_writes` — write tools (`log_meal`, `add_expense`,
  `create_task`, email and event drafts, "write" MCP tools) run only if the
  **user's own message** contains an explicit request (verbs like
  "record/add", "write/send", "schedule/move/cancel"…). Text of attached
  documents doesn't count, so an injection from an email or a file can't trigger
  an action, and a question like "I had pasta, 650 kcal, how much is left?"
  doesn't log a meal;
- `_limit_repeated_calls` — the third identical call (same function and
  arguments) within a turn is rejected, so the model doesn't loop when the
  needed tool doesn't exist (for example "send" with Gmail not connected);
- `_limit_external_calls` — at most 6 calls of one MCP tool per turn.

If Gmail isn't connected and the user asks to send an email, a warning is added
to the answer (`agent.wants_email_sent`); a false "sent" without a tool call is
replaced with an honest "nothing was done" (`claims_unperformed_action`).

**Explainability:** an answer based on the user's data ends with a "Source: …"
line (where from and for which period), and every memory fact stores `sources`
and `valid_from`/`valid_until`, visible in the "Memory" panel.

---

## Agent tools (function calling)

All tools are created anew for every request and bound to that request's
`user_id`; the model never sees `user_id` and can't substitute it. Integration
tools are added only when the integration is connected.

**Memory** (`tools.build_memory_tools`):

| Tool | Action |
|---|---|
| `search_memory(query)` | top 20 relevant facts (embed → FAISS → rerank) |
| `list_memories(source?)` | the whole profile without ranking, optionally filtered by source (`email`/`telegram`/`calendar`/`text`) |

**User data** — see [Deterministic data analytics](#deterministic-data-analytics).

**Actions and reference** (`tools.build_action_tools`):

| Tool | Action |
|---|---|
| `create_task`, `list_tasks` | tasks/reminders |
| `add_expense`, `add_income`, `list_transactions`, `update_transaction`, `get_finance_summary` | manual spending and income tracking |
| `log_meal`, `log_weight`, `get_health_summary` | food (2200 kcal target) and weight |
| `get_product_info(barcode)`, `search_product(query)` | Open Food Facts |
| `log_workout`, `get_workouts`, `get_workout_route` | Intervals.icu (if a key is connected); the workout route is drawn on a map |
| `create_calendar_event`, `get_calendar_events`, `update_calendar_event`, `delete_calendar_event` | Google Calendar (if connected); changes only as drafts for confirmation |
| `list_emails`, `get_email`, `draft_email` | Gmail (if connected); sending only via the UI button |
| `web_search(query)` | web search via `ddgs`, with source links |
| `show_map(place)`, `build_route(origin, destination, profile)` | geocoding (Nominatim) and an interactive map / route (Mapbox) |
| `get_weather(place)` | Open-Meteo |
| `get_exchange_rate(currency)`, `get_exchange_rate_history(currency, days_ago)` | Central Bank of Russia rates |

`GET /api/sandbox` returns the "sandbox": recent tasks/spending/meals/weight for
the "Memory" panel.

---

## Integrations

All integrations work the same way: credentials are stored in the `users` row,
tools are added only when credentials are present, and the prompt has an
`X_INSTRUCTIONS` / `X_NOT_CONNECTED_INSTRUCTIONS` pair.

- **Google Calendar** (`google_calendar.py`) and **Gmail** (`gmail.py`) — OAuth
  via the shared `google_oauth.py` (authorize URL, code exchange, refresh,
  revoke; `state` is a signed JWT with `user_id` and purpose, because Google's
  callback arrives without an authorization header). Connected in the
  "Settings" panel (`/api/google-calendar/connect`, `/api/gmail/connect`).
- **Intervals.icu** — an API key entered in "Settings", validated by the server
  before saving.
- **Voice input** (`transcription.py`, `POST /api/transcribe`) — hold the
  microphone button in the composer; browser webm/opus is transcoded to mp3 via
  `imageio-ffmpeg` (bundled ffmpeg, nothing to install) and sent to Whisper on
  cloud.ru.
- **Document attachments** — see [Security](#security-masking-injections-confirmations).
- **Maps** — Mapbox GL (`frontend/src/components/MapboxMap.jsx`); the map theme
  follows the app theme.

---

## MCP servers

`backend/app/mcp_tools.py` optionally connects external MCP servers via
`langchain-mcp-adapters`. While `backend/mcp_servers.json` is missing or empty,
it is a no-op.

To connect one: copy `backend/mcp_servers.example.json` to
`backend/mcp_servers.json` (in `.gitignore`), set `"enabled": true` for the
server (or remove the key) and describe it in `MultiServerMCPClient` format
(`streamable_http` with `url`, or `stdio` with `command`/`args`). Tokens come
from `backend/.env` via `${VAR}`. Tools are loaded at backend start (restart
after editing the config) and are named `mcp_<server>_<tool>`.

Security:
- each server connects separately with a timeout: an unavailable server shows up
  in the status but doesn't block startup;
- an external tool's response is data, not instructions: it goes through secret
  masking and the injection check (`injection_guard`), is marked with
  `UNTRUSTED_NOTE`, and its length is limited;
- tools that change something (name contains `send/create/delete/update/…` or
  the `destructiveHint` annotation) run only if the user's own message contains
  an explicit request (`agent_graph._block_unrequested_writes`), so an
  injection from an email or a document can't trigger them;
- server status is shown in the "MCP servers" block in settings.

**User's own servers** (Settings → MCP servers → "Add": name, URL, optional
token; `GET/POST /api/mcp/servers`, `DELETE /api/mcp/servers/{id}`, table
`user_mcp_servers`). They are visible and available only to that user; the
server is test-connected before saving. Security restrictions:
- only remote `https` servers (streamable HTTP); launching local commands
  (stdio) from the UI is impossible;
- SSRF protection: the address must resolve only to public IPs (localhost,
  10/8, 192.168/16, 169.254.169.254 and the like are rejected); the check is
  repeated on every HTTP request, and redirects are not followed;
- the token goes only to the backend and is never returned to the browser;
- at most 5 servers per user and 25 tools per server;
- for local development: `MCP_ALLOW_PRIVATE_URLS=1` allows `http` and private
  addresses (never enable it on a server).

Example of a public server without a key: DeepWiki — `https://mcp.deepwiki.com/mcp`
(questions about GitHub repositories).

---

## The memory system in detail

The memory system lives in `memory/`, a standalone package independent of
FastAPI (it is imported with flat paths, so `backend/app/memory_bridge.py` adds
`memory/` to `sys.path`). The single entry point is `MemorySystem`
(`memory/memory_system.py`), a singleton via `memory_bridge.get_memory_system`:

- `update(text, user_id, source=None)` — remember; `source` (`Source`: type
  email/telegram/calendar/text, id, time, and `end_at` for events) defines the
  source and validity period of the extracted facts;
- `retrieve(query, user_id, top_k)` — find;
- `get_all(user_id)` — everything, including inactive records;
- `delete_all(user_id)` — delete everything (with targeted removal from FAISS).

A fact added manually in the "Memory" panel goes through
`memory_bridge.handmade_memory`; profile facts go through
`replace_profile_facts`.

Every text goes through the **write pipeline**
(`MemoryPipeline.process`, `memory/memory_pipeline.py`):

```
text ──▶ CheapMemoryRouter ──▶ MemoryEnricher.process() ──▶ for each candidate:
         (skip / extract)      │                              MemoryMatcher.find_matches()
                                │  (Router → Unified LLM /            │
                                │   Rule-based fast-path /             ▼
                                │   Legacy pipeline)             MemoryConsolidator.decide()
                                ▼                                     │
                       list of EnrichedMemoryCandidate               ▼
                                                               MemoryUpdater.apply()
                                                               (writes to SQLite)
                                                                       │
                                                                       ▼
                                                       MemoryIndexManager.sync_after_pipeline()
                                                             (incrementally updates FAISS)
                                                                       │
                                                                       ▼
                                                       DreamingEngine (on a change threshold,
                                                       best-effort: a failure never loses a fact)
```

Below, "memory LLM" means `GigaChat-2` via `GigaChatTextModel` or, without a key,
local Qwen3-4B (the same `generate()` interface).

### 1. Cheap router (`CheapMemoryRouter`)

The first filter, **before** any model (`memory/cheap_memory_router.py`). It uses
regexes to drop what is clearly not memory: empty strings, short
acknowledgements ("thanks", "ok"), questions, explicit commands ("show",
"remind", ...). If it fires, the pipeline stops there.

### 2. Extracting memory candidates

`MemoryEnricher.process()` (`memory/enricher.py`) picks one of three routes:

**a) Rule-based router (`MemoryInputRouter`,
`memory/memory_input_router.py`)** — a deterministic layer without an LLM:
- `DROP` — question/command/acknowledgement → nothing is saved;
- `FAST` — unambiguously extracted by regex rules
  (`memory/extractor_contextual.py`), including "goal completed"
  (`COMPLETION_PATTERN` → `MemoryType.STATE`, `event_type=goal_completion`);
- otherwise — escalate to the LLM.

Multi-part messages are split into sentences; if even one can't be handled by
rules or contains a pronoun referring to another sentence, the whole text goes
to the LLM.

**b) `UnifiedMemoryAnalyzer`** (`memory/unified_memory_analyzer.py`) — **one**
memory LLM call: extraction, classification into 8 types and relations
(subject-predicate-object) in a single JSON. The prompt forbids saving
questions/commands/one-off actions and requires distinguishing facts about the
user from facts about others, hypotheticals and jokes. The response is parsed
by a tolerant parser (it can recover complete objects from truncated JSON);
then "command" sentences are removed, overlap with the source text is checked
(`_has_source_evidence`, protection against invented facts), and heuristic
reclassification is applied (procedure→preference, preference→goal).

**c) Legacy pipeline** (`_legacy_process`, fallback) — `MemoryExtractor` +
`MemoryValidator` as separate calls.

**Memory types** (`memory/models.py`, `MemoryType`): `fact`, `preference`,
`episode`, `entity`, `relationship`, `goal`, `state`, `procedure`.

### 3. Enrichment

Runs only when heuristics decide it is needed:

- **Coreference** (`memory/coreference.py`, memory LLM) — pronoun resolution
  (legacy path, when a pronoun is present).
- **Temporal** (`memory/temporal.py`) — time expressions, always.
- **Entity extraction** (GLiNER, `entity_enricher.py`) — if there is an
  `ENTITY`/`RELATIONSHIP` or a relationship marker ("my brother", "a colleague").
- **Grounding** (`CandidateGrounder`) — linking to entities and a position in
  the text.
- **Subject resolution** (`SubjectResolver`) — who the fact is about: the user
  or a third party.
- **Relation extraction/linking** (`RelationExtractor` + `RelationLinker`) —
  for the legacy path.
- **Grammar normalization** — one batch call for all candidates; it fixes only
  cases and agreement (Russian grammar).

The result is an `EnrichedMemoryCandidate`; all enrichment goes into the
record's `metadata`.

### 4. Matching against existing memory

`MemoryMatcher.find_matches()` (`memory/memory_matcher.py`) computes a `score`
from 0 to 1 against all **active** records of the user:

- semantic normalization into (subject, predicate, object)
  (`SemanticMemoryNormalizer`);
- text similarity (`SequenceMatcher` + Jaccard, 65/35);
- same subject+predicate with a different object → score 0.88 (a `SUPERSEDE`
  candidate: "moved from one city to another");
- goal completion (`goal_completion`) against an active `GOAL` → 0.95.

Default cutoff: `min_score=0.50`.

### 5. Consolidation (what to do with a candidate)

`MemoryConsolidator.decide()` (`memory/memory_consolidator.py`):

| Action | When |
|---|---|
| `CREATE` | no matches, or only weak ones |
| `CONFIRM` | same meaning — the record is "refreshed" (`freshness=1.0`, confidence = max) |
| `UPDATE` | score in `[0.70, 0.85)` — the same idea in different words → content is overwritten; also archives a completed goal |
| `SUPERSEDE` | same subject+predicate, different value — the old record becomes `SUPERSEDED` (`valid_until` = now), the new one `ACTIVE` |
| `CONFLICT` | a record with status `CONFLICTING` instead of a silent overwrite |
| `IGNORE` | do nothing |

Default thresholds: `confirm_threshold=0.85`, `update_threshold=0.70`.

### 6. Applying the decision (writing to the DB)

`MemoryUpdater.apply()` (`memory/memory_updater.py`) → `MemoryRepository`
(the `memories` table in `backend/data/memory.db`). The metadata
(`_build_metadata`) includes `subject`, `entities`, `relations`, `temporal`,
`grounding`, `source_text`, and for calendar events `event_start`/`event_end`.
`valid_from` comes from the source's time (the email, the message, the event
start), not from the import time.

The `Memory` model (`memory/models.py`): `id`, `user_id`, `type`, `content`,
`confidence`, `importance`, `freshness`, `status`
(`active`/`superseded`/`expired`/`archived`/`conflicting`), `sources`,
`created_at`/`updated_at`/`valid_from`/`valid_until`, `metadata` (JSON).

### 7. Vector index and retrieval

SQLite is the source of truth; **FAISS is a derived index**
(`memory/faiss_memory_index.py`, `IndexFlatIP`, L2-normalized embeddings).

- `MemoryIndexManager.ensure_ready()` loads the index from disk at startup
  (`backend/data/memory_index/`) and checks it against the database using
  SHA-256 signatures; on mismatch it rebuilds fully.
- After a write: `CREATE` — incremental add, `UPDATE`/`SUPERSEDE` — rebuild,
  delete — targeted `remove(ids)`.
- `backend/inspect_memory_index.py [user_id] [--vectors]` — a dev utility that
  shows what's in the index and finds mismatches with `memory.db`.

Retrieval (`MemorySystem.retrieve` → `MemoryRetrieverV2`):

1. **FAISS** — the query is embedded, top `candidate_k`, filtered by `user_id`
   and `status == ACTIVE`.
2. **Reranking and post-filters**: a cross-encoder reranks the candidates; a
   time marker in the query becomes a hard filter; goal queries boost `GOAL`;
   entity queries ("tell me about ...") expand the relation graph from the best
   seed (`_connected_memories`); otherwise a thresholded semantic fallback
   (sigmoid of the logit, cutoff below `0.01` and at a sharp score drop).
   `top_k` is a maximum, not a promise.

### 8. How memory gets into the agent's answer

There is no automatic injection of memory into the prompt: the agent calls
`search_memory` / `list_memories` itself (sometimes several times with
different phrasings). Results are formatted by `MemoryContextFormatter` and
reach the model as a function result.

### 9. Dreaming (batch consolidation)

`memory/dreaming_engine.py::DreamingEngine` runs automatically when enough
changes have accumulated, or manually via the "Reflect" button
(`POST /api/memories/dream`). In one LLM pass it merges obvious duplicates into
a generalized phrasing and archives outdated/cancelled/completed records.
Nothing is physically deleted, only `status=ARCHIVED`.

---

## Data storage

Everything lives in `backend/data/` (created automatically, excluded from git):

| File/folder | Contents |
|---|---|
| `app.db` | the app's SQLite (`backend/app/db.py`): `users` (including integration credentials), `conversations`, `chat_messages`, `tasks`, `expenses` (manual spending/income and transactions from bank emails), `meals`, `weights`, `raw_emails`, `raw_calendar_events`, `raw_messages` (imports with security flags), `imported_calendar_events`, `sent_email_drafts`, `calendar_action_drafts` |
| `memory.db` | SQLite: the `memories` table — all long-term memory |
| `memory_index/` | `memories.faiss` + `memory_ids.json` (id→vector + SHA-256 signatures) |

Authentication: JWT (`backend/app/auth.py`), registration with
username/password (bcrypt) or Google Sign-In. `user_id` is taken only from a
verified token.

---

## Backend API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | liveness check |
| `POST` | `/api/auth/register`, `/api/auth/login` | `{username, password, invite_code?}` → `{access_token}` (`invite_code` is required when `REGISTRATION_CODE` is set) |
| `POST` | `/api/auth/google` | `{id_token}` → `{access_token}` |
| `POST`/`GET` | `/api/conversations` | create a chat / list chats |
| `DELETE` | `/api/conversations/{id}` | delete a chat |
| `POST` | `/api/chat` | `{conversation_id, message, enable_thinking?}` → SSE (`token`/`tool_call`/`done`) + background memory write |
| `GET` | `/api/history?conversation_id=...` | chat history |
| `POST` | `/api/transcribe` | audio → text (Whisper on cloud.ru) |
| `POST` | `/api/documents/extract` | PDF/DOCX/text → safe block for the chat (no hidden text, masked, with dates) |
| `GET` | `/api/proactive` | proactive suggestions for the start screen |
| `POST` | `/api/import` | import `email`/`telegram`/`calendar` (`file`, `source?` — otherwise auto-detected, `replace?`, `sync_calendar?`) |
| `GET` | `/api/memories` | all memory records (including inactive) |
| `GET` | `/api/memories/export` | memory export (masked) |
| `GET` | `/api/memories/search?q=...` | semantic memory search |
| `POST` | `/api/memories` | `{content}` — add a fact manually |
| `POST` | `/api/memories/dream` | batch consolidation |
| `DELETE` | `/api/memories/{id}` | delete a memory record |
| `DELETE` | `/api/account/data` | delete all account data (memory, imports, records) |
| `GET` | `/api/sandbox` | recent tasks/spending/meals/weight |
| `GET` | `/api/finance/transactions`, `/api/finance/summary` | data for the "Finance" panel |
| `GET`/`PUT` | `/api/settings` | user settings (Intervals.icu key, integration statuses) |
| `GET`/`POST` | `/api/mcp/servers` | MCP servers: system ones (read-only) and the user's own; add `{name, url, token?}` |
| `DELETE` | `/api/mcp/servers/{id}` | delete the user's own MCP server |
| `GET` | `/api/google-calendar/connect`, `/callback`; `POST` `/disconnect` | Google Calendar OAuth |
| `GET` | `/api/gmail/connect`, `/callback`; `POST` `/disconnect` | Gmail OAuth |
| `POST` | `/api/gmail/send` | send a draft — only from the UI button |
| `POST` | `/api/calendar/confirm` | perform an event create/update/delete — only from the UI button |

All endpoints except health/auth/OAuth callbacks require
`Authorization: Bearer <token>`; someone else's `conversation_id` → 404.

---

## Frontend

React 19 + Vite, no state manager. The chat start screen (`App.jsx`) shows
proactive suggestions from `/api/proactive`; a click sends a ready request to
the chat. The UI is in Russian and English (`lib/i18n.jsx`), with
light/dark/system themes (`lib/settings.js`).

- `Login.jsx` — sign in/registration, Google Sign-In;
- `Chat.jsx` — messages with markdown, tool call indicators, embedded maps and
  routes (`MapboxMap.jsx`), draft cards for emails ("Send" button) and calendar
  events ("Confirm" button), "thinking" phrases while waiting;
- `Composer.jsx` — auto-growing input, voice input (hold and speak), document
  and dataset attachments (`.json`/`.jsonl` go to import with source
  auto-detection on send), a "Thinking" toggle;
- `MemoryPanel.jsx` — what the assistant remembered (with sources and periods),
  manual add, semantic search, delete, "Reflect";
- `FinancePanel.jsx` — transactions and a summary by month/category;
- `SettingsPanel.jsx` — theme, language, connecting Intervals.icu / Google
  Calendar / Gmail, MCP servers (`McpSettings.jsx`), deleting all account data;
- `MemoryProgress.jsx` — progress ring for background memory filling after an import;
- `ChatList.jsx`, `Menu.jsx`, `OrbCanvas.jsx` (the Three.js status "orb").

`lib/api.js` is a thin client with manual SSE parsing; all requests go to a
relative `/api/...` through the Vite proxy. `lib/auth.js` stores the JWT in
`localStorage`.

---

## Self-check on reference questions

`competition_task/eval_reference.py` runs the control questions from
`competition_task/train_dataset/reference_answers.md` through the real agent: it
loads the dataset into a separate eval user, freezes "now" at the dataset's
reference moment (`ASSISTANT_NOW`), asks every question **twice** with different
wording in new chats and checks that both answers contain the expected values.

```bash
python competition_task/eval_reference.py                  # load + ask everything
python competition_task/eval_reference.py --no-load q11 q14
python competition_task/eval_reference.py --with-memory    # also fill fact memory (slow)
python competition_task/eval_reference.py --security       # plant attacks in the data
python competition_task/eval_reference.py --data other_dir --now 2026-10-01T10:00:00+03:00
```

Requires `backend/.env` with GigaChat keys: every question is a real LLM call.
The open reference data is only an example; the final evaluation used a
different, closed dataset, so solutions must generalize rather than be tuned to
it.

`competition_task/test_memory_emails.py` is a separate check of fact extraction
from emails.

**Dataset series.** `competition_task/datasets/` contains a synthetic persona
generator (`gen.py`, `personas.py`) and an end-to-end run through the live API:
full account wipe → upload → every question twice with different wording in new
chats → security checks → grading by the competition criteria:

```bash
bash competition_task/datasets/run_series.sh <tag> maria artem olga   # one after another + summary
python competition_task/datasets/run_dataset.py maria --user eval_maria
python competition_task/datasets/grade_dataset.py maria             # → maria/grade_report.md
```

History of iterations and scores: `competition_task/iterations/REPORT.md`.

---

## Repository layout

```
backend/
  app/
    main.py               — FastAPI routes, CORS, startup, import
    agent.py              — one dialogue turn (stream, masking, citations, saving)
    agent_graph.py        — LangGraph graph (agent ⇄ masked tools), LLM choice
    chat_gigachat.py      — LangChain adapter over GigaChat
    chat_model.py         — LangChain adapter over local Qwen3-4B (fallback)
    prompts.py            — system prompt assembly
    tools.py              — action tools, integrations, memory tools
    data_tools.py         — tools over imported data
    personal_data.py      — import normalization and storage, time zone, "now"
    personal_analytics.py — deterministic analytics (calendar, email, finance, contacts)
    profile_builder.py    — digital profile → memory facts
    proactive.py          — proactive suggestion detectors
    bank_parser.py        — bank notifications → transactions
    injection_guard.py    — injection and phishing detector
    masking.py            — secret masking (data, API, logs)
    documents.py          — PDF/DOCX attachment parsing, hidden text removal
    google_oauth.py, google_calendar.py, gmail.py — Google integrations
    transcription.py      — voice → text (cloud.ru Whisper)
    memory_bridge.py      — backend ↔ memory/ bridge (ingest, profile, deletion)
    auth.py, conversations.py, user_settings.py, db.py, schemas.py
    mcp_tools.py          — optional MCP servers
  inspect_memory_index.py — dev utility for the FAISS index
  .env.example            — all environment variables with comments
  mcp_servers.example.json
  requirements.txt

frontend/src/
  App.jsx, main.jsx, styles.css
  components/  Chat, Composer, ChatList, Login, Menu, OrbCanvas, MapboxMap,
               MemoryPanel, MemoryProgress, FinancePanel, SettingsPanel, McpSettings
  lib/         api.js, auth.js, i18n.jsx, markdown.js, mapbox.js, settings.js

memory/                     — long-term memory package
  memory_system.py          — facade (update/retrieve/get_all/delete_all)
  model_registry.py         — model singletons, cloud/local choice
  gigachat_client.py, gigachat_text_model.py, gigachat_grammar_normalizer.py,
    gigachat_embedder.py    — GigaChat backends
  cloud_ru_embedder.py, cloud_ru_reranker.py — cloud.ru backends
  qwen_model.py, memory_embedder.py, memory_reranker.py,
    memory_grammar_normalizer.py, entity_enricher.py — local models
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

competition_task/           — case description, starter data (train_dataset/),
                              datasets and graders (datasets/, test_data/), reports (iterations/)

docs/
  ARCHITECTURE.md           — this document

scripts/
  dev.sh                    — debug run of both at once
  backend.sh / frontend.sh  — run separately
  stop.sh                   — stop (by ports 8001/5173)
  deploy.sh                 — update a server deployment
```

---

## Known limitations

- GLiNER is always local (CPU) for now, since it has no API equivalent; that's
  why `requirements.txt` still pulls torch/transformers.
- `GigaChat-2` sometimes ignores the `confidence` scale (always 0.0) and the
  JSON array format. The format is repaired automatically, but quality is worth
  double-checking; if needed, use `GigaChat-2-Pro/Max` via `GIGACHAT_LITE_MODEL`.
- GigaChat embeddings are unavailable on the `GIGACHAT_API_PERS` plan (402), so
  the embedder and reranker run on cloud.ru.
- Background fact extraction for a large import takes time: precise data
  questions work immediately, while the memory profile fills in gradually.
- The injection detectors and the bank parser are heuristic: they generalize
  well to typical attacks and phrasings but don't guarantee 100% coverage.
- Local mode (no keys) needs a GPU with spare VRAM; on 8 GB, Qwen3-4B + embedder
  + reranker take almost all of it (which is why GLiNER and the normalizer run
  on CPU by default).
- There is no automatic recalculation of `importance`/`freshness`/`expired`
  over time: the fields exist in the `Memory` model, but no background process
  updates them.
- PDFs are read only from the text layer: scans (image-only PDFs) aren't
  recognized without OCR; XLSX isn't supported yet.
- "Now" is the real time in the user's time zone. For reproducible checks on a
  dataset with a fixed reference date it can be "frozen" with `ASSISTANT_NOW`.
- While the Google OAuth app is in testing mode, Calendar and Gmail connect only
  for addresses on the Test users list, and access expires after 7 days.
