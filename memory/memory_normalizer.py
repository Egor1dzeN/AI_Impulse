from __future__ import annotations

from dataclasses import dataclass
import re

from enricher import EnrichedMemoryCandidate
from models import Memory, MemoryType


@dataclass
class NormalizedMemory:
    subject: str | None
    predicate: str | None
    object: str | None
    type: MemoryType
    content: str


class MemoryNormalizer:

    # =========================================================
    # BASIC NORMALIZATION
    # =========================================================

    @staticmethod
    def normalize_text(text: str) -> str:
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
    # SUBJECT
    # =========================================================

    @classmethod
    def normalize_subject(
        cls,
        subject: str | None,
    ) -> str | None:

        if subject is None:
            return None

        value = cls.normalize_text(
            subject
        )

        if value in {
            "я",
            "мне",
            "меня",
            "пользователь",
        }:
            return "пользователь"

        return value or None

    # =========================================================
    # PREDICATE
    # =========================================================

    @classmethod
    def normalize_predicate(
        cls,
        predicate: str | None,
    ) -> str | None:

        if predicate is None:
            return None

        value = cls.normalize_text(
            predicate
        )

        # Частые варианты одной и той же связи
        aliases = {
            "жить": "жить_в",
            "живет": "жить_в",
            "проживать": "жить_в",
            "проживает": "жить_в",

            "работать": "работать_как",
            "работает": "работать_как",

            "любить": "любить",
            "нравиться": "нравиться",
            "предпочитать": "предпочитать",

            "планировать": "планировать",
        }

        return aliases.get(
            value,
            value or None,
        )

    # =========================================================
    # OBJECT
    # =========================================================

    @classmethod
    def normalize_object(
        cls,
        object_value: str | None,
    ) -> str | None:

        if object_value is None:
            return None

        value = cls.normalize_text(
            object_value
        )

        # -----------------------------------------------------
        # User references
        # -----------------------------------------------------

        if value in {
            "я",
            "мне",
            "меня",
            "мой",
            "моя",
            "моего",
            "моей",
            "мою",
            "пользователь",
        }:
            return "пользователь"

        # -----------------------------------------------------
        # Russian case normalization for common locations
        # -----------------------------------------------------

        location_aliases = {
            "москве": "москва",
            "москву": "москва",
            "санкт петербурге": "санкт петербург",
            "санкт петербургe": "санкт петербург",
            "санкт петербургом": "санкт петербург",
            "казани": "казань",
            "казань": "казань",
        }

        if value in location_aliases:
            return location_aliases[value]

        # -----------------------------------------------------
        # Common temporal / grammatical forms
        # -----------------------------------------------------

        aliases = {
            "вечером": "вечером",
            "по вечерам": "вечером",
            "утром": "утром",
            "по утрам": "утром",
            "ночью": "ночью",
            "по ночам": "ночью",
        }

        if value in aliases:
            return aliases[value]

        return value or None

    # =========================================================
    # FROM ENRICHED CANDIDATE
    # =========================================================

    def from_candidate(
        self,
        candidate: EnrichedMemoryCandidate,
    ) -> NormalizedMemory:

        subject = None
        predicate = None
        object_value = None

        # -----------------------------------------------------
        # Prefer structured relation
        # -----------------------------------------------------

        if candidate.relations:

            relation = candidate.relations[0]

            subject = self.normalize_subject(
                relation.subject
            )

            predicate = self.normalize_predicate(
                relation.predicate
            )

            object_value = self.normalize_object(
                relation.object
            )

        # -----------------------------------------------------
        # Fallback to resolved subject
        # -----------------------------------------------------

        elif candidate.subject is not None:

            subject = self.normalize_subject(
                candidate.subject.text
            )

        return NormalizedMemory(
            subject=subject,
            predicate=predicate,
            object=object_value,
            type=candidate.candidate.type,
            content=self.normalize_text(
                candidate.candidate.content
            ),
        )

    # =========================================================
    # FROM EXISTING MEMORY
    # =========================================================

    def from_memory(
        self,
        memory: Memory,
    ) -> NormalizedMemory:

        subject = self.normalize_subject(
            (memory.metadata or {}).get(
                "subject"
            )
        )

        predicate = None
        object_value = None

        relations = (
            (memory.metadata or {}).get(
                "relations",
                []
            )
        )

        if isinstance(
            relations,
            list,
        ) and relations:

            relation = relations[0]

            if isinstance(
                relation,
                dict,
            ):

                subject = self.normalize_subject(
                    relation.get(
                        "subject"
                    )
                )

                predicate = (
                    self.normalize_predicate(
                        relation.get(
                            "predicate"
                        )
                    )
                )

                object_value = (
                    self.normalize_object(
                        relation.get(
                            "object"
                        )
                    )
                )

        return NormalizedMemory(
            subject=subject,
            predicate=predicate,
            object=object_value,
            type=memory.type,
            content=self.normalize_text(
                memory.content
            ),
        )