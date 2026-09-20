from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

from dreaming_engine import DreamingEngine
from enricher import MemoryEnricher
from faiss_memory_index import FaissMemoryIndex
from memory_consolidator import MemoryConsolidator
from memory_context_formatter import MemoryContextFormatter
from memory_index_manager import MemoryIndexManager
from memory_matcher import MemoryMatcher
from memory_pipeline import MemoryPipeline
from memory_repository import MemoryRepository
from memory_retriever import MemoryRetriever
from memory_retriever_v2 import MemoryRetrieverV2
from memory_updater import MemoryUpdater
from persistent_memory_store import PersistentMemoryStore
from retrieval_model_manager import RetrievalModelManager
from cheap_memory_router import CheapMemoryRouter
from models import Memory, MemoryStatus, MemoryType


class MemorySystem:
    """Complete persistent memory subsystem for Agent Core."""

    def __init__(
        self,
        enricher: MemoryEnricher,
        consolidator: MemoryConsolidator,
        db_path: str | Path = "memory.db",
        index_dir: str | Path = "memory_index",
        retrieval_models: RetrievalModelManager | None = None,
        qwen: Any = None,
    ) -> None:
        # Only force_dream() needs raw generation access — everything else
        # goes through the enricher/retrieval_models it already has.
        self.qwen = qwen

        self.repository = MemoryRepository(db_path)
        self.store = PersistentMemoryStore(self.repository)

        # Existing matcher/updater logic works with the MemoryStore API.
        matcher = MemoryMatcher(self.store)
        updater = MemoryUpdater(self.store)

        self.pipeline = MemoryPipeline(
            enricher=enricher,
            matcher=matcher,
            consolidator=consolidator,
            updater=updater,
            store=self.store,
        )

        # One RetrievalModelManager per runtime. It owns exactly one
        # Embedding model and one Reranker model.
        self.retrieval_models = (
            retrieval_models
            if retrieval_models is not None
            else RetrievalModelManager()
        )

        self.index = FaissMemoryIndex(
            dimension=self.retrieval_models.embedder.dimension,
            index_dir=index_dir,
        )

        self.index_manager = MemoryIndexManager(
            repository=self.repository,
            embedder=self.retrieval_models.embedder,
            index=self.index,
        )
        self.index_manager.ensure_ready()

        self.retriever = MemoryRetriever(
            repository=self.repository,
            embedder=self.retrieval_models.embedder,
            index=self.index,
        )

        self.memory_retriever = MemoryRetrieverV2(
            retriever=self.retriever,
            reranker=self.retrieval_models.reranker,
        )

        self.context_formatter = MemoryContextFormatter()
        self.cheap_memory_router = CheapMemoryRouter()

        # Automatic Dreaming is disabled for now.
        # Only force_dream() invokes it.
        self.dreaming_engine = DreamingEngine(
            store=self.store,
            qwen=qwen,
            every_n_memories=0,
        )

    # =========================================================
    # PUBLIC API
    # =========================================================

    def update(self, text: str, user_id: str):
        """Process a message, skipping obvious non-memory inputs early."""
        route = self.cheap_memory_router.route(text)

        if route.value == "skip":
            return self.pipeline.empty_result()

        result = self.pipeline.process(
            text=text,
            user_id=user_id,
        )

        self.index_manager.sync_after_pipeline(
            changed_memories=result.memories,
            decisions=result.decisions,
        )

        return result

    # =========================================================
    # MANUAL MEMORY
    # =========================================================

    def add_memory(
        self,
        content: str,
        user_id: str,
        memory_type: MemoryType = MemoryType.FACT,
        confidence: float = 1.0,
        importance: float = 1.0,
        freshness: float = 1.0,
    ) -> Memory:
        """Immediately add a manually created memory.

        Manual memories bypass:
        - cheap memory router
        - extraction
        - validation
        - grammar normalization
        - matching
        - consolidation
        - updater pipeline

        The memory is always created as ACTIVE and immediately added
        to both SQLite and FAISS.
        """

        now = datetime.utcnow()

        memory = Memory(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
            freshness=freshness,
            status=MemoryStatus.ACTIVE,
            sources=[],
            created_at=now,
            updated_at=now,
            valid_from=now,
            valid_until=None,
            metadata={
                "manual": True,
                "subject": None,
                "subject_is_user": False,
                "subject_evidence": "manual",
                "subject_confidence": 1.0,
                "entities": [],
                "relations": [],
                "temporal": [],
                "grounding": {},
                "semantic_scope": None,
                "semantic_relations": [],
                "source_text": content,
            },
        )

        # Save to SQLite.
        self.repository.add(memory)

        # Add to FAISS.
        embedding = self.retrieval_models.embedder.encode_documents(
            [memory.content]
        )

        self.index.add(
            [memory.id],
            embedding,
            signatures={
                memory.id: self.index.memory_signature(memory)
            },
        )

        self.index.save()

        return memory

    # =========================================================
    # READ
    # =========================================================

    def get_all(self, user_id: str):
        """Return all memories of the user, including non-active ones."""
        return self.repository.find_by_user(
            user_id=user_id,
            active_only=False,
        )

    def retrieve(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
    ):
        """Retrieve the most relevant active memories for the agent."""
        return self.memory_retriever.search(
            query=query,
            user_id=user_id,
            top_k=top_k,
            candidate_k=max(top_k * 3, 12),
        )

    # =========================================================
    # DELETE
    # =========================================================

    def delete(
        self,
        memory_id: str,
        user_id: str,
    ) -> None:
        """Delete one memory belonging to the specified user."""

        memory = self.repository.get(memory_id)

        if memory is None:
            raise ValueError(
                f"Memory with id '{memory_id}' does not exist."
            )

        if memory.user_id != user_id:
            raise ValueError(
                "Memory does not belong to the specified user."
            )

        self.repository.delete(memory_id)
        self.index_manager.rebuild()

    # =========================================================
    # DREAMING
    # =========================================================

    def force_dream(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """Run Dreaming for the specified user."""

        if self.qwen is None:
            raise RuntimeError(
                "force_dream() requires a qwen model — "
                "construct MemorySystem with qwen=..."
            )

        result = self.dreaming_engine.force_run(user_id)

        merged = [
            {
                "keep_id": op.keep_id,
                "removed_ids": op.archive_ids,
                "reason": op.reason,
            }
            for op in result.merge_operations
        ]

        archived = [
            {
                "ids": op.memory_ids,
                "reason": op.reason,
            }
            for op in result.archive_operations
        ]

        if result.summary:
            summary = result.summary

        elif merged or archived:
            parts = []

            if merged:
                parts.append(
                    "объединено записей: "
                    f"{sum(len(m['removed_ids']) for m in merged)}"
                )

            if archived:
                parts.append(
                    "архивировано: "
                    f"{sum(len(a['ids']) for a in archived)}"
                )

            summary = ", ".join(parts).capitalize() + "."

        else:
            summary = "Дубликатов и противоречий не найдено."

        return {
            "merged": merged,
            "archived": archived,
            "summary": summary,
        }

    # =========================================================
    # CLEAR
    # =========================================================

    def clear(
        self,
        user_id: str,
    ) -> None:
        """Delete all memories of the specified user."""

        memories = self.repository.find_by_user(
            user_id=user_id,
            active_only=False,
        )

        for memory in memories:
            self.repository.delete(memory.id)

        self.index_manager.rebuild()

    # =========================================================
    # CLOSE
    # =========================================================

    def close(self) -> None:
        self.repository.close()
