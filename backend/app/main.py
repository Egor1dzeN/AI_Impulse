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

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import agent, auth, conversations, db, mcp_tools, memory_bridge, tools, user_settings
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
    SettingsOut,
    TokenOut,
    UpdateSettingsRequest,
)

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


@app.post("/api/mcp/reload")
async def reload_mcp_servers(user_id: str = Depends(auth.get_current_user_id)) -> dict:
    """Переопрашивает все серверы из mcp_servers.json и обновляет
    кэшированный список MCP-инструментов — без перезапуска всего
    бэкенда (а значит без повторной загрузки локальных моделей в
    память видеокарты, которая при полном рестарте занимает время).

    Использовать: после того как в mcp_servers.json добавили/поправили
    сервер (например, чтобы подключить новый API прямо во время защиты),
    дёрнуть этот эндпоинт — и агент сразу увидит новый инструмент в
    следующем же сообщении, без перезапуска ./backend.sh.

    Требует авторизации (как и остальные /api/* эндпоинты) просто чтобы
    случайный посторонний запрос не мог дёргать перезагрузку — сама
    операция не завязана на конкретного user_id.
    """
    tools_list = await mcp_tools.refresh_mcp_tools()
    return {
        "status": "ok",
        "tools_count": len(tools_list),
        "tools": sorted(t.name for t in tools_list),
    }


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
        if key:
            # Validate against Intervals.icu before saving — otherwise a
            # typo'd key would only surface later, as a cryptic mid-chat
            # tool failure, instead of an immediate error right here.
            if not tools.intervals_icu_key_is_valid(key):
                raise HTTPException(status_code=400, detail="invalid Intervals.icu API key")
            user_settings.set_intervals_icu_key(user_id, key)
        else:
            user_settings.set_intervals_icu_key(user_id, None)
    return user_settings.get_settings(user_id)
