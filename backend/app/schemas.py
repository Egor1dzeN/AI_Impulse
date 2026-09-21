from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    enable_thinking: bool = True


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class GoogleLoginRequest(BaseModel):
    id_token: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    action: dict[str, Any] | None = None
    created_at: str


class AddMemoryRequest(BaseModel):
    content: str


class MemoryOut(BaseModel):
    id: str
    type: str
    content: str
    confidence: float
    importance: float
    status: str
    created_at: str


class MemorySearchOut(MemoryOut):
    relevance: float


class SandboxOut(BaseModel):
    tasks: list[dict[str, Any]]
    expenses: list[dict[str, Any]]
    meals: list[dict[str, Any]]
    weights: list[dict[str, Any]]


class SettingsOut(BaseModel):
    intervals_icu_connected: bool
    google_calendar_connected: bool
    gmail_connected: bool


class SendEmailRequest(BaseModel):
    draft_id: str
    to: str
    subject: str
    body: str


class UpdateSettingsRequest(BaseModel):
    # None = leave as-is, "" = disconnect, non-empty = connect/replace.
    intervals_icu_api_key: str | None = None
