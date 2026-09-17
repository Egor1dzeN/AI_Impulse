from __future__ import annotations

import re
from dataclasses import dataclass

from models import (
    MemoryCandidate,
    MemoryType,
)

from entity_enricher import Entity

from coreference import (
    CoreferenceCluster,
)

from candidate_grounder import (
    GroundedCandidate,
)

from memory_context import MemoryContext


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class ResolvedSubject:
    text: str

    start: int
    end: int

    entity_label: str | None

    is_user: bool

    evidence: str

    confidence: float


# ============================================================
# SUBJECT RESOLVER
# ============================================================

class SubjectResolver:

    USER_SUBJECT_TYPES = {
        MemoryType.FACT,
        MemoryType.PREFERENCE,
        MemoryType.GOAL,
        MemoryType.STATE,
        MemoryType.PROCEDURE,
    }

    # ========================================================
    # NORMALIZE
    # ========================================================

    @staticmethod
    def _normalize(
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

    # ========================================================
    # USER PRONOUN IN SPAN
    # ========================================================

    @staticmethod
    def _find_user_pronoun(
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
    ) -> ResolvedSubject | None:

        source = candidate.source_text

        # ----------------------------------------------------
        # Ищем только внутри grounded span.
        # ----------------------------------------------------

        span_text = source[
            grounded.start:grounded.end
        ]

        pattern = re.compile(
            r"\b(я|мне|меня)\b",
            re.IGNORECASE,
        )

        match = pattern.search(
            span_text
        )

        if match is None:
            return None

        start = (
            grounded.start
            + match.start()
        )

        end = (
            grounded.start
            + match.end()
        )

        return ResolvedSubject(
            text="Пользователь",
            start=start,
            end=end,
            entity_label=None,
            is_user=True,
            evidence="user_marker",
            confidence=0.98,
        )

    # ========================================================
    # EXPLICIT USER
    # ========================================================

    def _resolve_user_subject(
        self,
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
    ) -> ResolvedSubject | None:

        candidate_text = self._normalize(
            candidate.content
        )

        # ----------------------------------------------------
        # "Пользователь ..."
        # ----------------------------------------------------

        if candidate_text.startswith(
            "пользователь"
        ):

            return ResolvedSubject(
                text="Пользователь",
                start=-1,
                end=-1,
                entity_label=None,
                is_user=True,
                evidence="user_marker",
                confidence=0.95,
            )

        # ----------------------------------------------------
        # Direct "я ..."
        # ----------------------------------------------------

        if re.match(
            r"^я\b",
            candidate_text,
        ):

            result = self._find_user_pronoun(
                candidate,
                grounded,
            )

            if result is not None:
                return result

        # ----------------------------------------------------
        # User-oriented memory types:
        #
        # Даже если Qwen переписал:
        #
        # "Я переехал..."
        #
        # как:
        #
        # "переехал..."
        #
        # проверяем оригинальный grounded span.
        # ----------------------------------------------------

        if candidate.type in (
            self.USER_SUBJECT_TYPES
        ):

            result = self._find_user_pronoun(
                candidate,
                grounded,
            )

            if result is not None:
                return result

            # Для целей часто используется опущенный субъект:
            # "откладывать 50000 рублей",
            # "снизить вес до 75 кг",
            # "закончить отчёт".
            # Для GOAL это естественное значение "Пользователь".
            if candidate.type == MemoryType.GOAL:
                return ResolvedSubject(
                    text="Пользователь",
                    start=-1,
                    end=-1,
                    entity_label=None,
                    is_user=True,
                    evidence="implicit_user_subject",
                    confidence=0.85,
                )

        return None

    # ========================================================
    # COREFERENCE
    # ========================================================

    def _resolve_from_coreference(
        self,
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
        clusters: list[CoreferenceCluster],
    ) -> ResolvedSubject | None:

        if candidate.type == MemoryType.ENTITY:
            return None

        candidates = []

        for cluster in clusters:

            representative = (
                cluster.representative
            )

            score = 0

            # ------------------------------------------------
            # Representative inside grounded span
            # ------------------------------------------------

            if (
                representative.start
                >= grounded.start
                and representative.end
                <= grounded.end
            ):

                score += 5

            # ------------------------------------------------
            # Mention inside grounded span
            # ------------------------------------------------

            for mention in cluster.mentions:

                if (
                    mention.start
                    >= grounded.start
                    and mention.end
                    <= grounded.end
                ):

                    score += 3

            # ------------------------------------------------
            # Representative text in grounded text
            # ------------------------------------------------

            grounded_text = self._normalize(
                grounded.source_text
            )

            representative_text = (
                self._normalize(
                    representative.text
                )
            )

            if (
                representative_text
                and representative_text
                in grounded_text
            ):

                score += 3

            if score > 0:

                candidates.append(
                    (
                        score,
                        cluster,
                    )
                )

        if not candidates:
            return None

        _, best_cluster = max(
            candidates,
            key=lambda item: item[0],
        )

        representative = (
            best_cluster.representative
        )

        return ResolvedSubject(
            text=representative.text,
            start=representative.start,
            end=representative.end,
            entity_label="person",
            is_user=False,
            evidence="coreference",
            confidence=0.9,
        )

    # ========================================================
    # PERSON ENTITY
    # ========================================================

    def _resolve_from_person_entity(
        self,
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
        entities: list[Entity],
    ) -> ResolvedSubject | None:

        if candidate.type == MemoryType.ENTITY:
            return None

        matches = []

        for entity in entities:

            if entity.label != "person":
                continue

            overlap = (
                entity.start < grounded.end
                and entity.end > grounded.start
            )

            if overlap:

                matches.append(
                    entity
                )

        if not matches:
            return None

        selected = max(
            matches,
            key=lambda entity: (
                entity.score,
                len(entity.text),
            ),
        )

        return ResolvedSubject(
            text=selected.text,
            start=selected.start,
            end=selected.end,
            entity_label="person",
            is_user=False,
            evidence="entity",
            confidence=min(
                0.9,
                selected.score,
            ),
        )

    # ========================================================
    # CROSS-MESSAGE MEMORY CONTEXT
    # ========================================================

    @staticmethod
    def _looks_like_subject(
        entity: Entity,
        source: str,
    ) -> bool:
        """
        Conservative structural heuristic.

        A person entity is treated as a grammatical subject when it
        appears at the beginning of the clause or is not introduced
        by a preposition such as "с", "для", "к", "о", etc.
        """
        prefix = source[:entity.start].strip().lower()

        if not prefix:
            return True

        # Only look at the small grammatical window immediately
        # before the entity. These are grammatical prepositions,
        # not person/name-specific aliases.
        previous_words = prefix.split()[-2:]
        if not previous_words:
            return True

        if previous_words[-1] in {
            "с",
            "со",
            "о",
            "об",
            "для",
            "к",
            "ко",
            "у",
            "от",
            "из",
            "по",
            "на",
        }:
            return False

        # Avoid treating a predicative profession/role as the subject:
        # "Антон устроился работать аналитиком" may produce the entity
        # "аналитиком". The actual subject is resolved from the sentence
        # or cross-message context, not from the role noun.
        if previous_words[-1] in {
            "работать",
            "устроился",
            "устроилась",
            "стал",
            "стала",
            "является",
        }:
            return False

        return True

    @staticmethod
    def _person_name_from_value(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        parts = value.split()
        # "Коллега Сергей" -> "Сергей"; keep normal multiword names intact.
        if len(parts) == 2 and parts[-1][:1].isupper() and parts[0][:1].isupper():
            value = parts[-1]
        return value

    @staticmethod
    def _has_user_marker(text: str) -> bool:
        return bool(re.search(r"\b(я|мне|меня|мой|моя|моего|моей|мою)\b", text, re.IGNORECASE))

    @staticmethod
    def _has_third_person_pronoun(text: str) -> bool:
        return bool(re.search(r"\b(он|она|они|его|ее|её|ему|ей|им|ним|ней|них|него)\b", text, re.IGNORECASE))

    def _recent_person_from_context(self, context: MemoryContext) -> str | None:
        # Do not use the entity list from previous memories here.
        # GLiNER can mislabel locations/roles as persons (for example,
        # "Казани" or "аналитиком"), which then poisons antecedent resolution.
        # The previously resolved subject is a much stronger signal.
        for memory in reversed(context.memories):
            metadata = memory.metadata or {}
            subject = metadata.get("subject")
            if not isinstance(subject, str):
                continue

            value = self._person_name_from_value(subject)
            if not value or value.lower() == "пользователь":
                continue

            # Role + name: "Мой брат Антон" / "Коллега Сергей" -> "Антон" / "Сергей".
            parts = value.split()
            if len(parts) >= 2:
                capitalized = [part for part in parts if part[:1].isupper()]
                if capitalized:
                    return capitalized[-1]

            return value

        return None

    def _resolve_from_memory_context(
        self,
        candidate: MemoryCandidate,
        context: MemoryContext | None,
        entities: list[Entity],
    ) -> ResolvedSubject | None:
        source = candidate.source_text

        current_persons = [
            entity
            for entity in entities
            if entity.label == "person"
            and self._looks_like_subject(entity, source)
        ]

        # Prefer an explicit person at the beginning of the current
        # candidate. This is more reliable than GLiNER confidence when
        # several spans are accidentally labelled as people.
        normalized_content = self._normalize(candidate.content)
        for entity in sorted(current_persons, key=lambda item: item.start):
            normalized_entity = self._normalize(entity.text)
            if not normalized_entity:
                continue
            if normalized_content.startswith(normalized_entity + " ") or normalized_content == normalized_entity:
                return ResolvedSubject(
                    text=entity.text,
                    start=entity.start,
                    end=entity.end,
                    entity_label="person",
                    is_user=False,
                    evidence="explicit_leading_person",
                    confidence=min(0.96, entity.score),
                )

        # "Сергей написал мне" -> Сергей.
        if len(current_persons) == 1:
            entity = current_persons[0]
            return ResolvedSubject(
                text=entity.text,
                start=entity.start,
                end=entity.end,
                entity_label="person",
                is_user=False,
                evidence="current_person_subject",
                confidence=min(0.94, entity.score),
            )

        # "у меня встреча с научным руководителем" -> пользователь.
        if self._has_user_marker(source) and not self._has_third_person_pronoun(source):
            return ResolvedSubject(
                text="Пользователь",
                start=-1,
                end=-1,
                entity_label=None,
                is_user=True,
                evidence="explicit_user_structure",
                confidence=0.97,
            )

        # "Он ..." / "Она ..." -> latest explicit person from memory.
        if self._has_third_person_pronoun(source) and context is not None and context.memories:
            antecedent = self._recent_person_from_context(context)
            if antecedent:
                return ResolvedSubject(
                    text=antecedent,
                    start=-1,
                    end=-1,
                    entity_label="person",
                    is_user=False,
                    evidence="memory_context_coreference",
                    confidence=0.90,
                )

        # If all people in the current text are prepositional participants,
        # the fact/event itself is normally about the user.
        person_entities = [entity for entity in entities if entity.label == "person"]
        if person_entities and not current_persons and candidate.type in (
            MemoryType.STATE,
            MemoryType.EPISODE,
            MemoryType.FACT,
        ):
            return ResolvedSubject(
                text="Пользователь",
                start=-1,
                end=-1,
                entity_label=None,
                is_user=True,
                evidence="implicit_user_structure",
                confidence=0.80,
            )

        return None

    # ========================================================
    # MAIN
    # ========================================================

    def resolve(
        self,
        candidate: MemoryCandidate,
        grounded: GroundedCandidate,
        entities: list[Entity],
        coreference_clusters: list[
            CoreferenceCluster
        ],
        context: MemoryContext | None = None,
    ) -> ResolvedSubject | None:

        # ----------------------------------------------------
        # Entity itself doesn't require subject.
        # ----------------------------------------------------

        if candidate.type == MemoryType.ENTITY:
            return None

        # ----------------------------------------------------
        # 1. Current explicit person / cross-message context
        #
        # This must run BEFORE user-pronoun detection:
        # "Сергей написал мне" -> Сергей is the subject,
        # "Он сообщил" -> resolve "Он" from memory context.
        # ----------------------------------------------------

        context_subject = (
            self._resolve_from_memory_context(
                candidate,
                context,
                entities,
            )
        )

        if context_subject is not None:
            return context_subject

        # ----------------------------------------------------
        # 2. Explicit user subject
        # ----------------------------------------------------

        user_subject = (
            self._resolve_user_subject(
                candidate,
                grounded,
            )
        )

        if user_subject is not None:
            return user_subject

        # ----------------------------------------------------
        # 3. Same-message coreference
        # ----------------------------------------------------

        coreference_subject = (
            self._resolve_from_coreference(
                candidate,
                grounded,
                coreference_clusters,
            )
        )

        if coreference_subject is not None:
            return coreference_subject

        # ----------------------------------------------------
        # 4. Explicit person entity fallback
        # ----------------------------------------------------

        person_subject = (
            self._resolve_from_person_entity(
                candidate,
                grounded,
                entities,
            )
        )

        if person_subject is not None:
            return person_subject

        # ----------------------------------------------------
        # 5. Natural implicit user subject
        #
        # Goals, states, facts and episodes normally describe
        # the user when no other subject was identified.
        # ----------------------------------------------------

        if candidate.type in (
            MemoryType.GOAL,
            MemoryType.STATE,
            MemoryType.FACT,
            MemoryType.EPISODE,
        ):
            return ResolvedSubject(
                text="Пользователь",
                start=-1,
                end=-1,
                entity_label=None,
                is_user=True,
                evidence="implicit_user_subject",
                confidence=0.72,
            )

        return None
