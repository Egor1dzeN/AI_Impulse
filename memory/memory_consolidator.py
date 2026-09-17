from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from enricher import EnrichedMemoryCandidate
from memory_matcher import MemoryMatch
from memory_grammar_normalizer import MemoryGrammarNormalizer
from semantic_normalizer import SemanticMemoryNormalizer
from models import Memory, MemoryType


class ConsolidationAction(str, Enum):
    CREATE = "create"
    CONFIRM = "confirm"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    CONFLICT = "conflict"
    IGNORE = "ignore"


@dataclass
class ConsolidationDecision:
    action: ConsolidationAction
    candidate: EnrichedMemoryCandidate
    existing_memory: Memory | None = None
    score: float = 0.0
    reason: str = ""


class MemoryConsolidator:

    def __init__(
        self,
        confirm_threshold: float = 0.85,
        update_threshold: float = 0.70,
    ) -> None:

        self.confirm_threshold = confirm_threshold
        self.update_threshold = update_threshold

        self.normalizer = SemanticMemoryNormalizer()

    # =========================================================
    # BASIC NORMALIZATION
    # =========================================================

    @staticmethod
    def normalize(text: str) -> str:

        text = text.lower()
        text = text.replace("ё", "е")

        text = re.sub(
            r"[^\w\s\-]",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text.strip()

    # =========================================================
    # RELATIONS
    # =========================================================

    @classmethod
    def relation_signature(
        cls,
        memory: Memory,
    ) -> list[tuple[str, str, str]]:

        semantic_memory = (
            SemanticMemoryNormalizer()
            .from_memory(memory)
        )

        return [
            (
                relation.subject,
                relation.predicate,
                relation.object,
            )
            for relation in semantic_memory.relations
        ]

    @classmethod
    def candidate_relation_signature(
        cls,
        candidate: EnrichedMemoryCandidate,
    ) -> list[tuple[str, str, str]]:

        semantic_memory = (
            SemanticMemoryNormalizer()
            .from_candidate(candidate)
        )

        return [
            (
                relation.subject,
                relation.predicate,
                relation.object,
            )
            for relation in semantic_memory.relations
        ]

    # =========================================================
    # SAME SEMANTIC PROPERTY
    # =========================================================

    @classmethod
    def same_semantic_property(
        cls,
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> bool:

        candidate_semantic = (
            SemanticMemoryNormalizer()
            .from_candidate(candidate)
        )

        memory_semantic = (
            SemanticMemoryNormalizer()
            .from_memory(memory)
        )

        if (
            not candidate_semantic.subject
            or not memory_semantic.subject
        ):
            return False

        if (
            not candidate_semantic.predicate
            or not memory_semantic.predicate
        ):
            return False

        return (
            candidate_semantic.subject
            == memory_semantic.subject
            and
            candidate_semantic.predicate
            == memory_semantic.predicate
        )

    # =========================================================
    # DIFFERENT SEMANTIC VALUE
    # =========================================================

    @classmethod
    def different_semantic_value(
        cls,
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> bool:

        candidate_semantic = (
            SemanticMemoryNormalizer()
            .from_candidate(candidate)
        )

        memory_semantic = (
            SemanticMemoryNormalizer()
            .from_memory(memory)
        )

        if (
            not candidate_semantic.object
            or not memory_semantic.object
        ):
            return False

        return (
            candidate_semantic.object
            != memory_semantic.object
        )

    # =========================================================
    # SAME SEMANTIC VALUE
    # =========================================================

    @classmethod
    def same_semantic_value(
        cls,
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> bool:

        candidate_semantic = (
            SemanticMemoryNormalizer()
            .from_candidate(candidate)
        )

        memory_semantic = (
            SemanticMemoryNormalizer()
            .from_memory(memory)
        )

        if (
            not candidate_semantic.subject
            or not memory_semantic.subject
        ):
            return False

        if (
            not candidate_semantic.predicate
            or not memory_semantic.predicate
        ):
            return False

        if (
            not candidate_semantic.object
            or not memory_semantic.object
        ):
            return False

        return (
            candidate_semantic.subject
            == memory_semantic.subject
            and
            candidate_semantic.predicate
            == memory_semantic.predicate
            and
            candidate_semantic.object
            == memory_semantic.object
        )

    # =========================================================
    # REAL RELATION CONFLICT
    # =========================================================

    @classmethod
    def has_relation_conflict(
        cls,
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> bool:

        candidate_relations = (
            cls.candidate_relation_signature(
                candidate
            )
        )

        memory_relations = (
            cls.relation_signature(
                memory
            )
        )

        if not candidate_relations:
            return False

        if not memory_relations:
            return False

        for (
            candidate_subject,
            candidate_predicate,
            candidate_object,
        ) in candidate_relations:

            for (
                memory_subject,
                memory_predicate,
                memory_object,
            ) in memory_relations:

                same_subject = (
                    candidate_subject
                    == memory_subject
                )

                same_predicate = (
                    candidate_predicate
                    == memory_predicate
                )

                different_object = (
                    candidate_object
                    != memory_object
                )

                if (
                    same_subject
                    and same_predicate
                    and different_object
                ):

                    return True

        return False

    # =========================================================
    # SAME CONTENT
    # =========================================================

    @classmethod
    def same_content(
        cls,
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> bool:

        return (
            cls.normalize(
                candidate.candidate.content
            )
            ==
            cls.normalize(
                memory.content
            )
        )

    # =========================================================
    # DECISION
    # =========================================================

    def decide(
            self,
            candidate: EnrichedMemoryCandidate,
            matches: list[MemoryMatch],
    ) -> ConsolidationDecision:

        if not matches:
            return ConsolidationDecision(
                action=ConsolidationAction.CREATE,
                candidate=candidate,
                existing_memory=None,
                score=0.0,
                reason="No matching active memory found.",
            )

        best_match = matches[0]

        memory = best_match.memory
        score = best_match.score

        # =====================================================
        # GOAL COMPLETION
        # =====================================================

        if (
                (candidate.candidate.metadata or {}).get("event_type")
                == "goal_completion"
        ):
            return ConsolidationDecision(
                action=ConsolidationAction.UPDATE,
                candidate=candidate,
                existing_memory=memory,
                score=score,
                reason="Goal completion updates the existing goal lifecycle.",
            )

        # =====================================================
        # EXACT CONTENT
        # =====================================================

        if self.same_content(
                candidate,
                memory,
        ):
            return ConsolidationDecision(
                action=ConsolidationAction.CONFIRM,
                candidate=candidate,
                existing_memory=memory,
                score=score,
                reason="Candidate content matches existing memory.",
            )

        # =====================================================
        # SAME SEMANTIC VALUE
        # =====================================================

        if (
            score >= self.update_threshold
            and self.same_semantic_value(
                candidate,
                memory,
            )
        ):

            return ConsolidationDecision(
                action=ConsolidationAction.CONFIRM,
                candidate=candidate,
                existing_memory=memory,
                score=score,
                reason=(
                    "Candidate has the same "
                    "normalized semantic value."
                ),
            )

        # =====================================================
        # SAME PROPERTY + DIFFERENT VALUE
        # =====================================================

        if (
            score >= self.update_threshold
            and self.same_semantic_property(
                candidate,
                memory,
            )
            and self.different_semantic_value(
                candidate,
                memory,
            )
        ):

            return ConsolidationDecision(
                action=ConsolidationAction.SUPERSEDE,
                candidate=candidate,
                existing_memory=memory,
                score=score,
                reason=(
                    "Candidate describes the same "
                    "semantic property with a new value."
                ),
            )

        # =====================================================
        # HIGH SIMILARITY
        # =====================================================

        if score >= self.confirm_threshold:

            return ConsolidationDecision(
                action=ConsolidationAction.CONFIRM,
                candidate=candidate,
                existing_memory=memory,
                score=score,
                reason=(
                    "Candidate is highly similar "
                    "to existing memory."
                ),
            )

        # =====================================================
        # MEDIUM SIMILARITY
        # =====================================================

        if score >= self.update_threshold:

            return ConsolidationDecision(
                action=ConsolidationAction.UPDATE,
                candidate=candidate,
                existing_memory=memory,
                score=score,
                reason=(
                    "Candidate appears to describe "
                    "the same memory with a different "
                    "formulation."
                ),
            )

        # =====================================================
        # WEAK MATCH
        # =====================================================

        return ConsolidationDecision(
            action=ConsolidationAction.CREATE,
            candidate=candidate,
            existing_memory=None,
            score=score,
            reason=(
                "Existing memories are not similar "
                "enough for consolidation."
            ),
        )
