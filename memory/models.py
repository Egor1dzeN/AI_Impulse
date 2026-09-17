from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ============================================================
# MEMORY TYPE
# ============================================================

class MemoryType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    EPISODE = "episode"
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    GOAL = "goal"
    STATE = "state"
    PROCEDURE = "procedure"


# ============================================================
# MEMORY STATUS
# ============================================================

class MemoryStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    CONFLICTING = "conflicting"


# ============================================================
# SOURCE
# ============================================================

@dataclass
class Source:
    type: str
    id: str
    title: str | None = None
    created_at: datetime | None = None
    url: str | None = None


# ============================================================
# EVENT
# ============================================================

@dataclass
class Event:
    id: str
    type: str
    source: Source
    content: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================
# MEMORY CANDIDATE
# ============================================================

@dataclass
class MemoryCandidate:
    id: str
    user_id: str
    type: MemoryType

    # Нормализованный текст candidate.
    #
    # Например:
    # "Пользователь живёт в Москве"
    content: str

    confidence: float
    source: Source
    created_at: datetime

    # Оригинальный текст пользователя.
    #
    # Именно его используют:
    # - NER
    # - Coreference
    # - Temporal
    # - Subject Resolver
    source_text: str = ""

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


# ============================================================
# MEMORY
# ============================================================

@dataclass
class Memory:
    id: str
    user_id: str

    type: MemoryType

    content: str

    confidence: float
    importance: float
    freshness: float

    status: MemoryStatus

    sources: list[Source] = field(
        default_factory=list
    )

    created_at: datetime = field(
        default_factory=datetime.utcnow
    )

    updated_at: datetime = field(
        default_factory=datetime.utcnow
    )

    valid_from: datetime | None = None
    valid_until: datetime | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )