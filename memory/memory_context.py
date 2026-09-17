from __future__ import annotations

from dataclasses import dataclass

from models import Memory


@dataclass
class MemoryContext:
    memories: list[Memory]


class MemoryContextBuilder:

    def __init__(
        self,
        max_memories: int = 20,
    ) -> None:

        self.max_memories = max_memories

    def build(
        self,
        memories: list[Memory],
    ) -> MemoryContext:

        active_memories = [
            memory
            for memory in memories
            if memory.status.value == "active"
        ]

        # Пока просто ограничиваем объём контекста.
        # Позже сюда добавим настоящий retrieval/ranking.

        active_memories = active_memories[
            : self.max_memories
        ]

        return MemoryContext(
            memories=active_memories
        )