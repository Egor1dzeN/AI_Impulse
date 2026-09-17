from __future__ import annotations

import json

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import agent, db, memory_bridge, tools
from .schemas import ChatRequest, MemoryOut

app = FastAPI(title="Impulse Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest, background_tasks: BackgroundTasks) -> StreamingResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")

    def event_stream():
        for event in agent.handle_chat_turn_stream(req.user_id, req.message):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    background_tasks.add_task(agent.extract_memory_background, req.user_id, req.message)
    response.background = background_tasks
    return response


@app.get("/api/history/{user_id}")
def history(user_id: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, role, content, action_json, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
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


@app.get("/api/memories/{user_id}", response_model=list[MemoryOut])
def memories(user_id: str) -> list[dict]:
    return memory_bridge.list_memories(user_id)


@app.get("/api/sandbox/{user_id}")
def sandbox(user_id: str) -> dict:
    return tools.get_sandbox_snapshot(user_id)
