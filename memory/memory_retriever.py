from __future__ import annotations

from dataclasses import dataclass

from models import Memory, MemoryStatus, MemoryType

from faiss_memory_index import FaissHit, FaissMemoryIndex
from memory_repository import MemoryRepository
from memory_embedder import Qwen3Embedder


@dataclass(frozen=True)
class RetrievedMemory:
    memory: Memory
    vector_score: float


class MemoryRetriever:
    """Fast first-stage retrieval: bi-encoder -> FAISS -> metadata filtering."""

    def __init__(
        self,
        repository: MemoryRepository,
        embedder: Qwen3Embedder,
        index: FaissMemoryIndex,
    ) -> None:
        self.repository = repository
        self.embedder = embedder
        self.index = index

    def rebuild_index(
        self,
        user_id: str | None = None,
        active_only: bool = True,
        save: bool = True,
    ) -> None:
        if user_id is None:
            memories = (
                self.repository.get_active()
                if active_only
                else self.repository.get_all()
            )
        else:
            memories = self.repository.find_by_user(
                user_id=user_id,
                active_only=active_only,
            )

        self.index.build(
            memories=memories,
            embedder=self.embedder,
        )

        if save:
            self.index.save()

    def search(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        candidate_k: int | None = None,
        memory_types: set[MemoryType] | None = None,
        active_only: bool = True,
    ) -> list[RetrievedMemory]:
        candidate_k = candidate_k or max(top_k * 4, 20)

        query_embedding = self.embedder.encode_query(
            query
        )

        hits = self.index.search(
            query_embedding=query_embedding,
            top_k=candidate_k,
        )

        results: list[RetrievedMemory] = []

        for hit in hits:
            memory = self.repository.get(
                hit.memory_id
            )

            if memory is None:
                continue

            if memory.user_id != user_id:
                continue

            if (
                active_only
                and memory.status != MemoryStatus.ACTIVE
            ):
                continue

            if (
                memory_types is not None
                and memory.type not in memory_types
            ):
                continue

            results.append(
                RetrievedMemory(
                    memory=memory,
                    vector_score=hit.score,
                )
            )

            if len(results) >= top_k:
                break

        return results

    def search_active(
        self,
        query: str,
        user_id: str,
        top_k: int = 10,
        candidate_k: int | None = None,
    ) -> list[RetrievedMemory]:
        return self.search(
            query=query,
            user_id=user_id,
            top_k=top_k,
            candidate_k=candidate_k,
            active_only=True,
        )
