from __future__ import annotations

from typing import Any, Literal

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


class MemorySourceOut(BaseModel):
    type: str
    id: str
    title: str | None = None
    created_at: str | None = None


class MemoryOut(BaseModel):
    id: str
    type: str
    content: str
    confidence: float
    importance: float
    status: str
    created_at: str
    sources: list[MemorySourceOut] = []
    valid_from: str | None = None
    valid_until: str | None = None


class MemorySearchOut(MemoryOut):
    relevance: float


class SandboxOut(BaseModel):
    tasks: list[dict[str, Any]]
    expenses: list[dict[str, Any]]
    meals: list[dict[str, Any]]
    weights: list[dict[str, Any]]


class TransactionOut(BaseModel):
    id: str
    amount: float
    type: str
    category: str
    note: str | None = None
    date: str
    created_at: str


class FinanceSummaryOut(BaseModel):
    total_expense: float
    total_income: float
    balance: float
    by_month: dict[str, dict[str, float]]
    expense_by_category: dict[str, float]
    income_by_category: dict[str, float]


class SettingsOut(BaseModel):
    intervals_icu_connected: bool
    google_calendar_connected: bool
    gmail_connected: bool


class SendEmailRequest(BaseModel):
    draft_id: str
    to: str
    subject: str
    body: str


class CalendarActionRequest(BaseModel):
    draft_id: str
    kind: Literal["create", "update", "delete"]
    event_id: str | None = None  # required for update/delete
    title: str | None = None
    start_time: str | None = None
    duration_minutes: int | None = None
    notes: str | None = None
    attendees: list[str] | None = None  # create only: invitee emails


class UpdateSettingsRequest(BaseModel):
    # None = leave as-is, "" = disconnect, non-empty = connect/replace.
    intervals_icu_api_key: str | None = None
