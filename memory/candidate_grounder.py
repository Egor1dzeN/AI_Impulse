from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from models import MemoryCandidate, MemoryType
from entity_enricher import Entity


# ============================================================
# DATA MODEL
# ============================================================

@dataclass
class GroundedCandidate:
    candidate: MemoryCandidate

    source_text: str

    start: int
    end: int

    confidence: float

    evidence: str


# ============================================================
# CANDIDATE GROUNDER
# ============================================================

class CandidateGrounder:

    # ========================================================
    # NORMALIZE
    # ========================================================

    @staticmethod
    def normalize(text: str) -> str:

        text = text.lower()

        text = text.replace(
            "ё",
            "е",
        )

        text = re.sub(
            r"[^\w\s]",
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
    # COMPACT NORMALIZE
    # ========================================================

    @classmethod
    def compact_normalize(
        cls,
        text: str,
    ) -> str:

        return re.sub(
            r"[\s\-_]",
            "",
            cls.normalize(text),
        )

    # ========================================================
    # SENTENCES
    # ========================================================

    @staticmethod
    def split_sentences(
        text: str,
    ) -> list[tuple[str, int, int]]:

        result = []

        pattern = re.compile(
            r"[^.!?]+(?:[.!?]+|$)",
            re.UNICODE,
        )

        for match in pattern.finditer(text):

            raw = match.group(0)

            leading = len(
                raw
            ) - len(
                raw.lstrip()
            )

            start = (
                match.start()
                + leading
            )

            end = match.end()

            while (
                end > start
                and text[end - 1].isspace()
            ):
                end -= 1

            sentence = text[
                start:end
            ].strip()

            if sentence:

                result.append(
                    (
                        sentence,
                        start,
                        end,
                    )
                )

        return result

    # ========================================================
    # EXACT CONTENT
    # ========================================================

    def _find_exact_content(
        self,
        candidate: MemoryCandidate,
    ) -> GroundedCandidate | None:

        source = candidate.source_text

        content = candidate.content.strip()

        if not content:
            return None

        start = source.find(
            content
        )

        if start == -1:
            return None

        end = (
            start
            + len(content)
        )

        return GroundedCandidate(
            candidate=candidate,
            source_text=source[
                start:end
            ],
            start=start,
            end=end,
            confidence=1.0,
            evidence="exact_content",
        )

    # ========================================================
    # FUZZY ENTITY MATCH
    # ========================================================

    def _find_entity_match(
        self,
        candidate: MemoryCandidate,
        entities: list[Entity],
    ) -> GroundedCandidate | None:

        source = candidate.source_text

        candidate_normalized = (
            self.compact_normalize(
                candidate.content
            )
        )

        if not candidate_normalized:
            return None

        best_entity = None
        best_score = 0.0

        for entity in entities:

            entity_normalized = (
                self.compact_normalize(
                    entity.text
                )
            )

            if not entity_normalized:
                continue

            # ------------------------------------------------
            # Exact normalized match
            # ------------------------------------------------

            if (
                candidate_normalized
                == entity_normalized
            ):

                score = 1.0

            else:

                # ------------------------------------------------
                # Fuzzy matching handles:
                #
                # Санкт-Петербург
                # Санкт-Петербурге
                #
                # Москва
                # Москве
                # ------------------------------------------------

                score = SequenceMatcher(
                    None,
                    candidate_normalized,
                    entity_normalized,
                ).ratio()

            if score > best_score:

                best_score = score
                best_entity = entity

        if best_entity is None:
            return None

        # Don't accept weak matches.
        if best_score < 0.72:
            return None

        start = best_entity.start
        end = best_entity.end

        return GroundedCandidate(
            candidate=candidate,
            source_text=source[
                start:end
            ],
            start=start,
            end=end,
            confidence=min(
                0.95,
                best_score,
            ),
            evidence="fuzzy_entity_match",
        )

    # ========================================================
    # SENTENCE MATCH
    # ========================================================

    def _find_sentence_match(
        self,
        candidate: MemoryCandidate,
    ) -> GroundedCandidate | None:

        source = candidate.source_text

        candidate_normalized = (
            self.normalize(
                candidate.content
            )
        )

        candidate_words = set(
            candidate_normalized.split()
        )

        if not candidate_words:
            return None

        best = None
        best_score = 0.0

        for (
            sentence,
            start,
            end,
        ) in self.split_sentences(source):

            sentence_normalized = (
                self.normalize(
                    sentence
                )
            )

            sentence_words = set(
                sentence_normalized.split()
            )

            if not sentence_words:
                continue

            intersection = (
                candidate_words
                & sentence_words
            )

            # ------------------------------------------------
            # Word coverage.
            # ------------------------------------------------

            coverage = (
                len(intersection)
                / len(candidate_words)
            )

            # ------------------------------------------------
            # Additional fuzzy similarity.
            # ------------------------------------------------

            similarity = SequenceMatcher(
                None,
                candidate_normalized,
                sentence_normalized,
            ).ratio()

            score = (
                0.7 * coverage
                + 0.3 * similarity
            )

            if score > best_score:

                best_score = score

                best = (
                    sentence,
                    start,
                    end,
                )

        if best is None:
            return None

        sentence, start, end = best

        if best_score < 0.45:
            return None

        return GroundedCandidate(
            candidate=candidate,
            source_text=sentence,
            start=start,
            end=end,
            confidence=min(
                0.9,
                best_score,
            ),
            evidence="sentence_match",
        )

    # ========================================================
    # FALLBACK
    # ========================================================

    def _fallback(
        self,
        candidate: MemoryCandidate,
    ) -> GroundedCandidate:

        source = candidate.source_text

        return GroundedCandidate(
            candidate=candidate,
            source_text=source,
            start=0,
            end=len(source),
            confidence=0.1,
            evidence="fallback",
        )

    # ========================================================
    # PUBLIC API
    # ========================================================

    def ground(
        self,
        candidate: MemoryCandidate,
        entities: list[Entity],
    ) -> GroundedCandidate:

        # ----------------------------------------------------
        # 1. Exact content always has highest priority.
        # ----------------------------------------------------

        exact = self._find_exact_content(
            candidate
        )

        if exact is not None:
            return exact

        # ----------------------------------------------------
        # ENTITY candidates:
        #
        # For these we deliberately prefer entity matching.
        #
        # "OMODA C5"
        # "Иван"
        # "Санкт-Петербург"
        # ----------------------------------------------------

        if candidate.type == MemoryType.ENTITY:

            entity_match = (
                self._find_entity_match(
                    candidate,
                    entities,
                )
            )

            if entity_match is not None:
                return entity_match

        # ----------------------------------------------------
        # Other candidate types:
        #
        # Prefer sentence grounding.
        #
        # This prevents:
        #
        # "Пользователь предпочитает работать вечером"
        #
        # from being grounded only to "вечером".
        # ----------------------------------------------------

        sentence_match = (
            self._find_sentence_match(
                candidate
            )
        )

        if sentence_match is not None:

            return sentence_match

        # ----------------------------------------------------
        # Entity fallback
        # ----------------------------------------------------

        entity_match = (
            self._find_entity_match(
                candidate,
                entities,
            )
        )

        if entity_match is not None:
            return entity_match

        # ----------------------------------------------------
        # Fallback
        # ----------------------------------------------------

        return self._fallback(
            candidate
        )