from __future__ import annotations

from dataclasses import dataclass

from enricher import EnrichedMemoryCandidate, MemoryEnricher

from memory_context import MemoryContextBuilder

from memory_matcher import (
    MemoryMatch,
    MemoryMatcher,
)

from memory_consolidator import (
    ConsolidationDecision,
    MemoryConsolidator,
)

from memory_updater import MemoryUpdater

from memory_store import MemoryStore

from models import Memory, Source


@dataclass
class MemoryPipelineResult:
    enriched_candidates: list[
        EnrichedMemoryCandidate
    ]

    matches: list[
        list[MemoryMatch]
    ]

    decisions: list[
        ConsolidationDecision
    ]

    memories: list[
        Memory | None
    ]


class MemoryPipeline:

    def __init__(
        self,
        enricher: MemoryEnricher,
        matcher: MemoryMatcher,
        consolidator: MemoryConsolidator,
        updater: MemoryUpdater,
        store: MemoryStore,
        context_builder: MemoryContextBuilder | None = None,
    ) -> None:

        self.enricher = enricher
        self.matcher = matcher
        self.consolidator = consolidator
        self.updater = updater
        self.store = store

        self.context_builder = (
            context_builder
            if context_builder is not None
            else MemoryContextBuilder(
                max_memories=20
            )
        )

    # =========================================================
    # PROCESS
    # =========================================================

    def process(
        self,
        text: str,
        user_id: str,
        source: Source | None = None,
    ) -> MemoryPipelineResult:

        # =====================================================
        # 0. GET CURRENT MEMORY
        # =====================================================

        existing_memories = (
            self.store.find_by_user(
                user_id=user_id,
                active_only=True,
            )
        )

        # =====================================================
        # 1. BUILD CONTEXT
        # =====================================================

        context = (
            self.context_builder.build(
                existing_memories
            )
        )

        # =====================================================
        # 2. EXTRACTION + ENRICHMENT
        # =====================================================

        enriched_candidates = (
            self.enricher.process(
                text=text,
                user_id=user_id,
                context=context,
                source=source,
            )
        )

        # =====================================================
        # 3. PREPARE RESULTS
        # =====================================================

        all_matches: list[
            list[MemoryMatch]
        ] = []

        decisions: list[
            ConsolidationDecision
        ] = []

        memories: list[
            Memory | None
        ] = []

        # =====================================================
        # 4. PROCESS EACH CANDIDATE
        # =====================================================

        for enriched_candidate in (
            enriched_candidates
        ):

            # -------------------------------------------------
            # MATCHING
            # -------------------------------------------------

            matches = (
                self.matcher.find_matches(
                    enriched_candidate
                )
            )

            all_matches.append(
                matches
            )

            # -------------------------------------------------
            # CONSOLIDATION
            # -------------------------------------------------

            decision = (
                self.consolidator.decide(
                    candidate=enriched_candidate,
                    matches=matches,
                )
            )

            decisions.append(
                decision
            )

            # -------------------------------------------------
            # UPDATE STORE
            # -------------------------------------------------

            memory = (
                self.updater.apply(
                    decision
                )
            )

            memories.append(
                memory
            )

        # =====================================================
        # 5. RETURN
        # =====================================================

        return MemoryPipelineResult(
            enriched_candidates=(
                enriched_candidates
            ),
            matches=all_matches,
            decisions=decisions,
            memories=memories,
        )

    def empty_result(self) -> MemoryPipelineResult:
        return MemoryPipelineResult(
            enriched_candidates=[],
            matches=[],
            decisions=[],
            memories=[],
        )

    # =========================================================
    # MEMORY ACCESS
    # =========================================================

    def get_all_memories(
        self,
    ) -> list[Memory]:

        return self.store.get_all()

    def get_active_memories(
        self,
    ) -> list[Memory]:

        return self.store.get_active()

    def get_user_memories(
        self,
        user_id: str,
        active_only: bool = False,
    ) -> list[Memory]:

        return self.store.find_by_user(
            user_id=user_id,
            active_only=active_only,
        )

    # =========================================================
    # CLEAR
    # =========================================================

    def clear(self) -> None:

        self.store.clear()