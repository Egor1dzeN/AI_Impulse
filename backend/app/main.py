from __future__ import annotations

import json
import logging
import os

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

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse

from . import agent, auth, conversations, db, gmail, google_calendar, mcp_tools, memory_bridge, tools, transcription, user_settings
from .schemas import (
    AddMemoryRequest,
    ChatMessageOut,
    ChatRequest,
    ConversationOut,
    GoogleLoginRequest,
    LoginRequest,
    MemoryOut,
    MemorySearchOut,
    RegisterRequest,
    SandboxOut,
    SendEmailRequest,
    SettingsOut,
    TokenOut,
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
    await mcp_tools.refresh_mcp_tools()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/auth/register", response_model=TokenOut)
def register(req: RegisterRequest) -> dict:
    _, token = auth.register_user(req.username, req.password)
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
            "content": row["content"],
            "action": json.loads(row["action_json"]) if row["action_json"] else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@app.get("/api/memories", response_model=list[MemoryOut])
def memories(user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    return memory_bridge.list_memories(user_id)


@app.get("/api/memories/search", response_model=list[MemorySearchOut])
def search_memories(q: str, user_id: str = Depends(auth.get_current_user_id)) -> list[dict]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="empty query")
    return memory_bridge.search_memories(q, user_id)


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


@app.get("/api/sandbox", response_model=SandboxOut)
def sandbox(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    return tools.get_sandbox_snapshot(user_id)


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

    result = gmail.send_message(refresh_token=refresh_token, to=req.to, subject=req.subject, body=req.body)
    gmail.mark_draft_sent(req.draft_id, user_id)
    return {"already_sent": False, **result}
