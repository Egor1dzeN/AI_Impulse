from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from enricher import EnrichedMemoryCandidate
from memory_store import MemoryStore
from models import Memory, MemoryType
from semantic_normalizer import (
    SemanticMemoryNormalizer,
    SemanticRelation, SemanticMemory,
)


@dataclass
class MemoryMatch:
    memory: Memory
    score: float
    evidence: list[str]


class MemoryMatcher:

    def __init__(
        self,
        store: MemoryStore,
        min_score: float = 0.50,
    ) -> None:

        self.store = store
        self.min_score = min_score
        self.normalizer = (
            SemanticMemoryNormalizer()
        )

    # =========================================================
    # TEXT
    # =========================================================

    @staticmethod
    def normalize(
        text: str,
    ) -> str:

        text = text.lower()
        text = text.replace(
            "ё",
            "е",
        )

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

    @classmethod
    def text_similarity(
        cls,
        text_a: str,
        text_b: str,
    ) -> float:

        a = cls.normalize(
            text_a
        )

        b = cls.normalize(
            text_b
        )

        if not a or not b:
            return 0.0

        if a == b:
            return 1.0

        sequence_score = (
            SequenceMatcher(
                None,
                a,
                b,
            ).ratio()
        )

        words_a = set(
            a.split()
        )

        words_b = set(
            b.split()
        )

        if not words_a or not words_b:
            return sequence_score

        intersection = (
            words_a & words_b
        )

        union = (
            words_a | words_b
        )

        jaccard = (
            len(intersection)
            / len(union)
        )

        return (
            0.65 * sequence_score
            + 0.35 * jaccard
        )

    # =========================================================
    # TYPE
    # =========================================================

    @staticmethod
    def type_compatible(
        candidate_type: MemoryType,
        memory_type: MemoryType,
    ) -> bool:

        if candidate_type == memory_type:
            return True

        compatible_groups = [
            {
                MemoryType.FACT,
                MemoryType.STATE,
            },
            {
                MemoryType.PREFERENCE,
            },
        ]

        for group in compatible_groups:

            if (
                candidate_type in group
                and memory_type in group
            ):
                return True

        return False

    # =========================================================
    # USER
    # =========================================================

    @staticmethod
    def same_user(
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> bool:

        return (
            candidate.candidate.user_id
            == memory.user_id
        )

    # =========================================================
    # RELATION SIMILARITY
    # =========================================================

    @classmethod
    def relation_similarity(
        cls,
        candidate_relation: SemanticRelation,
        memory_relation: SemanticRelation,
    ) -> float:

        subject_score = cls.text_similarity(
            candidate_relation.subject,
            memory_relation.subject,
        )

        predicate_score = cls.text_similarity(
            candidate_relation.predicate,
            memory_relation.predicate,
        )

        object_score = cls.text_similarity(
            candidate_relation.object,
            memory_relation.object,
        )

        return (
            0.20 * subject_score
            + 0.35 * predicate_score
            + 0.45 * object_score
        )

    @classmethod
    def best_relation_similarity(
        cls,
        candidate_relations: list[
            SemanticRelation
        ],
        memory_relations: list[
            SemanticRelation
        ],
    ) -> float:

        if (
            not candidate_relations
            or not memory_relations
        ):
            return 0.0

        scores = []

        for candidate_relation in (
            candidate_relations
        ):

            best = 0.0

            for memory_relation in (
                memory_relations
            ):

                similarity = (
                    cls.relation_similarity(
                        candidate_relation,
                        memory_relation,
                    )
                )

                best = max(
                    best,
                    similarity,
                )

            scores.append(
                best
            )

        return (
            sum(scores)
            / len(scores)
        )

    # =========================================================
    # HARD STRUCTURAL INFORMATION
    # =========================================================

    @staticmethod
    def has_same_subject_and_predicate(
        candidate: SemanticMemory,
        memory: SemanticMemory,
    ) -> bool:

        if (
            not candidate.subject
            or not memory.subject
        ):
            return False

        if (
            not candidate.predicate
            or not memory.predicate
        ):
            return False

        return (
            candidate.subject
            == memory.subject
            and candidate.predicate
            == memory.predicate
        )

    # =========================================================
    # OBJECT DIFFERENCE
    # =========================================================

    @staticmethod
    def has_different_object(
        candidate: SemanticMemory,
        memory: SemanticMemory,
    ) -> bool:

        if (
            not candidate.object
            or not memory.object
        ):
            return False

        return (
            candidate.object
            != memory.object
        )

    # =========================================================
    # SCORE
    # =========================================================

    def score(
        self,
        candidate: EnrichedMemoryCandidate,
        memory: Memory,
    ) -> MemoryMatch | None:

        # -----------------------------------------------------
        # User
        # -----------------------------------------------------

        if not self.same_user(
            candidate,
            memory,
        ):
            return None

        # -----------------------------------------------------
        # Goal completion matches existing goal explicitly.
        # -----------------------------------------------------
        candidate_metadata = candidate.candidate.metadata or {}

        if (
            candidate_metadata.get("event_type")
            == "goal_completion"
            and memory.type == MemoryType.GOAL
        ):
            target = str(
                candidate_metadata.get(
                    "target_goal",
                    "",
                )
            ).strip()

            candidate_text = self.normalize(
                candidate.candidate.content
            )

            goal_text = self.normalize(
                memory.content
            )

            # 1. Explicit target_goal, when available.
            if target:
                target_normalized = self.normalize(
                    target
                )

                target_words = set(
                    re.findall(
                        r"[а-яёa-z0-9]+",
                        target_normalized,
                    )
                )

                goal_words = set(
                    re.findall(
                        r"[а-яёa-z0-9]+",
                        goal_text,
                    )
                )

                overlap = len(
                    target_words & goal_words
                )

                if (
                    overlap >= 1
                    or target_normalized in goal_text
                ):
                    return MemoryMatch(
                        memory=memory,
                        score=0.95,
                        evidence=[
                            "goal_completion",
                            "goal_target_match",
                        ],
                    )

            # 2. Fallback to completion candidate text.
            completion_words = set(
                re.findall(
                    r"[а-яёa-z0-9]+",
                    candidate_text,
                )
            )

            goal_words = set(
                re.findall(
                    r"[а-яёa-z0-9]+",
                    goal_text,
                )
            )

            stop_words = {
                "пользователь",
                "цель",
                "уже",
                "закончил",
                "закончила",
                "закончило",
                "закончили",
                "завершил",
                "завершила",
                "завершило",
                "завершили",
                "выполнил",
                "выполнила",
                "выполнило",
                "выполнили",
                "сделал",
                "сделала",
                "сделало",
                "сделали",
                "завершена",
                "завершен",
                "завершено",
                "завершены",
            }

            completion_words -= stop_words

            overlap_words = (
                completion_words & goal_words
            )

            if overlap_words:
                return MemoryMatch(
                    memory=memory,
                    score=0.95,
                    evidence=[
                        "goal_completion",
                        "completion_text_match",
                    ],
                )

        # -----------------------------------------------------
        # Type
        # -----------------------------------------------------

        if not self.type_compatible(
            candidate.candidate.type,
            memory.type,
        ):
            return None

        # -----------------------------------------------------
        # Semantic normalization
        # -----------------------------------------------------

        candidate_semantic = (
            self.normalizer.from_candidate(
                candidate
            )
        )

        memory_semantic = (
            self.normalizer.from_memory(
                memory
            )
        )

        # =====================================================
        # SUBJECT
        # =====================================================

        if (
            candidate_semantic.subject
            and memory_semantic.subject
            and
            candidate_semantic.subject
            != memory_semantic.subject
        ):

            return None

        # =====================================================
        # RELATIONS
        # =====================================================

        relation_score = (
            self.best_relation_similarity(
                candidate_semantic.relations,
                memory_semantic.relations,
            )
        )

        # =====================================================
        # TEXT
        # =====================================================

        text_score = self.text_similarity(
            candidate.candidate.content,
            memory.content,
        )

        # =====================================================
        # SAME SUBJECT + PREDICATE
        # =====================================================

        same_subject_predicate = (
            self.has_same_subject_and_predicate(
                candidate_semantic,
                memory_semantic,
            )
        )

        different_object = (
            self.has_different_object(
                candidate_semantic,
                memory_semantic,
            )
        )

        # =====================================================
        # SAME PROPERTY + DIFFERENT VALUE
        #
        # Strong semantic match for evolving properties.
        # This allows the consolidator to produce SUPERSEDE.
        # =====================================================

        if (
            same_subject_predicate
            and candidate_semantic.object
            and memory_semantic.object
        ):
            if different_object:
                return MemoryMatch(
                    memory=memory,
                    score=0.88,
                    evidence=[
                        "same_subject_predicate",
                        "different_object",
                    ],
                )

            return MemoryMatch(
                memory=memory,
                score=0.97,
                evidence=[
                    "same_subject_predicate",
                    "same_semantic_value",
                ],
            )

        # =====================================================
        # IMPORTANT CASE:
        #
        # same semantic property,
        # different value
        #
        # Москва vs Санкт-Петербург
        #
        # This is a VALID MATCH.
        # Consolidator will decide SUPERSEDE.
        # =====================================================

        semantic_state_match = (
            same_subject_predicate
            and bool(
                candidate_semantic.object
            )
            and bool(
                memory_semantic.object
            )
        )

        # =====================================================
        # SCORE
        # =====================================================

        score = 0.0

        # -----------------------------------------------------
        # Strong structural match
        # -----------------------------------------------------

        if relation_score > 0:

            score += (
                0.55
                * relation_score
            )

        else:

            score += (
                0.20
                * text_score
            )

        # -----------------------------------------------------
        # Text
        # -----------------------------------------------------

        score += (
            0.20
            * text_score
        )

        # -----------------------------------------------------
        # Exact semantic property
        # -----------------------------------------------------

        if semantic_state_match:

            score += 0.30

        # -----------------------------------------------------
        # Same object
        # -----------------------------------------------------

        if (
            same_subject_predicate
            and not different_object
        ):

            score += 0.20

        score = min(
            1.0,
            score,
        )

        # =====================================================
        # EVIDENCE
        # =====================================================

        evidence: list[str] = []

        if text_score >= 0.85:

            evidence.append(
                "high_text_similarity"
            )

        elif text_score >= 0.65:

            evidence.append(
                "text_similarity"
            )

        if relation_score >= 0.85:

            evidence.append(
                "strong_relation_match"
            )

        elif relation_score >= 0.65:

            evidence.append(
                "relation_match"
            )

        if same_subject_predicate:

            evidence.append(
                "same_subject_predicate"
            )

        if different_object:

            evidence.append(
                "different_object"
            )

        if (
            same_subject_predicate
            and not different_object
        ):

            evidence.append(
                "same_semantic_value"
            )

        # =====================================================
        # FINAL DECISION
        # =====================================================

        # Same subject + predicate itself is enough
        # to consider a state/property candidate
        # a meaningful match.

        if (
            semantic_state_match
            and score >= 0.65
        ):

            return MemoryMatch(
                memory=memory,
                score=score,
                evidence=evidence,
            )

        if score < self.min_score:

            return None

        return MemoryMatch(
            memory=memory,
            score=score,
            evidence=evidence,
        )

    # =========================================================
    # FIND
    # =========================================================

    def find_matches(
        self,
        candidate: EnrichedMemoryCandidate,
    ) -> list[MemoryMatch]:

        matches: list[
            MemoryMatch
        ] = []

        memories = self.store.find_by_user(
            user_id=(
                candidate.candidate.user_id
            ),
            active_only=True,
        )

        for memory in memories:

            match = self.score(
                candidate=candidate,
                memory=memory,
            )

            if match is None:
                continue

            matches.append(
                match
            )

        matches.sort(
            key=lambda match: match.score,
            reverse=True,
        )

        return matches