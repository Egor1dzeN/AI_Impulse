from __future__ import annotations

from pathlib import Path

from faiss_memory_index import FaissMemoryIndex
from memory_embedder import Qwen3Embedder
from memory_repository import MemoryRepository
from models import Memory


class MemoryIndexManager:
    """Keeps the persistent FAISS search index aligned with SQLite."""

    def __init__(
        self,
        repository: MemoryRepository,
        embedder: Qwen3Embedder,
        index: FaissMemoryIndex,
    ) -> None:
        self.repository = repository
        self.embedder = embedder
        self.index = index

    def ensure_ready(self) -> None:
        memories = self.repository.get_active()

        try:
            self.index.load()
        except FileNotFoundError:
            self.rebuild()
            return
        except (ValueError, KeyError, OSError):
            self.rebuild()
            return

        if not self.index.is_in_sync(memories):
            self.rebuild()

    def rebuild(self, save: bool = True) -> None:
        memories = self.repository.get_active()
        self.index.build(memories, self.embedder)

        if save:
            self.index.save()

    def sync_after_pipeline(
        self,
        changed_memories: list[Memory | None],
        decisions,
    ) -> None:
        """Update FAISS once after a complete write transaction.

        CREATE-only transactions are appended incrementally.
        Updates/supersedes can remove old vectors, so those transactions use
        a full rebuild. CONFIRM/IGNORE/CONFLICT do not change the active
        embedding set and require no rebuild by themselves.
        """

        actions = {
            getattr(decision.action, "value", str(decision.action)).lower()
            for decision in decisions
        }

        if not actions:
            return

        destructive = {
            "update",
            "supersede",
        }

        if actions & destructive:
            self.rebuild()
            return

        created = [
            memory
            for memory in changed_memories
            if memory is not None
            and memory.status.value == "active"
            and memory.id not in self.index.memory_ids
        ]

        if not created:
            return

        embeddings = self.embedder.encode_documents(
            [memory.content for memory in created]
        )

        self.index.add(
            [memory.id for memory in created],
            embeddings,
            signatures={
                memory.id: self.index.memory_signature(memory)
                for memory in created
            },
        )
        self.index.save()
