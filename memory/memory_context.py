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

        # For now we just limit the context size.
        # Real retrieval/ranking will be added here later.

        active_memories = active_memories[
            : self.max_memories
        ]

        return MemoryContext(
            memories=active_memories
        )