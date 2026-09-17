from __future__ import annotations

from models import Memory


class MemoryContextFormatter:
    """Converts retrieved memories into compact LLM-readable context."""

    def format(self, memories: list[Memory]) -> str:
        active = [
            memory
            for memory in memories
            if memory.status.value == "active"
        ]

        if not active:
            return ""

        lines = ["Relevant memories:"]
        lines.extend(f"- {memory.content}" for memory in active)
        return "\n".join(lines)
