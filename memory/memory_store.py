from __future__ import annotations

from models import Memory, MemoryStatus


class MemoryStore:

    def __init__(self) -> None:
        self._memories: dict[str, Memory] = {}

    # =========================================================
    # CREATE
    # =========================================================

    def add(
        self,
        memory: Memory,
    ) -> None:

        if memory.id in self._memories:
            raise ValueError(
                f"Memory with id '{memory.id}' already exists."
            )

        self._memories[memory.id] = memory

    # =========================================================
    # READ
    # =========================================================

    def get(
        self,
        memory_id: str,
    ) -> Memory | None:

        return self._memories.get(
            memory_id
        )

    def get_all(self) -> list[Memory]:

        return list(
            self._memories.values()
        )

    def get_active(self) -> list[Memory]:

        return [
            memory
            for memory in self._memories.values()
            if memory.status == MemoryStatus.ACTIVE
        ]

    # =========================================================
    # UPDATE
    # =========================================================

    def update(
        self,
        memory: Memory,
    ) -> None:

        if memory.id not in self._memories:
            raise ValueError(
                f"Memory with id '{memory.id}' "
                "does not exist."
            )

        self._memories[memory.id] = memory

    # =========================================================
    # DELETE
    # =========================================================

    def delete(
        self,
        memory_id: str,
    ) -> None:

        if memory_id not in self._memories:
            raise ValueError(
                f"Memory with id '{memory_id}' "
                "does not exist."
            )

        del self._memories[memory_id]

    # =========================================================
    # FILTERING
    # =========================================================

    def find_by_user(
        self,
        user_id: str,
        active_only: bool = False,
    ) -> list[Memory]:

        memories = [
            memory
            for memory in self._memories.values()
            if memory.user_id == user_id
        ]

        if active_only:

            memories = [
                memory
                for memory in memories
                if memory.status == MemoryStatus.ACTIVE
            ]

        return memories

    def find_by_type(
        self,
        memory_type,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[Memory]:

        memories = [
            memory
            for memory in self._memories.values()
            if memory.type == memory_type
        ]

        if user_id is not None:

            memories = [
                memory
                for memory in memories
                if memory.user_id == user_id
            ]

        if active_only:

            memories = [
                memory
                for memory in memories
                if memory.status == MemoryStatus.ACTIVE
            ]

        return memories

    # =========================================================
    # CLEAR
    # =========================================================

    def clear(self) -> None:

        self._memories.clear()

    def __len__(self) -> int:

        return len(
            self._memories
        )