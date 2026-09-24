from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict

# Debug-heavy logging (tool calls, memory retrieval/writes) is set up here,
# before any `app.*` module is imported below, so every `logging.getLogger`
# call those modules make already resolves to a configured root logger.
# uvicorn's own dictConfig (which touches only its "uvicorn.*" loggers) runs
# before this module is imported, so it never overwrites this.
#
# Root level stays at INFO (not DEBUG) on purpose: DEBUG would also unleash
# every third-party library on the import path (httpx, urllib3, faiss,
# transformers, ...) and bury the useful trace in noise. All the tracing
# added for debugging (tool calls, memory retrieve/write) logs at INFO for
# that reason. Override with LOG_LEVEL=DEBUG (or WARNING to quiet down)
# when needed.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from .masking import install_log_masking, mask_obj, mask_sensitive  # noqa: E402

install_log_masking()

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from . import agent, auth, conversations, db, documents, gmail, google_calendar, mcp_tools, memory_bridge, personal_data, proactive, profile_builder, tools, transcription, user_settings
from .schemas import (
    AddMemoryRequest,
    CalendarActionRequest,
    ChatMessageOut,
    ChatRequest,
    ConversationOut,
    FinanceSummaryOut,
    GoogleLoginRequest,
    LoginRequest,
    MemoryOut,
    MemorySearchOut,
    RegisterRequest,
    SandboxOut,
    SendEmailRequest,
    SettingsOut,
    TokenOut,
    TransactionOut,
    UpdateSettingsRequest,
)

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

logger = logging.getLogger(__name__)

