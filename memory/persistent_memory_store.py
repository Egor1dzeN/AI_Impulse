from __future__ import annotations

from memory_store import MemoryStore
from memory_repository import MemoryRepository
from models import Memory, MemoryType


class PersistentMemoryStore(MemoryStore):
    """MemoryStore-compatible adapter backed by SQLite.

    The existing matcher/updater code can use this class without changes.
    SQLite is the source of truth; no in-memory copy is maintained.
    """

    def __init__(self, repository: MemoryRepository) -> None:
        super().__init__()
        self.repository = repository

    def add(self, memory: Memory) -> None:
        self.repository.add(memory)

    def get(self, memory_id: str) -> Memory | None:
        return self.repository.get(memory_id)

    def get_all(self) -> list[Memory]:
        return self.repository.get_all()

    def get_active(self) -> list[Memory]:
        return self.repository.get_active()

    def update(self, memory: Memory) -> None:
        self.repository.update(memory)

    def delete(self, memory_id: str) -> None:
        self.repository.delete(memory_id)

    def find_by_user(
        self,
        user_id: str,
        active_only: bool = False,
    ) -> list[Memory]:
        return self.repository.find_by_user(
            user_id=user_id,
            active_only=active_only,
        )

    def find_by_type(
        self,
        memory_type: MemoryType,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[Memory]:
        return self.repository.find_by_type(
            memory_type=memory_type,
            user_id=user_id,
            active_only=active_only,
        )

    def clear(self) -> None:
        self.repository.clear()

    def __len__(self) -> int:
        return len(self.repository)
