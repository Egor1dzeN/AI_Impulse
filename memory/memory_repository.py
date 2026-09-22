from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from models import Memory, MemoryStatus, MemoryType, Source


class MemoryRepository:
    """Persistent structured storage for long-term memories.

    SQLite is the source of truth. The vector index is only a search index
    and can be rebuilt from this repository at any time.
    """

    def __init__(self, db_path: str | Path = "memory.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # This one connection is a long-lived singleton (via memory_bridge's
        # model registry) reused across FastAPI's threadpool worker threads
        # and BackgroundTasks — check_same_thread=False lifts sqlite3's
        # same-thread guard, and the lock serializes access since a raw
        # connection still isn't safe under concurrent use from multiple
        # threads at once.
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    freshness REAL NOT NULL,
                    status TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_until TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_user_status ON memories(user_id, status)"
            )
            self._connection.commit()

    @staticmethod
    def _datetime_to_json(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    @staticmethod
    def _datetime_from_json(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)

    @staticmethod
    def _source_to_dict(source: Source) -> dict[str, Any]:
        if is_dataclass(source):
            result: dict[str, Any] = {}
            for field in fields(source):
                value = getattr(source, field.name)
                if isinstance(value, datetime):
                    value = value.isoformat()
                elif isinstance(value, Enum):
                    value = value.value
                result[field.name] = value
            return result

        raise TypeError("Source must be a dataclass")

    @staticmethod
    def _source_from_dict(data: dict[str, Any]) -> Source:
        field_names = {field.name for field in fields(Source)}
        kwargs: dict[str, Any] = {}

        for key, value in data.items():
            if key not in field_names:
                continue

            field = next(field for field in fields(Source) if field.name == key)

            if value is not None and field.name in ("created_at", "end_at"):
                try:
                    value = datetime.fromisoformat(value)
                except (TypeError, ValueError):
                    pass

            kwargs[key] = value

        return Source(**kwargs)

    @staticmethod
    def _encode_sources(sources: list[Source]) -> str:
        return json.dumps(
            [MemoryRepository._source_to_dict(source) for source in sources],
            ensure_ascii=False,
        )

    @staticmethod
    def _decode_sources(value: str) -> list[Source]:
        raw = json.loads(value)
        if not isinstance(raw, list):
            return []
        return [
            MemoryRepository._source_from_dict(item)
            for item in raw
            if isinstance(item, dict)
        ]

    def add(self, memory: Memory) -> None:
        self._insert_or_replace(memory, replace=False)

    def upsert(self, memory: Memory) -> None:
        self._insert_or_replace(memory, replace=True)

    def _insert_or_replace(
        self,
        memory: Memory,
        replace: bool,
    ) -> None:
        sql = (
            "INSERT OR REPLACE INTO memories "
            if replace
            else "INSERT INTO memories "
        ) + "(" \
            "id, user_id, type, content, confidence, importance, freshness, " \
            "status, sources_json, created_at, updated_at, valid_from, " \
            "valid_until, metadata_json" \
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"

        with self._lock:
            self._connection.execute(
                sql,
                (
                    memory.id,
                    memory.user_id,
                    memory.type.value,
                    memory.content,
                    float(memory.confidence),
                    float(memory.importance),
                    float(memory.freshness),
                    memory.status.value,
                    self._encode_sources(memory.sources),
                    self._datetime_to_json(memory.created_at),
                    self._datetime_to_json(memory.updated_at),
                    self._datetime_to_json(memory.valid_from),
                    self._datetime_to_json(memory.valid_until),
                    json.dumps(memory.metadata or {}, ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def get(self, memory_id: str) -> Memory | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()

        return None if row is None else self._row_to_memory(row)

    def get_all(self) -> list[Memory]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM memories ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def get_active(self) -> list[Memory]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM memories WHERE status = ? ORDER BY created_at ASC",
                (MemoryStatus.ACTIVE.value,),
            ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def find_by_user(
        self,
        user_id: str,
        active_only: bool = False,
    ) -> list[Memory]:
        with self._lock:
            if active_only:
                rows = self._connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at ASC
                    """,
                    (user_id, MemoryStatus.ACTIVE.value),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE user_id = ?
                    ORDER BY created_at ASC
                    """,
                    (user_id,),
                ).fetchall()

        return [self._row_to_memory(row) for row in rows]

    def find_by_type(
        self,
        memory_type: MemoryType,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE type = ?"
        params: list[Any] = [memory_type.value]

        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)

        if active_only:
            sql += " AND status = ?"
            params.append(MemoryStatus.ACTIVE.value)

        sql += " ORDER BY created_at ASC"

        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def update(self, memory: Memory) -> None:
        self.upsert(memory)

    def delete(self, memory_id: str) -> None:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM memories WHERE id = ?",
                (memory_id,),
            )
            self._connection.commit()

        if cursor.rowcount == 0:
            raise ValueError(f"Memory with id '{memory_id}' does not exist.")

    def delete_by_user(self, user_id: str) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM memories WHERE user_id = ?",
                (user_id,),
            )
            self._connection.commit()
        return cursor.rowcount

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM memories")
            self._connection.commit()

    def __len__(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM memories"
            ).fetchone()
        return int(row["count"])

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            user_id=row["user_id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            confidence=float(row["confidence"]),
            importance=float(row["importance"]),
            freshness=float(row["freshness"]),
            status=MemoryStatus(row["status"]),
            sources=MemoryRepository._decode_sources(row["sources_json"]),
            created_at=MemoryRepository._datetime_from_json(row["created_at"]),
            updated_at=MemoryRepository._datetime_from_json(row["updated_at"]),
            valid_from=MemoryRepository._datetime_from_json(row["valid_from"]),
            valid_until=MemoryRepository._datetime_from_json(row["valid_until"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )
