from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime

from enricher import EnrichedMemoryCandidate
from memory_consolidator import (
    ConsolidationAction,
    ConsolidationDecision,
)
from memory_store import MemoryStore
from models import Memory, MemoryStatus


class MemoryUpdater:

    def __init__(
        self,
        store: MemoryStore,
    ) -> None:

        self.store = store

    # =========================================================
    # METADATA
    # =========================================================

    @staticmethod
    def _build_metadata(
        candidate: EnrichedMemoryCandidate,
    ) -> dict:

        metadata: dict = {}

        # -----------------------------------------------------
        # Subject
        # -----------------------------------------------------

        if candidate.subject is not None:
            metadata["subject"] = candidate.subject.text
            metadata["subject_is_user"] = (
                candidate.subject.is_user
            )
            metadata["subject_evidence"] = (
                candidate.subject.evidence
            )
            metadata["subject_confidence"] = (
                candidate.subject.confidence
            )

        # -----------------------------------------------------
        # Entities
        # -----------------------------------------------------

        metadata["entities"] = [
            entity.text
            for entity in candidate.entities
        ]

        # -----------------------------------------------------
        # Relations
        # -----------------------------------------------------

        metadata["relations"] = []

        for relation in candidate.relations:

            metadata["relations"].append(
                {
                    "subject": relation.subject,
                    "predicate": relation.predicate,
                    "object": relation.object,
                    "confidence": relation.confidence,
                    "temporal_value": (
                        relation.temporal_value
                    ),
                    "temporal_text": (
                        relation.temporal_text
                    ),
                }
            )

        # -----------------------------------------------------
        # Temporal
        # -----------------------------------------------------

        metadata["temporal"] = []

        for expression in (
            candidate.temporal_expressions
        ):

            metadata["temporal"].append(
                {
                    "text": expression.text,
                    "type": expression.expression_type,
                    "normalized_value": (
                        expression.normalized_value
                    ),
                }
            )

        # -----------------------------------------------------
        # Grounding
        # -----------------------------------------------------

        if candidate.grounded is not None:

            metadata["grounding"] = {
                "source_text": (
                    candidate.grounded.source_text
                ),
                "start": (
                    candidate.grounded.start
                ),
                "end": (
                    candidate.grounded.end
                ),
                "confidence": (
                    candidate.grounded.confidence
                ),
                "evidence": (
                    candidate.grounded.evidence
                ),
            }

        # -----------------------------------------------------
        # Original source text
        # -----------------------------------------------------

        metadata["source_text"] = (
            candidate.candidate.source_text
        )

        return metadata

    # =========================================================
    # CREATE
    # =========================================================

    def _create_memory(
        self,
        candidate: EnrichedMemoryCandidate,
    ) -> Memory:

        now = datetime.utcnow()

        metadata = self._build_metadata(
            candidate
        )

        memory = Memory(
            id=str(uuid.uuid4()),
            user_id=candidate.candidate.user_id,
            type=candidate.candidate.type,
            content=candidate.candidate.content,
            confidence=candidate.candidate.confidence,
            importance=0.5,
            freshness=1.0,
            status=MemoryStatus.ACTIVE,
            sources=[
                deepcopy(
                    candidate.candidate.source
                )
            ],
            created_at=now,
            updated_at=now,
            valid_from=now,
            metadata=metadata,
        )

        self.store.add(
            memory
        )

        return memory

    # =========================================================
    # CONFIRM
    # =========================================================

    def _confirm(
        self,
        decision: ConsolidationDecision,
    ) -> Memory:

        memory = decision.existing_memory

        if memory is None:
            raise ValueError(
                "CONFIRM decision has no existing memory."
            )

        # -----------------------------------------------------
        # Confidence
        # -----------------------------------------------------

        old_confidence = memory.confidence
        new_confidence = (
            decision.candidate.candidate.confidence
        )

        memory.confidence = min(
            1.0,
            max(
                old_confidence,
                new_confidence,
            ),
        )

        # -----------------------------------------------------
        # Freshness
        # -----------------------------------------------------

        memory.freshness = 1.0

        # -----------------------------------------------------
        # Updated timestamp
        # -----------------------------------------------------

        memory.updated_at = datetime.utcnow()

        # -----------------------------------------------------
        # Source
        # -----------------------------------------------------

        source = decision.candidate.candidate.source

        source_exists = any(
            existing.type == source.type
            and existing.id == source.id
            for existing in memory.sources
        )

        if not source_exists:
            memory.sources.append(
                deepcopy(source)
            )

        # -----------------------------------------------------
        # Metadata merge
        # -----------------------------------------------------

        candidate_metadata = (
            self._build_metadata(
                decision.candidate
            )
        )

        memory.metadata.update(
            candidate_metadata
        )

        self.store.update(
            memory
        )

        return memory

    # =========================================================
    # UPDATE
    # =========================================================

    def _update(
        self,
        decision: ConsolidationDecision,
    ) -> Memory:

        memory = decision.existing_memory

        if memory is None:
            raise ValueError(
                "UPDATE decision has no existing memory."
            )

        candidate = decision.candidate.candidate

        # -----------------------------------------------------
        # Goal completion
        # -----------------------------------------------------

        if (candidate.metadata or {}).get("event_type") == "goal_completion":
            memory.status = MemoryStatus.ARCHIVED
            memory.valid_until = datetime.utcnow()
            memory.updated_at = datetime.utcnow()
            memory.metadata.update({
                "completed": True,
                "completed_at": datetime.utcnow().isoformat(),
                "completion_source": candidate.source_text,
            })
            memory.sources.append(deepcopy(candidate.source))
            self.store.update(memory)
            return memory

        # -----------------------------------------------------
        # Content
        # -----------------------------------------------------

        memory.content = candidate.content

        # -----------------------------------------------------
        # Confidence
        # -----------------------------------------------------

        memory.confidence = max(
            memory.confidence,
            candidate.confidence,
        )

        # -----------------------------------------------------
        # Freshness
        # -----------------------------------------------------

        memory.freshness = 1.0

        # -----------------------------------------------------
        # Updated
        # -----------------------------------------------------

        memory.updated_at = datetime.utcnow()

        # -----------------------------------------------------
        # Source
        # -----------------------------------------------------

        source = candidate.source

        source_exists = any(
            existing.type == source.type
            and existing.id == source.id
            for existing in memory.sources
        )

        if not source_exists:
            memory.sources.append(
                deepcopy(source)
            )

        # -----------------------------------------------------
        # Metadata
        # -----------------------------------------------------

        candidate_metadata = (
            self._build_metadata(
                decision.candidate
            )
        )

        memory.metadata.update(
            candidate_metadata
        )

        self.store.update(
            memory
        )

        return memory

    # =========================================================
    # SUPERSEDE
    # =========================================================

    def _supersede(
        self,
        decision: ConsolidationDecision,
    ) -> Memory:

        old_memory = decision.existing_memory

        if old_memory is None:
            raise ValueError(
                "SUPERSEDE decision has no existing memory."
            )

        # -----------------------------------------------------
        # Old memory
        # -----------------------------------------------------

        now = datetime.utcnow()

        old_memory.status = (
            MemoryStatus.SUPERSEDED
        )

        old_memory.valid_until = now
        old_memory.updated_at = now

        self.store.update(
            old_memory
        )

        # -----------------------------------------------------
        # New memory
        # -----------------------------------------------------

        return self._create_memory(
            decision.candidate
        )

    # =========================================================
    # CONFLICT
    # =========================================================

    def _conflict(
        self,
        decision: ConsolidationDecision,
    ) -> Memory:

        now = datetime.utcnow()

        candidate = decision.candidate

        metadata = self._build_metadata(
            candidate
        )

        memory = Memory(
            id=str(uuid.uuid4()),
            user_id=candidate.candidate.user_id,
            type=candidate.candidate.type,
            content=candidate.candidate.content,
            confidence=candidate.candidate.confidence,
            importance=0.5,
            freshness=1.0,
            status=MemoryStatus.CONFLICTING,
            sources=[
                deepcopy(
                    candidate.candidate.source
                )
            ],
            created_at=now,
            updated_at=now,
            valid_from=now,
            metadata=metadata,
        )

        self.store.add(
            memory
        )

        return memory

    # =========================================================
    # IGNORE
    # =========================================================

    def _ignore(
        self,
        decision: ConsolidationDecision,
    ) -> None:

        # Ничего не изменяем.

        return None

    # =========================================================
    # PUBLIC
    # =========================================================

    def apply(
        self,
        decision: ConsolidationDecision,
    ) -> Memory | None:

        action = decision.action

        if action == ConsolidationAction.CREATE:
            return self._create_memory(
                decision.candidate
            )

        if action == ConsolidationAction.CONFIRM:
            return self._confirm(
                decision
            )

        if action == ConsolidationAction.UPDATE:
            return self._update(
                decision
            )

        if action == ConsolidationAction.SUPERSEDE:
            return self._supersede(
                decision
            )

        if action == ConsolidationAction.CONFLICT:
            return self._conflict(
                decision
            )

        if action == ConsolidationAction.IGNORE:
            return self._ignore(
                decision
            )

        raise ValueError(
            f"Unknown consolidation action: {action}"
        )