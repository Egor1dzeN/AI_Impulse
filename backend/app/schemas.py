from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_id: str
    message: str


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    action: dict[str, Any] | None = None
    created_at: str


class MemoryOut(BaseModel):
    id: str
    type: str
    content: str
    confidence: float
    importance: float
    status: str
    created_at: str


class SandboxOut(BaseModel):
    tasks: list[dict[str, Any]]
    expenses: list[dict[str, Any]]
    meals: list[dict[str, Any]]
    weights: list[dict[str, Any]]
