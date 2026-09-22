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
        """Bring the index in line with SQLite, embedding only what changed:
        vectors of deleted/archived/edited memories are cut out, and only
        new or edited memories are sent to the embedder. (A full re-embed of
        every user's memories on each delete/supersede was the old behaviour.)"""
        memories = self.repository.get_active()
        by_id = {memory.id: memory for memory in memories}

        stale = [
            memory_id for memory_id in self.index.memory_ids
            if memory_id not in by_id
            or self.index.memory_signatures.get(memory_id) != self.index.memory_signature(by_id[memory_id])
        ]
        self.index.remove(stale)

        indexed = set(self.index.memory_ids)
        missing = [memory for memory in memories if memory.id not in indexed]
        if missing:
            self.index.add(
                [memory.id for memory in missing],
                self.embedder.encode_documents([memory.content for memory in missing]),
                signatures={memory.id: self.index.memory_signature(memory) for memory in missing},
            )

        if save:
            self.index.save()

    def remove(self, memory_ids) -> None:
        """Deletion path: cut the vectors out instead of a full rebuild,
        which would re-embed every remaining memory of every user."""
        if self.index.remove(memory_ids):
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
