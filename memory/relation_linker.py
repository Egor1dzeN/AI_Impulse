from __future__ import annotations

from dataclasses import dataclass
import re

from candidate_grounder import GroundedCandidate
from coreference import CoreferenceCluster
from entity_enricher import Entity
from models import MemoryCandidate, MemoryType
from relation_extractor import Relation


@dataclass
class LinkedRelation:
    relation: Relation
    confidence: float
    evidence: str


class RelationLinker:

    # =========================================================
    # LEXICAL HINTS
    # =========================================================

    PREDICATE_HINTS = {
        "быть_братом": {
            "брат",
            "брата",
            "братом",
        },
        "быть_сестрой": {
            "сестра",
            "сестры",
            "сестрой",
        },
        "быть_коллегой": {
            "коллега",
            "коллеги",
            "коллегой",
        },
        "быть_отцом": {
            "отец",
            "отцом",
        },
        "быть_матерью": {
            "мать",
            "матерью",
        },
        "быть_другом": {
            "друг",
            "друга",
            "другом",
        },
        "быть_мужем": {
            "муж",
            "мужа",
            "мужем",
        },
        "быть_женой": {
            "жена",
            "жены",
            "женой",
        },
        "быть_владельцем": {
            "владелец",
            "владельца",
            "владельцем",
        },
        "жить_в": {
            "живет",
            "жить",
            "проживает",
            "проживать",
            "житель",
        },
        "работать_как": {
            "работает",
            "работать",
            "программист",
            "инженер",
            "врач",
            "менеджер",
            "профессия",
        },
        "предпочитать": {
            "предпочитаю",
            "предпочитает",
            "предпочитать",
        },
        "планировать": {
            "планирую",
            "планирует",
            "планировать",
        },
        "купить": {
            "купил",
            "купила",
            "купить",
            "покупать",
        },
        "нравиться": {
            "нравится",
            "нравиться",
        },
        "рассказать_о": {
            "рассказал",
            "рассказать",
            "рассказывает",
        },
        "встретиться_с": {
            "встретился",
            "встретилась",
            "встретиться",
            "встречаться",
        },
    }

    # =========================================================
    # NORMALIZATION
    # =========================================================

    @staticmethod
    def _normalize(text: str) -> str:

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

    @classmethod
    def _words(
        cls,
        text: str,
    ) -> set[str]:

        return set(
            cls._normalize(text).split()
        )

    # =========================================================
    # COREFERENCE
    # =========================================================

    def _resolve_coreference(
        self,
        value: str,
        clusters: list[CoreferenceCluster],
    ) -> tuple[str, str] | None:

        value_normalized = self._normalize(
            value
        )

        for cluster in clusters:

            representative = (
                cluster.representative.text
            )

            for mention in cluster.mentions:

                mention_normalized = (
                    self._normalize(
                        mention.text
                    )
                )

                if (
                    mention_normalized
                    == value_normalized
                ):

                    return (
                        representative,
                        "coreference",
                    )

        return None

    # =========================================================
    # ENTITY
    # =========================================================

    def _resolve_entity(
        self,
        value: str,
        entities: list[Entity],
    ) -> tuple[str, str | None] | None:

        value_normalized = self._normalize(
            value
        )

        for entity in entities:

            entity_normalized = self._normalize(
                entity.text
            )

            if (
                value_normalized
                == entity_normalized
            ):

                return (
                    entity.text,
                    entity.label,
                )

        return None

    # =========================================================
    # CANONICAL VALUE FROM CANDIDATE CONTENT
    # =========================================================

    @staticmethod
    def _is_pronoun(value: str) -> bool:
        return value.strip().lower() in {
            "он", "она", "они",
            "его", "её", "ее", "ему", "ей", "им", "ими",
            "ним", "ней", "ними",
        }

    def _resolve_pronoun_from_candidate(
        self,
        value: str,
        candidate: MemoryCandidate,
        entities: list[Entity],
    ) -> tuple[str, str | None] | None:
        if not self._is_pronoun(value):
            return None

        content = self._normalize(candidate.content)

        candidates: list[tuple[int, Entity]] = []
        for entity in entities:
            if entity.label not in {"person", "organization", "location", "object"}:
                continue

            normalized = self._normalize(entity.text)
            if not normalized or normalized not in content:
                continue

            # Do not resolve a pronoun to the generic user marker.
            if normalized == "пользователь":
                continue

            candidates.append((len(normalized.split()), entity))

        if len(candidates) == 1:
            _, entity = candidates[0]
            return entity.text, entity.label

        # A single proper-name token in canonical content is a safe
        # fallback when NER did not return the entity.
        proper_names = re.findall(
            r"\b[А-ЯЁ][а-яё-]+\b",
            candidate.content,
        )
        filtered = [
            value
            for value in proper_names
            if self._normalize(value) != "пользователь"
        ]

        unique = []
        for value in filtered:
            if self._normalize(value) not in {
                self._normalize(item) for item in unique
            }:
                unique.append(value)

        if len(unique) == 1:
            return unique[0], "person"

        return None

    # =========================================================
    # RELATION NORMALIZATION
    # =========================================================

    def normalize_relation(
        self,
        relation: Relation,
        candidate: MemoryCandidate,
        entities: list[Entity],
        coreference_clusters: list[CoreferenceCluster],
    ) -> Relation:

        subject = relation.subject
        object_value = relation.object

        subject_label = (
            relation.subject_entity_label
        )

        object_label = (
            relation.object_entity_label
        )

        metadata = dict(
            relation.metadata or {}
        )

        evidence = []

        # -----------------------------------------------------
        # Subject coreference
        # -----------------------------------------------------

        resolved_subject = (
            self._resolve_coreference(
                subject,
                coreference_clusters,
            )
        )

        if resolved_subject is not None:

            subject, reason = resolved_subject

            evidence.append(
                reason
            )

        # -----------------------------------------------------
        # Object coreference
        # -----------------------------------------------------

        resolved_object = (
            self._resolve_coreference(
                object_value,
                coreference_clusters,
            )
        )

        if resolved_object is not None:

            object_value, reason = (
                resolved_object
            )

            evidence.append(
                reason
            )

        # -----------------------------------------------------
        # Canonical candidate content
        # -----------------------------------------------------

        resolved_candidate_object = (
            self._resolve_pronoun_from_candidate(
                value=object_value,
                candidate=candidate,
                entities=entities,
            )
        )

        if resolved_candidate_object is not None:
            object_value, object_label = resolved_candidate_object
            evidence.append(
                "candidate_content"
            )

        resolved_candidate_subject = (
            self._resolve_pronoun_from_candidate(
                value=subject,
                candidate=candidate,
                entities=entities,
            )
        )

        if resolved_candidate_subject is not None:
            subject, subject_label = resolved_candidate_subject
            evidence.append(
                "candidate_content_subject"
            )

        # -----------------------------------------------------
        # User object
        # -----------------------------------------------------

        user_objects = {
            "я",
            "мне",
            "меня",
            "мой",
            "моя",
            "моего",
            "моей",
            "мою",
            "пользователь",
        }

        if (
            self._normalize(object_value)
            in user_objects
        ):

            object_value = "пользователь"
            object_label = None

            evidence.append(
                "user_object"
            )

        # -----------------------------------------------------
        # Entity normalization
        # -----------------------------------------------------

        resolved_subject_entity = (
            self._resolve_entity(
                subject,
                entities,
            )
        )

        if resolved_subject_entity is not None:

            subject, subject_label = (
                resolved_subject_entity
            )

        resolved_object_entity = (
            self._resolve_entity(
                object_value,
                entities,
            )
        )

        if resolved_object_entity is not None:

            object_value, object_label = (
                resolved_object_entity
            )

        metadata[
            "normalization_evidence"
        ] = evidence

        return Relation(
            id=relation.id,
            subject=subject,
            predicate=relation.predicate,
            object=object_value,
            confidence=relation.confidence,
            subject_entity_label=subject_label,
            object_entity_label=object_label,
            temporal_value=relation.temporal_value,
            temporal_text=relation.temporal_text,
            source_text=relation.source_text,
            source_fragment=relation.source_fragment,
            metadata=metadata,
        )

    # =========================================================
    # PREDICATE MATCH
    # =========================================================

    def _predicate_matches_candidate(
        self,
        relation: Relation,
        candidate: MemoryCandidate,
    ) -> bool:

        candidate_words = self._words(
            candidate.content
        )

        hints = self.PREDICATE_HINTS.get(
            relation.predicate.lower(),
            set(),
        )

        if not hints:
            return False

        normalized_hints = {
            self._normalize(hint)
            for hint in hints
        }

        return bool(
            candidate_words
            & normalized_hints
        )

    # =========================================================
    # OBJECT MATCH
    # =========================================================

    def _object_matches_candidate(
        self,
        relation: Relation,
        candidate: MemoryCandidate,
    ) -> bool:

        candidate_words = self._words(
            candidate.content
        )

        object_words = self._words(
            relation.object
        )

        if not object_words:
            return False

        return bool(
            candidate_words
            & object_words
        )

    # =========================================================
    # SUBJECT MATCH
    # =========================================================

    def _subject_matches_candidate(
        self,
        relation: Relation,
        candidate: MemoryCandidate,
    ) -> bool:

        candidate_text = self._normalize(
            candidate.content
        )

        subject = self._normalize(
            relation.subject
        )

        if not subject:
            return False

        return subject in candidate_text

    # =========================================================
    # SOURCE FRAGMENT MATCH
    # =========================================================

    def _same_source_fragment(
        self,
        relation: Relation,
        grounded: GroundedCandidate,
    ) -> bool:

        relation_fragment = self._normalize(
            relation.source_fragment
        )

        grounded_fragment = self._normalize(
            grounded.source_text
        )

        if not relation_fragment:
            return False

        if (
            relation_fragment
            == grounded_fragment
        ):
            return True

        if (
            relation_fragment
            in grounded_fragment
        ):
            return True

        if (
            grounded_fragment
            in relation_fragment
        ):
            return True

        return False

    # =========================================================
    # RELATION -> CANDIDATE SCORE
    # =========================================================

    def _score(
        self,
        relation: Relation,
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
    ) -> tuple[float, list[str]]:

        score = 0.0

        evidence: list[str] = []

        same_source = (
            self._same_source_fragment(
                relation,
                grounded,
            )
        )

        predicate_match = (
            self._predicate_matches_candidate(
                relation,
                candidate,
            )
        )

        object_match = (
            self._object_matches_candidate(
                relation,
                candidate,
            )
        )

        subject_match = (
            self._subject_matches_candidate(
                relation,
                candidate,
            )
        )

        # =====================================================
        # CANDIDATE TYPE: RELATIONSHIP
        # =====================================================

        if candidate.type == MemoryType.RELATIONSHIP:

            # Для relationship candidate
            # predicate/object должны быть релевантны.

            if predicate_match:
                score += 0.45
                evidence.append(
                    "predicate_hint"
                )

            if object_match:
                score += 0.20
                evidence.append(
                    "object_in_candidate"
                )

            if subject_match:
                score += 0.20
                evidence.append(
                    "subject_in_candidate"
                )

            if same_source:
                score += 0.15
                evidence.append(
                    "same_source_fragment"
                )

            # Без predicate/object совпадения
            # relation не проходит.

            if not predicate_match and not object_match:
                return 0.0, []

        # =====================================================
        # CANDIDATE TYPE: STATE
        # =====================================================

        elif candidate.type == MemoryType.STATE:

            if predicate_match:
                score += 0.45
                evidence.append(
                    "predicate_hint"
                )

            if object_match:
                score += 0.20
                evidence.append(
                    "object_in_candidate"
                )

            if subject_match:
                score += 0.20
                evidence.append(
                    "subject_in_candidate"
                )

            if same_source:
                score += 0.15
                evidence.append(
                    "same_source_fragment"
                )

            if not predicate_match:
                return 0.0, []

        # =====================================================
        # CANDIDATE TYPE: PREFERENCE
        # =====================================================

        elif candidate.type == MemoryType.PREFERENCE:

            if predicate_match:
                score += 0.55
                evidence.append(
                    "predicate_hint"
                )

            if object_match:
                score += 0.20
                evidence.append(
                    "object_in_candidate"
                )

            if same_source:
                score += 0.15
                evidence.append(
                    "same_source_fragment"
                )

            if not predicate_match:
                return 0.0, []

        # =====================================================
        # CANDIDATE TYPE: GOAL
        # =====================================================

        elif candidate.type == MemoryType.GOAL:

            if predicate_match:
                score += 0.55
                evidence.append(
                    "predicate_hint"
                )

            if object_match:
                score += 0.20
                evidence.append(
                    "object_in_candidate"
                )

            if same_source:
                score += 0.15
                evidence.append(
                    "same_source_fragment"
                )

            if not predicate_match:
                return 0.0, []

        # =====================================================
        # CANDIDATE TYPE: EPISODE
        # =====================================================

        elif candidate.type == MemoryType.EPISODE:

            # Episode описывает весь фрагмент.
            # Поэтому несколько relations допустимы.

            if same_source:
                score += 0.55
                evidence.append(
                    "same_source_fragment"
                )

            if subject_match:
                score += 0.15
                evidence.append(
                    "subject_in_candidate"
                )

            if object_match:
                score += 0.15
                evidence.append(
                    "object_in_candidate"
                )

            if predicate_match:
                score += 0.15
                evidence.append(
                    "predicate_hint"
                )

        # =====================================================
        # CANDIDATE TYPE: ENTITY
        # =====================================================

        elif candidate.type == MemoryType.ENTITY:

            # Для entity relation должна быть явно
            # связана с этой сущностью.

            if object_match:
                score += 0.50
                evidence.append(
                    "object_in_candidate"
                )

            if subject_match:
                score += 0.30
                evidence.append(
                    "subject_in_candidate"
                )

            if same_source:
                score += 0.10
                evidence.append(
                    "same_source_fragment"
                )

        # =====================================================
        # OTHER TYPES
        # =====================================================

        else:

            if predicate_match:
                score += 0.35
                evidence.append(
                    "predicate_hint"
                )

            if object_match:
                score += 0.25
                evidence.append(
                    "object_in_candidate"
                )

            if subject_match:
                score += 0.20
                evidence.append(
                    "subject_in_candidate"
                )

            if same_source:
                score += 0.20
                evidence.append(
                    "same_source_fragment"
                )

        return score, evidence

    # =========================================================
    # PUBLIC
    # =========================================================

    def link(
        self,
        relation: Relation,
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
        coreference_clusters: list[CoreferenceCluster],
        entities: list[Entity],
    ) -> LinkedRelation | None:

        normalized_relation = (
            self.normalize_relation(
                relation=relation,
                candidate=candidate,
                entities=entities,
                coreference_clusters=coreference_clusters,
            )
        )

        score, evidence = self._score(
            relation=normalized_relation,
            candidate=candidate,
            grounded=grounded,
        )

        if score < 0.60:
            return None

        confidence = min(
            1.0,
            0.5 * normalized_relation.confidence
            + 0.5 * score,
        )

        metadata = dict(
            normalized_relation.metadata or {}
        )

        metadata[
            "link_confidence"
        ] = confidence

        metadata[
            "link_evidence"
        ] = evidence

        normalized_relation = Relation(
            id=normalized_relation.id,
            subject=normalized_relation.subject,
            predicate=normalized_relation.predicate,
            object=normalized_relation.object,
            confidence=normalized_relation.confidence,
            subject_entity_label=(
                normalized_relation.subject_entity_label
            ),
            object_entity_label=(
                normalized_relation.object_entity_label
            ),
            temporal_value=(
                normalized_relation.temporal_value
            ),
            temporal_text=(
                normalized_relation.temporal_text
            ),
            source_text=(
                normalized_relation.source_text
            ),
            source_fragment=(
                normalized_relation.source_fragment
            ),
            metadata=metadata,
        )

        return LinkedRelation(
            relation=normalized_relation,
            confidence=confidence,
            evidence=",".join(
                evidence
            ),
        )