app = FastAPI(title="Impulse Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    db.init_db()
    install_log_masking()
    await mcp_tools.refresh_mcp_tools()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/auth/register", response_model=TokenOut)
def register(req: RegisterRequest) -> dict:
    _, token = auth.register_user(req.username, req.password, req.invite_code)
    return {"access_token": token}


@app.post("/api/auth/login", response_model=TokenOut)
def login(req: LoginRequest) -> dict:
    _, token = auth.login_user(req.username, req.password)
    return {"access_token": token}


@app.post("/api/auth/google", response_model=TokenOut)
def google_login(req: GoogleLoginRequest) -> dict:
    claims = auth.verify_google_id_token(req.id_token)
    _, token = auth.login_or_register_google_user(
        google_id=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name"),
        invite_code=req.invite_code,
    )
    return {"access_token": token}


@app.post("/api/conversations", response_model=ConversationOut)
def create_conversation(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return conversations.create_conversation(user_id)


@app.get("/api/conversations", response_model=list[ConversationOut])
def list_conversations(user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    return conversations.list_conversations(user_id)


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user_id: str = Depends(auth.get_current_user_id)) -> dict:
    conversations.delete_conversation(conversation_id, user_id)
    return {"status": "ok"}


@app.post("/api/chat")
def chat(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        user_id: str = Depends(auth.get_current_user_id),
) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")

    conversations.assert_owned(req.conversation_id, user_id)

    def event_stream():
        for event in agent.handle_chat_turn_stream(
                user_id, req.conversation_id, req.message, enable_thinking=req.enable_thinking
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    response = StreamingResponse(event_stream(), media_type="text/event-stream")

    # Оригинальное сообщение (req.message) отправляем в memory_bridge
    background_tasks.add_task(agent.extract_memory_background, user_id, req.message)
    response.background = background_tasks
    return response


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...), user_id: str = Depends(auth.get_current_user_id)
) -> dict:
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio file")
    transcription.save_debug_copy(audio_bytes)
    text = transcription.transcribe_audio(audio_bytes)
    return {"text": text}


@app.get("/api/proactive")
def proactive_suggestions(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return mask_obj(proactive.scan(user_id, limit=6))


MAX_DOCUMENT_BYTES = 15 * 1024 * 1024


@app.post("/api/documents/extract")
async def extract_document(
    file: UploadFile = File(...), user_id: str = Depends(auth.get_current_user_id)
) -> dict:
    """Chat attachments (PDF/DOCX/text): extract visible text server-side,
    strip hidden text, scan for injections, mask secrets and pull out dates,
    returning a ready block that is explicitly marked as data for the model."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    try:
        doc = documents.extract_document(file.filename or "document", data)
    except Exception:  # noqa: BLE001 — a broken/encrypted file is a user error, not a server crash
        logger.exception("document extraction failed: user_id=%s filename=%s", user_id, file.filename)
        raise HTTPException(status_code=422, detail="could not read document")
    return {
        "filename": doc["filename"],
        "block": documents.format_for_chat(doc),
        "security": doc["security"],
        "hidden_text_removed": doc["hidden_text_removed"],
        "dates": doc["dates"],
    }


@app.get("/api/history", response_model=list[ChatMessageOut])
def history(conversation_id: str, user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    conversations.assert_owned(conversation_id, user_id)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, action_json, created_at FROM chat_messages "
            "WHERE conversation_id = ? ORDER BY rowid ASC",
            (conversation_id,),
        ).fetchall()

    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": mask_sensitive(row["content"]),
            "action": mask_obj(json.loads(row["action_json"])) if row["action_json"] else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@app.get("/api/memories", response_model=list[MemoryOut])
def memories(user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    return mask_obj(memory_bridge.list_memories(user_id))


@app.get("/api/memories/export")
def export_memories(user_id: str = Depends(auth.get_current_user_id)) -> Response:
    """The digital profile as a downloadable JSON file: every fact with its
    type, source(s) and validity period, masked like every other output."""
    facts = mask_obj(memory_bridge.list_memories(user_id))
    payload = {
        "exported_at": db.now_iso(),
        "user": mask_obj(personal_data.user_identity(user_id)),
        "facts_count": len(facts),
        "facts": [
            {
                "content": f["content"],
                "type": f["type"],
                "confidence": f["confidence"],
                "sources": [
                    {"type": s["type"], "title": s["title"], "date": s["created_at"]} for s in f["sources"]
                ],
                "valid_from": f["valid_from"],
                "valid_until": f["valid_until"],
            }
            for f in facts
        ],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="profile_export.json"'},
    )


@app.get("/api/memories/search", response_model=list[MemorySearchOut])
def search_memories(q: str, user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="empty query")
    return mask_obj(memory_bridge.search_memories(q, user_id))


@app.post("/api/memories")
def add_memory(req: AddMemoryRequest, user_id: str = Depends(auth.get_current_user_id)) -> dict:
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="empty content")
    memory_bridge.handmade_memory(req.content, user_id)
    return {"status": "ok"}


@app.post("/api/memories/dream")
def dream_memories(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return memory_bridge.force_dream(user_id)


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str, user_id: str = Depends(auth.get_current_user_id)) -> dict:
    try:
        memory_bridge.delete_memory(memory_id, user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"status": "ok"}


@app.delete("/api/account/data")
def delete_account_data(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    """Account-wide "forget me" reset: every remembered fact (memory_bridge's
    fact-memory system) plus every task/expense/meal/weight this app itself
    logged for the user. Deliberately irreversible and total, so the
    frontend gates this behind its own confirmation dialog before ever
    calling it — there is no undo here, unlike a single memory's delete."""
    _cancel_background_imports(user_id)
    memories_deleted = memory_bridge.delete_all_memories(user_id)
    db.delete_all_user_data(user_id)
    return {"memories_deleted": memories_deleted}


IMPORT_SOURCES = {"email", "telegram", "calendar"}


def _parse_import_file(raw: str) -> tuple[list[dict], int]:
    """Accept both a JSON array file and a .jsonl (one JSON object per
    non-empty line) file — the competition's own mock data ships as
    .jsonl, but a jury-supplied replacement dataset's exact format isn't
    guaranteed, and this costs nothing to support. Returns (records,
    skipped_lines): one broken .jsonl line (a stray character left by an
    editor, a truncated record) is skipped instead of rejecting the whole
    file; JSONDecodeError only when nothing in it parses."""
    raw = raw.strip().lstrip("﻿")
    if not raw:
        return [], 0

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return parsed, 0
    if isinstance(parsed, dict):
        return [parsed], 0

    records: list[dict] = []
    skipped = 0
    first_error: json.JSONDecodeError | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            skipped += 1
            first_error = first_error or exc
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            skipped += 1
    if not records and first_error is not None:
        raise first_error
    return records, skipped


def _detect_import_source(records: list[dict], filename: str = "") -> str | None:
    """Chat attachments carry no explicit source, so infer it from the
    record shape (Gmail-like email / Google Calendar event / Telegram
    message or Desktop export), falling back to hints in the file name.
    None means "not a dataset" — the caller treats the file as a document."""
    votes = {"email": 0, "calendar": 0, "telegram": 0}
    for record in records[:50]:
        if not isinstance(record, dict):
            continue
        keys = set(record)
        if str(record.get("kind", "")).startswith("calendar#") or keys & {"start", "dtstart", "end", "dtend"}:
            votes["calendar"] += 1
        elif keys & {"subject", "thread_id", "threadId", "labelIds"} or {"from", "body"} <= keys:
            votes["email"] += 1
        elif keys & {"msg_id", "chats", "chat"} or isinstance(record.get("messages"), list) or {"sender", "text"} <= keys:
            votes["telegram"] += 1
    best = max(votes, key=votes.get)
    if votes[best]:
        return best

    name = filename.lower()
    for source, hints in (("email", ("mail", "email")), ("calendar", ("calendar", "event")), ("telegram", ("telegram", "tg_", "chat"))):
        if any(h in name for h in hints):
            return source
    return None


def import_calendar_to_google(user_id: str, records: list[dict]) -> dict:
    """The "или на сервис" half of the Data Import panel for source=calendar:
    besides being remembered as facts (import_records() above already did
    that), also create each record as a REAL event on the user's connected
    Google Calendar. imported_google_event_id() makes re-running the same
    import (e.g. the jury re-uploading their dataset) a no-op per event
    instead of creating duplicates — see the competition brief's "повторный
    запуск... не должен создавать дубликаты" requirement."""
    refresh_token = user_settings.get_google_calendar_refresh_token(user_id)
    if not refresh_token:
        return {"synced": 0, "skipped": 0, "failed": 0, "error": "Google Calendar is not connected"}

    synced = skipped = failed = 0
    for record in records:
        external_id = str(record.get("id") or "")
        if not external_id:
            failed += 1
            continue
        if google_calendar.imported_google_event_id(user_id, external_id) is not None:
            skipped += 1
            continue
        try:
            result = google_calendar.create_event_raw(
                refresh_token=refresh_token,
                summary=record.get("summary") or "(без названия)",
                start=record.get("start") or {},
                end=record.get("end") or {},
                description=record.get("description"),
            )
            google_calendar.mark_imported(user_id, external_id, result["id"])
            synced += 1
        except Exception:
            logger.exception("import_calendar_to_google: user_id=%s external_id=%s failed", user_id, external_id)
            failed += 1

    return {"synced": synced, "skipped": skipped, "failed": failed}


# Per-user import generation: a newer import (or a data reset) bumps it,
# and a still-running background memory extraction from an older import
# stops at its next record — so a replaced dataset can't leak old facts in.
_import_generation: dict[str, int] = {}
_import_lock = threading.Lock()


def _cancel_background_imports(user_id: str) -> int:
    with _import_lock:
        _import_generation[user_id] = _import_generation.get(user_id, 0) + 1
        _import_progress.pop(user_id, None)
        return _import_generation[user_id]


# Per-user progress of the background half of imports (profile build +
# per-record fact extraction), polled by the chat's progress ring. One
# batch accumulates while work is outstanding (several files attached at
# once show one ring); it restarts from zero once everything has finished.
_import_progress: dict[str, dict[str, int]] = {}


def _progress_add(user_id: str, generation: int, records: int) -> None:
    with _import_lock:
        p = _import_progress.get(user_id)
        finished = p is not None and p["done"] >= p["total"] and p["profile_pending"] == 0
        if p is None or p["generation"] != generation or finished:
            p = _import_progress[user_id] = {"generation": generation, "total": 0, "done": 0, "profile_pending": 0}
        p["total"] += records
        p["profile_pending"] += 1


def _progress_update(user_id: str, generation: int, key: str, delta: int) -> None:
    """A cancelled (older-generation) import must not touch the new batch."""
    with _import_lock:
        p = _import_progress.get(user_id)
        if p is not None and p["generation"] == generation:
            p[key] += delta


_profile_requests: dict[str, int] = {}
_profile_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _request_profile_build(user_id: str) -> int:
    with _import_lock:
        _profile_requests[user_id] = _profile_requests.get(user_id, 0) + 1
        return _profile_requests[user_id]


def _process_import_in_background(
    user_id: str, source: str, records: list[dict], generation: int, profile_request: int
) -> None:
    """1) Rebuild the digital profile from all imported raw data (only the
    newest request runs — several quick imports collapse into one build);
    2) run per-record fact extraction into long-term memory. Both stop as
    soon as a newer replace-import or a data reset bumps the generation."""
    def current() -> bool:
        return _import_generation.get(user_id) == generation

    with _profile_locks[user_id]:
        if current() and _profile_requests.get(user_id) == profile_request:
            try:
                result = profile_builder.build_profile(
                    user_id, should_continue=lambda: current() and _profile_requests.get(user_id) == profile_request
                )
                logger.info("profile build: user_id=%s result=%s", user_id, result)
            except Exception:  # noqa: BLE001 — the per-record extraction below must still run
                logger.exception("profile build failed: user_id=%s", user_id)
    _progress_update(user_id, generation, "profile_pending", -1)

    done = 0
    for record in records:
        if not current():
            logger.info("import memory extraction cancelled: user_id=%s source=%s after %d record(s)", user_id, source, done)
            return
        memory_bridge.import_records(user_id, source, [record])
        _progress_update(user_id, generation, "done", 1)
        done += 1
    logger.info("import memory extraction done: user_id=%s source=%s records=%d", user_id, source, done)


@app.post("/api/import")
async def import_data(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source: str | None = Form(None),
    replace: bool = Form(False),
    sync_calendar: bool = Form(False),
    user_id: str = Depends(auth.get_current_user_id),
) -> dict:
    """The Data Import panel's one endpoint: takes an uploaded email/
    Telegram/calendar export (.jsonl or a JSON array) and
    1) synchronously stores every record (masked, security-scanned) in the
       raw data store, parsing bank notifications into transactions — exact
       questions can be answered right after this returns;
    2) runs the records that carry profile knowledge through fact
       extraction into long-term memory in the background.
    `replace` wipes the account's data first and cancels any extraction
    still running from a previous import, so old facts never mix with the
    new dataset. For source=calendar, `sync_calendar` additionally creates
    each event on the connected Google Calendar. Without `source` (a
    dataset file attached in the chat) the source is detected from the
    records; 422 means the file doesn't look like a dataset at all."""
    if source is not None and source not in IMPORT_SOURCES:
        raise HTTPException(status_code=400, detail=f"source must be one of: {', '.join(sorted(IMPORT_SOURCES))}")

    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        records, skipped_lines = _parse_import_file(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse file as JSON or JSONL: {exc}")

    if source is None:
        source = _detect_import_source(records, file.filename or "")
        if source is None:
            raise HTTPException(status_code=422, detail="could not detect data source (email/telegram/calendar)")

    if replace:
        generation = _cancel_background_imports(user_id)
        memory_bridge.delete_all_memories(user_id)
        db.delete_all_user_data(user_id)
    else:
        generation = _import_generation.setdefault(user_id, 0)

    stored = personal_data.store_records(user_id, source, records)
    memory_records = stored.pop("memory_records")
    _progress_add(user_id, generation, len(memory_records))
    background_tasks.add_task(
        _process_import_in_background, user_id, source, memory_records, generation, _request_profile_build(user_id)
    )

    result: dict = {
        "source": source,
        "imported": stored["stored"],
        "failed": 0,
        "errors": [],
        "transactions": stored.get("transactions", 0),
        "flagged": stored.get("flagged", []),
        "memory_extraction_queued": len(memory_records),
        "skipped_lines": skipped_lines,
    }
    if source == "calendar" and sync_calendar:
        result["calendar_sync"] = import_calendar_to_google(user_id, records)
    return result


@app.get("/api/import/progress")
def import_progress(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    """How far the background memory filling has got. Percent counts
    extracted records; it holds at 99 while a profile build is still
    running, so 100 means the memory is fully ready for questions."""
    with _import_lock:
        p = dict(_import_progress.get(user_id) or {"total": 0, "done": 0, "profile_pending": 0})
    total, done, profile_pending = p["total"], min(p["done"], p["total"]), max(p["profile_pending"], 0)
    active = profile_pending > 0 or done < total
    percent = round(100 * done / total) if total else (0 if active else 100)
    if active:
        percent = min(percent, 99)
    return {
        "active": active,
        "percent": percent,
        "done": done,
        "total": total,
        "phase": "profile" if profile_pending > 0 else ("facts" if active else "idle"),
    }


@app.get("/api/sandbox", response_model=SandboxOut)
def sandbox(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return tools.get_sandbox_snapshot(user_id)


@app.get("/api/finance/transactions", response_model=list[TransactionOut])
def finance_transactions(user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    return tools.list_transactions_impl(user_id, limit=500)["transactions"]


@app.get("/api/finance/summary", response_model=FinanceSummaryOut)
def finance_summary(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return tools.get_finance_summary_impl(user_id)


@app.get("/api/settings", response_model=SettingsOut)
def get_settings(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return user_settings.get_settings(user_id)


@app.put("/api/settings", response_model=SettingsOut)
def update_settings(req: UpdateSettingsRequest, user_id: str = Depends(auth.get_current_user_id)) -> dict:
    if req.intervals_icu_api_key is not None:
        key = req.intervals_icu_api_key.strip()
        try:
            if key:
                # Validate against Intervals.icu before saving — otherwise a
                # typo'd key would only surface later, as a cryptic mid-chat
                # tool failure, instead of an immediate error right here.
                if not tools.intervals_icu_key_is_valid(key):
                    raise HTTPException(status_code=400, detail="invalid Intervals.icu API key")
                user_settings.set_intervals_icu_key(user_id, key)
            else:
                user_settings.set_intervals_icu_key(user_id, None)
        except ValueError as exc:
            # Stale JWT for a user_id that no longer exists (e.g. after a
            # DB reset) — surface it instead of silently no-oping the save.
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    return user_settings.get_settings(user_id)


@app.get("/api/google-calendar/connect")
def google_calendar_connect(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    if not google_calendar.google_calendar_is_configured():
        raise HTTPException(status_code=503, detail="Google Calendar is not configured on this server")
    return {"url": google_calendar.build_authorize_url(google_calendar.create_state(user_id))}


@app.get("/api/google-calendar/callback")
def google_calendar_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    # Google redirects the bare browser here (no Authorization header from
    # our frontend), so auth.get_current_user_id can't be used — the user_id
    # travels inside `state` instead (see google_calendar.create_state).
    if error or not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}/?google_calendar=error")

    user_id = google_calendar.decode_state(state)
    try:
        refresh_token = google_calendar.exchange_code_for_refresh_token(code)
        user_settings.set_google_calendar_refresh_token(user_id, refresh_token)
    except (RuntimeError, ValueError):
        logger.exception("Google Calendar connect failed for user_id=%s", user_id)
        return RedirectResponse(f"{FRONTEND_URL}/?google_calendar=error")

    return RedirectResponse(f"{FRONTEND_URL}/?google_calendar=connected")


@app.post("/api/google-calendar/disconnect")
def google_calendar_disconnect(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    refresh_token = user_settings.get_google_calendar_refresh_token(user_id)
    if refresh_token:
        google_calendar.revoke(refresh_token)
        user_settings.set_google_calendar_refresh_token(user_id, None)
    return user_settings.get_settings(user_id)


@app.get("/api/gmail/connect")
def gmail_connect(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    if not gmail.gmail_is_configured():
        raise HTTPException(status_code=503, detail="Gmail is not configured on this server")
    return {"url": gmail.build_authorize_url(gmail.create_state(user_id))}


@app.get("/api/gmail/callback")
def gmail_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}/?gmail=error")

    user_id = gmail.decode_state(state)
    try:
        refresh_token = gmail.exchange_code_for_refresh_token(code)
        user_settings.set_gmail_refresh_token(user_id, refresh_token)
    except (RuntimeError, ValueError):
        logger.exception("Gmail connect failed for user_id=%s", user_id)
        return RedirectResponse(f"{FRONTEND_URL}/?gmail=error")

    return RedirectResponse(f"{FRONTEND_URL}/?gmail=connected")


@app.post("/api/gmail/disconnect")
def gmail_disconnect(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    refresh_token = user_settings.get_gmail_refresh_token(user_id)
    if refresh_token:
        gmail.revoke(refresh_token)
        user_settings.set_gmail_refresh_token(user_id, None)
    return user_settings.get_settings(user_id)


@app.post("/api/gmail/send")
def gmail_send(req: SendEmailRequest, user_id: str = Depends(auth.get_current_user_id)) -> dict:
    """The ONLY code path that actually calls gmail.send_message — reachable
    exclusively from the frontend's own "Send" button on a draft_email
    artifact card (see tools.py's TOPIC 1b comment), never from the agent's
    tool-calling loop. draft_already_sent() makes a repeat click (e.g. after
    a page refresh re-renders an old draft card) a safe no-op instead of a
    duplicate email."""
    refresh_token = user_settings.get_gmail_refresh_token(user_id)
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Gmail is not connected")

    if gmail.draft_already_sent(req.draft_id):
        return {"already_sent": True}

    try:
        result = gmail.send_message(refresh_token=refresh_token, to=req.to, subject=req.subject, body=req.body)
    except RuntimeError as exc:
        # _get_access_token's revoked/expired-refresh-token message — was an
        # unhandled 500 before this, which the frontend showed as a bare
        # "500 Internal Server Error" with no way to tell what actually
        # went wrong (see google_oauth.get_access_token's revoked_message).
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    gmail.mark_draft_sent(req.draft_id, user_id)
    return {"already_sent": False, **result}


@app.post("/api/calendar/confirm")
def calendar_confirm(req: CalendarActionRequest, user_id: str = Depends(auth.get_current_user_id)) -> dict:
    """The ONLY code path that actually calls create_event/update_event/
    delete_event — reachable exclusively from the frontend's own "Confirm"
    button on a create/update/delete calendar artifact card (see tools.py's
    TOPIC 1 comment), never from the agent's tool-calling loop.
    action_already_done() makes a repeat click a safe no-op, same idea as
    gmail_send's draft_already_sent() above."""
    refresh_token = user_settings.get_google_calendar_refresh_token(user_id)
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Google Calendar is not connected")

    if google_calendar.action_already_done(req.draft_id):
        return {"already_done": True}

    # Times on the card are the user's local time — the zone derived from
    # their imported data, not the Google account's own setting.
    tz_name = personal_data.user_timezone(user_id).key

    try:
        if req.kind == "create":
            if not req.title or not req.start_time:
                raise HTTPException(status_code=400, detail="title and start_time are required")
            # Same meeting confirmed again from another chat/session — keep
            # exactly one object on the calendar instead of a second copy.
            existing = google_calendar.find_duplicate_event(refresh_token, req.title, req.start_time, tz_name)
            if existing:
                google_calendar.mark_action_done(req.draft_id, user_id)
                return {"already_done": False, "duplicate": True, **existing}
            result = google_calendar.create_event(
                refresh_token=refresh_token,
                summary=req.title,
                start_iso=req.start_time,
                duration_minutes=req.duration_minutes or 30,
                description=req.notes,
                attendees=req.attendees,
                tz_name=tz_name,
            )
        elif req.kind == "update":
            if not req.event_id:
                raise HTTPException(status_code=400, detail="event_id is required")
            result = google_calendar.update_event(
                refresh_token=refresh_token,
                event_id=req.event_id,
                summary=req.title,
                start_iso=req.start_time,
                duration_minutes=req.duration_minutes,
                description=req.notes,
                tz_name=tz_name,
            )
        else:
            if not req.event_id:
                raise HTTPException(status_code=400, detail="event_id is required")
            google_calendar.delete_event(refresh_token=refresh_token, event_id=req.event_id)
            result = {"id": req.event_id}
    except RuntimeError as exc:
        # Same revoked/expired-token case as gmail_send above — was an
        # unhandled 500 before this.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    google_calendar.mark_action_done(req.draft_id, user_id)
    return {"already_done": False, **result}
