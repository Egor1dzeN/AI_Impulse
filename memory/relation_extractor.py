from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from qwen_model import QwenModel
from entity_enricher import Entity
from coreference import CoreferenceCluster
from temporal import TemporalExpression


@dataclass
class Relation:
    id: str
    subject: str
    predicate: str
    object: str
    confidence: float

    subject_entity_label: str | None = None
    object_entity_label: str | None = None

    temporal_value: str | None = None
    temporal_text: str | None = None

    source_text: str = ""
    source_fragment: str = ""

    metadata: dict[str, Any] | None = None


class RelationExtractor:

    SYSTEM_PROMPT = """
Ты извлекаешь структурированные отношения из русского текста.

Нужно найти утверждения вида:

subject → predicate → object

Также отдельно указывай время relation, если оно относится
к этому утверждению.

============================================================
ПРИМЕР 1
============================================================

Текст:

"Иван живёт в Москве."

Ответ:

[
  {
    "subject": "Иван",
    "predicate": "жить_в",
    "object": "Москва",
    "temporal": null,
    "source_fragment": "Иван живёт в Москве.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 2
============================================================

Текст:

"Я переехал в Санкт-Петербург в 2024 году."

Ответ:

[
  {
    "subject": "Я",
    "predicate": "переехать_в",
    "object": "Санкт-Петербург",
    "temporal": "2024",
    "source_fragment": "Я переехал в Санкт-Петербург в 2024 году.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 3
============================================================

Текст:

"Мой брат Иван живёт в Москве.
Он работает программистом."

Ответ:

[
  {
    "subject": "Иван",
    "predicate": "быть_братом",
    "object": "пользователь",
    "temporal": null,
    "source_fragment": "Мой брат Иван",
    "confidence": 0.95
  },
  {
    "subject": "Иван",
    "predicate": "жить_в",
    "object": "Москва",
    "temporal": null,
    "source_fragment": "Мой брат Иван живёт в Москве.",
    "confidence": 0.95
  },
  {
    "subject": "Иван",
    "predicate": "работать_как",
    "object": "программист",
    "temporal": null,
    "source_fragment": "Он работает программистом.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 4
============================================================

Текст:

"Вчера я встретился с Иваном.
Он рассказал мне о своей новой машине."

Ответ:

[
  {
    "subject": "Я",
    "predicate": "встретиться_с",
    "object": "Иван",
    "temporal": "вчера",
    "source_fragment": "Вчера я встретился с Иваном.",
    "confidence": 0.95
  },
  {
    "subject": "Иван",
    "predicate": "рассказать_о",
    "object": "новая машина",
    "temporal": null,
    "source_fragment": "Он рассказал мне о своей новой машине.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 5
============================================================

Текст:

"Мария купила OMODA C5.
Она сказала, что ей нравится автомобиль."

Ответ:

[
  {
    "subject": "Мария",
    "predicate": "купить",
    "object": "OMODA C5",
    "temporal": null,
    "source_fragment": "Мария купила OMODA C5.",
    "confidence": 0.95
  },
  {
    "subject": "Мария",
    "predicate": "нравиться",
    "object": "автомобиль",
    "temporal": null,
    "source_fragment": "Она сказала, что ей нравится автомобиль.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 6
============================================================

Текст:

"Иван — мой коллега."

Ответ:

[
  {
    "subject": "Иван",
    "predicate": "быть_коллегой",
    "object": "пользователь",
    "temporal": null,
    "source_fragment": "Иван — мой коллега.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 7
============================================================

Текст:

"Мария — моя сестра."

Ответ:

[
  {
    "subject": "Мария",
    "predicate": "быть_сестрой",
    "object": "пользователь",
    "temporal": null,
    "source_fragment": "Мария — моя сестра.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 8
============================================================

Текст:

"Я предпочитаю работать вечером."

Ответ:

[
  {
    "subject": "Я",
    "predicate": "предпочитать",
    "object": "работать вечером",
    "temporal": null,
    "source_fragment": "Я предпочитаю работать вечером.",
    "confidence": 0.95
  }
]

============================================================
ПРИМЕР 9
============================================================

Текст:

"Я планирую через неделю начать новый проект."

Ответ:

[
  {
    "subject": "Я",
    "predicate": "планировать",
    "object": "начать новый проект",
    "temporal": "через неделю",
    "source_fragment": "Я планирую через неделю начать новый проект.",
    "confidence": 0.95
  }
]

============================================================
ВАЖНЫЕ ПРАВИЛА
============================================================

1. Возвращай ТОЛЬКО JSON-массив.
2. Не используй Markdown.
3. Не добавляй объяснений.
4. Не используй <think>.
5. Не придумывай отношения.
6. Используй только информацию из SOURCE TEXT.
7. Учитывай COREFERENCE CONTEXT.
8. Если "Он" относится к Ивану, используй "Иван".
9. Subject и object должны быть короткими.
10. Predicate должен быть коротким canonical relation.
11. Predicate должен использовать snake_case.
12. Если temporal относится к relation, помести его в поле temporal.
13. Temporal не должен становиться object.
14. В конструкциях "мой брат", "моя сестра",
    "мой коллега", "мой отец" и т.п.
    объектом является "пользователь".
15. В конструкциях "Иван — мой брат",
    "Мария — моя сестра" и подобных
    subject — человек, object — пользователь.
16. Для конструкций "Мой брат Иван" можно извлечь
    отношение Иван → быть_братом → пользователь.
17. Для "Иван — житель Москвы" можно извлечь
    Иван → жить_в → Москва.
18. source_fragment должен быть дословным фрагментом SOURCE TEXT.
19. source_fragment должен содержать именно ту часть,
    которая подтверждает relation.
20. Не объединяй независимые предложения в один source_fragment.
21. Confidence — число от 0 до 1.
22. Если отношений нет, верни [].
""".strip()

    # =========================================================
    # TYPICAL USER RELATIONSHIPS
    # =========================================================

    USER_RELATION_PATTERNS = [
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>брат|брата|братом)\b",
                re.IGNORECASE,
            ),
            "быть_братом",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>сестра|сестры|сестрой)\b",
                re.IGNORECASE,
            ),
            "быть_сестрой",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>коллега|коллеги|коллегой)\b",
                re.IGNORECASE,
            ),
            "быть_коллегой",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>отец|отцом)\b",
                re.IGNORECASE,
            ),
            "быть_отцом",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>мать|матерью)\b",
                re.IGNORECASE,
            ),
            "быть_матерью",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>друг|друга|другом)\b",
                re.IGNORECASE,
            ),
            "быть_другом",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>муж|мужа|мужем)\b",
                re.IGNORECASE,
            ),
            "быть_мужем",
        ),
        (
            re.compile(
                r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
                r"\s*[—-]\s*"
                r"(?P<possessive>мой|моя|моего|мою|моем|моём)\s+"
                r"(?P<relation>жена|жены|женой)\b",
                re.IGNORECASE,
            ),
            "быть_женой",
        ),
    ]

    # =========================================================
    # "МОЙ БРАТ ИВАН", "МОЯ СЕСТРА МАРИЯ"
    # =========================================================

    POSSESSIVE_NOUN_PATTERNS = [
        (
            re.compile(
                r"\bмой\s+"
                r"(?P<relation>брат|брата|братом)\s+"
                r"(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)\b",
                re.IGNORECASE,
            ),
            "быть_братом",
        ),
        (
            re.compile(
                r"\bмоя\s+"
                r"(?P<relation>сестра|сестры|сестрой)\s+"
                r"(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)\b",
                re.IGNORECASE,
            ),
            "быть_сестрой",
        ),
        (
            re.compile(
                r"\bмой\s+"
                r"(?P<relation>коллега|коллеги|коллегой)\s+"
                r"(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)\b",
                re.IGNORECASE,
            ),
            "быть_коллегой",
        ),
        (
            re.compile(
                r"\bмой\s+"
                r"(?P<relation>отец|отцом)\s+"
                r"(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)\b",
                re.IGNORECASE,
            ),
            "быть_отцом",
        ),
        (
            re.compile(
                r"\bмоя\s+"
                r"(?P<relation>мать|матерью)\s+"
                r"(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)\b",
                re.IGNORECASE,
            ),
            "быть_матерью",
        ),
    ]

    # =========================================================
    # "ИВАН — ЖИТЕЛЬ МОСКВЫ"
    # =========================================================

    LOCATION_PATTERNS = [
        re.compile(
            r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
            r"\s*[—-]\s*"
            r"житель\s+"
            r"(?P<object>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<subject>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]*)"
            r"\s*[—-]\s*"
            r"житель\s+"
            r"(?P<object>[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]+)"
            r"\b",
            re.IGNORECASE,
        ),
    ]

    def __init__(
        self,
        qwen: QwenModel,
    ) -> None:

        self.qwen = qwen

    # =========================================================
    # FORMATTING
    # =========================================================

    @staticmethod
    def _format_entities(
        entities: list[Entity],
    ) -> str:

        if not entities:
            return "None"

        return "\n".join(
            f"- {entity.text} "
            f"[{entity.label}] "
            f"score={entity.score:.3f}"
            for entity in entities
        )

    @staticmethod
    def _format_coreference(
        clusters: list[CoreferenceCluster],
    ) -> str:

        if not clusters:
            return "None"

        lines = []

        for cluster in clusters:

            representative = (
                cluster.representative.text
            )

            mentions = ", ".join(
                mention.text
                for mention in cluster.mentions
            )

            lines.append(
                f"- {representative} <-> {mentions}"
            )

        return "\n".join(lines)

    @staticmethod
    def _format_temporal(
        expressions: list[TemporalExpression],
    ) -> str:

        if not expressions:
            return "None"

        return "\n".join(
            f"- {expression.text} -> "
            f"{expression.normalized_value} "
            f"[{expression.expression_type}]"
            for expression in expressions
        )

    # =========================================================
    # HELPERS
    # =========================================================

    @staticmethod
    def _find_entity_label(
        text: str,
        entities: list[Entity],
    ) -> str | None:

        normalized = text.lower().strip()

        for entity in entities:

            if (
                entity.text.lower().strip()
                == normalized
            ):
                return entity.label

        return None

    @staticmethod
    def _find_temporal(
        temporal_value: Any,
        temporal_expressions: list[TemporalExpression],
    ) -> tuple[str | None, str | None]:

        if temporal_value is None:
            return None, None

        if not isinstance(
            temporal_value,
            str,
        ):
            return None, None

        temporal_value = temporal_value.strip()

        if not temporal_value:
            return None, None

        for expression in temporal_expressions:

            if (
                expression.normalized_value
                == temporal_value
            ):

                return (
                    expression.normalized_value,
                    expression.text,
                )

        for expression in temporal_expressions:

            if (
                temporal_value.lower()
                in expression.text.lower()
                or expression.text.lower()
                in temporal_value.lower()
            ):

                return (
                    expression.normalized_value,
                    expression.text,
                )

        return (
            temporal_value,
            None,
        )

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

    # =========================================================
    # RULE-BASED USER RELATIONS
    # =========================================================

    def _extract_user_relationships(
        self,
        source_text: str,
    ) -> list[Relation]:

        relations: list[Relation] = []

        # -----------------------------------------------------
        # Иван — мой брат
        # Иван — моя сестра
        # -----------------------------------------------------

        for pattern, predicate in (
            self.USER_RELATION_PATTERNS
        ):

            for match in pattern.finditer(
                source_text
            ):

                subject = match.group(
                    "subject"
                ).strip()

                source_fragment = (
                    match.group(0).strip()
                )

                relations.append(
                    Relation(
                        id=str(uuid.uuid4()),
                        subject=subject,
                        predicate=predicate,
                        object="пользователь",
                        confidence=0.99,
                        subject_entity_label=None,
                        object_entity_label=None,
                        temporal_value=None,
                        temporal_text=None,
                        source_text=source_text,
                        source_fragment=source_fragment,
                        metadata={
                            "relation_source": "rule"
                        },
                    )
                )

        # -----------------------------------------------------
        # Мой брат Иван
        # -----------------------------------------------------

        for pattern, predicate in (
            self.POSSESSIVE_NOUN_PATTERNS
        ):

            for match in pattern.finditer(
                source_text
            ):

                subject = match.group(
                    "subject"
                ).strip()

                source_fragment = (
                    match.group(0).strip()
                )

                relations.append(
                    Relation(
                        id=str(uuid.uuid4()),
                        subject=subject,
                        predicate=predicate,
                        object="пользователь",
                        confidence=0.99,
                        subject_entity_label=None,
                        object_entity_label=None,
                        temporal_value=None,
                        temporal_text=None,
                        source_text=source_text,
                        source_fragment=source_fragment,
                        metadata={
                            "relation_source": "rule"
                        },
                    )
                )

        return relations

    # =========================================================
    # "ИВАН — ЖИТЕЛЬ МОСКВЫ"
    # =========================================================

    def _extract_location_relations(
        self,
        source_text: str,
    ) -> list[Relation]:

        relations: list[Relation] = []

        for pattern in self.LOCATION_PATTERNS:

            for match in pattern.finditer(
                source_text
            ):

                subject = match.group(
                    "subject"
                ).strip()

                object_value = match.group(
                    "object"
                ).strip()

                source_fragment = (
                    match.group(0).strip()
                )

                relations.append(
                    Relation(
                        id=str(uuid.uuid4()),
                        subject=subject,
                        predicate="жить_в",
                        object=object_value,
                        confidence=0.97,
                        subject_entity_label="person",
                        object_entity_label="location",
                        temporal_value=None,
                        temporal_text=None,
                        source_text=source_text,
                        source_fragment=source_fragment,
                        metadata={
                            "relation_source": "rule"
                        },
                    )
                )

        return relations

    # =========================================================
    # SOURCE FRAGMENT FALLBACK
    # =========================================================

    @classmethod
    def _find_source_fragment(
        cls,
        source_text: str,
        relation: dict[str, Any],
        coreference_clusters: list[CoreferenceCluster],
    ) -> str:

        requested_fragment = relation.get(
            "source_fragment"
        )

        if isinstance(
            requested_fragment,
            str,
        ):

            requested_fragment = (
                requested_fragment.strip()
            )

            if (
                requested_fragment
                and requested_fragment in source_text
            ):

                return requested_fragment

        # -----------------------------------------------------
        # split source into sentences
        # -----------------------------------------------------

        sentences = []

        pattern = re.compile(
            r"[^.!?]+(?:[.!?]+|$)",
            re.UNICODE,
        )

        for match in pattern.finditer(
            source_text
        ):

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
                and source_text[
                    end - 1
                ].isspace()
            ):
                end -= 1

            sentence = source_text[
                start:end
            ].strip()

            if sentence:
                sentences.append(
                    sentence
                )

        subject = str(
            relation.get(
                "subject",
                "",
            )
        )

        object_value = str(
            relation.get(
                "object",
                "",
            )
        )

        subject_normalized = (
            cls._normalize(subject)
        )

        object_normalized = (
            cls._normalize(object_value)
        )

        best_sentence = ""
        best_score = 0.0

        for sentence in sentences:

            normalized_sentence = (
                cls._normalize(sentence)
            )

            score = 0.0

            if (
                subject_normalized
                and subject_normalized
                in normalized_sentence
            ):
                score += 2.0

            if (
                object_normalized
                and object_normalized
                in normalized_sentence
            ):
                score += 2.0

            # coreference mentions
            for cluster in (
                coreference_clusters
            ):

                representative = (
                    cls._normalize(
                        cluster.representative.text
                    )
                )

                if (
                    representative
                    == subject_normalized
                ):

                    for mention in (
                        cluster.mentions
                    ):

                        mention_normalized = (
                            cls._normalize(
                                mention.text
                            )
                        )

                        if (
                            mention_normalized
                            in normalized_sentence
                        ):

                            score += 1.5

            if score > best_score:

                best_score = score
                best_sentence = sentence

        if best_sentence:
            return best_sentence

        return source_text

    # =========================================================
    # DEDUPLICATION
    # =========================================================

    @staticmethod
    def _normalize_for_dedup(
            text: str,
    ) -> str:

        text = text.lower()
        text = text.replace("ё", "е")

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

    @staticmethod
    def _deduplicate(
            relations: list[Relation],
    ) -> list[Relation]:

        result: list[Relation] = []

        seen: set[
            tuple[str, str, str, str]
        ] = set()

        for relation in relations:

            key = (
                RelationExtractor._normalize_for_dedup(
                    relation.subject
                ),
                RelationExtractor._normalize_for_dedup(
                    relation.predicate
                ),
                RelationExtractor._normalize_for_dedup(
                    relation.object
                ),
                RelationExtractor._normalize_for_dedup(
                    relation.source_fragment
                ),
            )

            if key in seen:
                continue

            seen.add(key)

            result.append(
                relation
            )

        return result

    # =========================================================
    # ROBUST JSON PARSING
    # =========================================================

    @staticmethod
    def _parse_json_response(
            response: str,
    ) -> list[dict[str, Any]]:

        text = response.strip()

        if text.startswith("```"):
            text = re.sub(
                r"^```(?:json)?\s*",
                "",
                text,
                flags=re.IGNORECASE,
            )
            text = re.sub(
                r"\s*```$",
                "",
                text,
            )
            text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        # Generation may stop in the middle of the final relation.
        # Preserve all complete objects that were generated before it.
        if text.startswith("["):
            last_complete = text.rfind("},")

            if last_complete == -1:
                last_complete = text.rfind("}")

            if last_complete != -1:
                candidate = text[:last_complete + 1].rstrip() + "]"

                try:
                    data = json.loads(candidate)
                    if isinstance(data, list):
                        return data
                except json.JSONDecodeError:
                    pass

        raise ValueError(
            "Invalid relation JSON:\n"
            f"{response}"
        )

    # =========================================================
    # MAIN EXTRACTION
    # =========================================================

    def extract(
        self,
        source_text: str,
        entities: list[Entity],
        coreference_clusters: list[CoreferenceCluster],
        temporal_expressions: list[TemporalExpression],
    ) -> list[Relation]:

        # =====================================================
        # 1. QWEN
        # =====================================================

        prompt = f"""
SOURCE TEXT:
{source_text}

ENTITIES:
{self._format_entities(entities)}

COREFERENCE CONTEXT:
{self._format_coreference(coreference_clusters)}

TEMPORAL CONTEXT:
{self._format_temporal(temporal_expressions)}

Извлеки ВСЕ структурированные отношения
из SOURCE TEXT.

Для каждой relation обязательно укажи
source_fragment — дословный фрагмент SOURCE TEXT,
из которого следует это отношение.

Не делай отдельный relation для самого времени.
Если время относится к отношению,
запиши его в поле temporal.
""".strip()

        response = self.qwen.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            max_new_tokens=1024,
        )

        data = self._parse_json_response(response)

        if not isinstance(
            data,
            list,
        ):

            raise ValueError(
                "RelationExtractor response "
                "must be a JSON array."
            )

        relations: list[Relation] = []

        # =====================================================
        # 2. QWEN RELATIONS
        # =====================================================

        for item in data:

            if not isinstance(
                item,
                dict,
            ):
                continue

            subject = item.get(
                "subject"
            )

            predicate = item.get(
                "predicate"
            )

            object_value = item.get(
                "object"
            )

            temporal_value = item.get(
                "temporal"
            )

            confidence = item.get(
                "confidence",
                0.5,
            )

            if not isinstance(
                subject,
                str,
            ):
                continue

            if not isinstance(
                predicate,
                str,
            ):
                continue

            if not isinstance(
                object_value,
                str,
            ):
                continue

            subject = subject.strip()
            predicate = predicate.strip()
            object_value = object_value.strip()

            if not subject:
                continue

            if not predicate:
                continue

            if not object_value:
                continue

            predicate = re.sub(
                r"\s+",
                "_",
                predicate.lower(),
            )

            try:

                confidence = float(
                    confidence
                )

            except (
                TypeError,
                ValueError,
            ):

                confidence = 0.5

            confidence = max(
                0.0,
                min(
                    1.0,
                    confidence,
                ),
            )

            (
                temporal_normalized,
                temporal_text,
            ) = self._find_temporal(
                temporal_value,
                temporal_expressions,
            )

            source_fragment = (
                self._find_source_fragment(
                    source_text=source_text,
                    relation=item,
                    coreference_clusters=(
                        coreference_clusters
                    ),
                )
            )

            relations.append(
                Relation(
                    id=str(uuid.uuid4()),
                    subject=subject,
                    predicate=predicate,
                    object=object_value,
                    confidence=confidence,
                    subject_entity_label=(
                        self._find_entity_label(
                            subject,
                            entities,
                        )
                    ),
                    object_entity_label=(
                        self._find_entity_label(
                            object_value,
                            entities,
                        )
                    ),
                    temporal_value=(
                        temporal_normalized
                    ),
                    temporal_text=(
                        temporal_text
                    ),
                    source_text=source_text,
                    source_fragment=source_fragment,
                    metadata={
                        "relation_source": "qwen3"
                    },
                )
            )

        # =====================================================
        # 3. RULE-BASED COMPLETION
        # =====================================================

        rule_relations = (
            self._extract_user_relationships(
                source_text
            )
        )

        location_relations = (
            self._extract_location_relations(
                source_text
            )
        )

        relations.extend(
            rule_relations
        )

        relations.extend(
            location_relations
        )

        # =====================================================
        # 4. NORMALIZATION
        # =====================================================

        normalized_relations: list[Relation] = []

        for relation in relations:

            subject = relation.subject.strip()
            predicate = relation.predicate.strip()
            object_value = relation.object.strip()

            # "я" → "Я"
            if (
                subject.lower()
                in {"я", "мне", "меня"}
            ):
                subject = "Я"

            # user object
            if (
                object_value.lower()
                in {
                    "мой",
                    "моя",
                    "мне",
                    "меня",
                    "я",
                    "пользователь",
                }
            ):
                object_value = "пользователь"

            metadata = dict(
                relation.metadata or {}
            )

            normalized_relations.append(
                Relation(
                    id=relation.id,
                    subject=subject,
                    predicate=predicate,
                    object=object_value,
                    confidence=relation.confidence,
                    subject_entity_label=(
                        relation.subject_entity_label
                    ),
                    object_entity_label=(
                        relation.object_entity_label
                    ),
                    temporal_value=(
                        relation.temporal_value
                    ),
                    temporal_text=(
                        relation.temporal_text
                    ),
                    source_text=(
                        relation.source_text
                    ),
                    source_fragment=(
                        relation.source_fragment
                    ),
                    metadata=metadata,
                )
            )

        # =====================================================
        # 5. DEDUPLICATION
        # =====================================================

        return self._deduplicate(
            normalized_relations
        )
