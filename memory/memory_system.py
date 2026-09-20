from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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

logger = logging.getLogger(__name__)


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
        # We create them here against the persistent store.
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

        # every_n_memories=0 disables the periodic auto-trigger — only
        # force_dream() invokes it for now, nothing calls notify_memory_changes.
        self.dreaming_engine = DreamingEngine(store=self.store, qwen=qwen, every_n_memories=0)

    # =========================================================
    # PUBLIC API
    # =========================================================

    def update(self, text: str, user_id: str):
        """Process a chat message, skipping obvious non-memory inputs early."""
        route = self.cheap_memory_router.route(text)

        if route.value == "skip":
            logger.info("memory update: user_id=%s text=%r -> SKIPPED by cheap_memory_router", user_id, text)
            return self.pipeline.empty_result()

        return self._process_and_sync("update", text, user_id)

    def handmade_memory(self, text: str, user_id: str):
        """Process a fact the user typed directly into the memory UI.

        Skips cheap_memory_router: it's tuned to drop chat noise (questions,
        commands, casual replies like "ок"/"спасибо"), but a deliberately
        entered fact can innocently start with a question word ("Как зовут
        собаку - Рекс") and would otherwise be silently discarded even
        though the user explicitly asked to save it.
        """
        return self._process_and_sync("handmade_memory", text, user_id)

    def _process_and_sync(self, source: str, text: str, user_id: str):
        result = self.pipeline.process(
            text=text,
            user_id=user_id,
        )

        logger.info(
            "%s: user_id=%s text=%r -> %d candidate(s)",
            source, user_id, text, len(result.decisions),
        )
        for decision, memory in zip(result.decisions, result.memories):
            content = memory.content if memory is not None else decision.candidate.candidate.content
            logger.info(
                "  %s decision: action=%s content=%r score=%.2f reason=%r",
                source, decision.action.value, content, decision.score, decision.reason,
            )

        self.index_manager.sync_after_pipeline(
            changed_memories=result.memories,
            decisions=result.decisions,
        )
        return result

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

    def delete(self, memory_id: str, user_id: str) -> None:
        """Delete one memory belonging to the specified user."""
        memory = self.repository.get(memory_id)
        if memory is None:
            raise ValueError(f"Memory with id '{memory_id}' does not exist.")
        if memory.user_id != user_id:
            raise ValueError("Memory does not belong to the specified user.")

        self.repository.delete(memory_id)
        self.index_manager.rebuild()

    def force_dream(self, user_id: str) -> dict[str, Any]:
        """Batch memory consolidation ("dreaming"): review all of a user's
        active memories together in one LLM pass, merging obvious
        duplicates into a compacted record and archiving explicitly
        superseded/cancelled/completed ones — the kind of cleanup
        incremental per-message consolidation (matcher+consolidator, which
        only ever compares one new candidate against its single best match)
        can't do on its own.

        Delegates to DreamingEngine (dreaming_engine.py), which is strict
        about it: an archive is only honored if the model's own stated
        reason contains an explicit obsolescence marker AND is backed by
        matching semantic scope/relations (or an explicit marker in the
        memory's own text) — "unrelated to another memory's topic" alone
        is never accepted as a reason. Nothing is ever hard deleted, only
        marked ARCHIVED, and expired memories (valid_until in the past) are
        archived deterministically before the LLM is even involved.
        """
        if self.qwen is None:
            raise RuntimeError("force_dream() requires a qwen model — construct MemorySystem with qwen=...")

        result = self.dreaming_engine.force_run(user_id)

        merged = [
            {"keep_id": op.keep_id, "removed_ids": op.archive_ids, "reason": op.reason}
            for op in result.merge_operations
        ]
        archived = [
            {"ids": op.memory_ids, "reason": op.reason}
            for op in result.archive_operations
        ]

        if result.summary:
            summary = result.summary
        elif merged or archived:
            parts = []
            if merged:
                parts.append(f"объединено записей: {sum(len(m['removed_ids']) for m in merged)}")
            if archived:
                parts.append(f"архивировано: {sum(len(a['ids']) for a in archived)}")
            summary = ", ".join(parts).capitalize() + "."
        else:
            summary = "Дубликатов и противоречий не найдено."

        return {"merged": merged, "archived": archived, "summary": summary}

    def clear(self, user_id: str) -> None:
        """Delete all memories of the specified user."""
        memories = self.repository.find_by_user(
            user_id=user_id,
            active_only=False,
        )

        for memory in memories:
            self.repository.delete(memory.id)

        self.index_manager.rebuild()

    def close(self) -> None:
        self.repository.close()

