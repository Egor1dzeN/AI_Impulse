from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from memory_context import MemoryContext
from models import MemoryCandidate, MemoryType, Source
from qwen_model import QwenModel


class MemoryExtractor:
    SYSTEM_PROMPT = """
Ты извлекаешь долговременную информацию из сообщений пользователя
для персонального AI-агента.

Твоя задача — найти только новую долговременную информацию,
которая содержится в CURRENT USER MESSAGE.

Разрешённые типы:
- fact
- preference
- episode
- entity
- relationship
- goal
- state
- procedure

ВАЖНЫЕ ПРАВИЛА:
1. Возвращай ТОЛЬКО JSON-массив.
2. Не используй Markdown.
3. Не добавляй объяснений.
4. Не используй <think>.
5. Не придумывай новую информацию.
6. CURRENT USER MESSAGE является единственным источником новой памяти.
7. MEMORY CONTEXT можно использовать только для разрешения местоимений,
   пропущенных субъектов и ссылок на уже известные сущности.
8. Если местоимение относится к сущности из MEMORY CONTEXT, используй
   конкретное имя сущности в content, subject и relations.
9. Если субъект пропущен, восстанавливай его как Пользователь.
   Например: "Работаю программистом" ->
   "Пользователь работает программистом".
10. "Мне нравится X", "Я люблю X", "Я предпочитаю X" -> preference.
11. "Я живу в X" -> fact.
12. "Я переехал в X" -> state.
13. "Я планирую X" -> goal.
14. Информация типа "Иван — мой брат" -> relationship.
15. Конкретно произошедшее событие -> episode.
16. Если в одном сообщении есть несколько независимых фактов,
    верни несколько элементов массива.
17. Не превращай старую память из MEMORY CONTEXT в новую память.
18. Для профессии, работы, места проживания и подобных фактов
    НЕ используй preference, если в тексте нет предпочтения.
19. Content должен быть кратким, каноническим и понятным.
20. Confidence — число от 0 до 1.

Формат элемента:
{
  "type": "fact|preference|episode|entity|relationship|goal|state|procedure",
  "content": "...",
  "confidence": 0.0,
  "subject": "...",
  "relations": [
    {
      "subject": "...",
      "predicate": "...",
      "object": "..."
    }
  ]
}

Если поля subject или relations не нужны, оставляй их пустыми.
""".strip()

    PREFERENCE_PATTERNS = [
        re.compile(
            r"^\s*(?:я\s+)?предпочитаю\s+(?P<content>.+?)\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*мне\s+нравится\s+(?P<content>.+?)\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*я\s+люблю\s+(?P<content>.+?)\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*я\s+не\s+люблю\s+(?P<content>.+?)\s*[.!?]?\s*$",
            re.IGNORECASE,
        ),
    ]

    LIVING_PATTERN = re.compile(
        r"^\s*я\s+жив\w*\s+в\s+(?P<location>.+?)\s*[.!?]?\s*$",
        re.IGNORECASE,
    )

    MOVED_PATTERN = re.compile(
        r"^\s*я\s+переех\w*\s+в\s+(?P<location>.+?)\s*[.!?]?\s*$",
        re.IGNORECASE,
    )

    PLAN_PATTERN = re.compile(
        r"^\s*я\s+планирую\s+(?P<content>.+?)\s*[.!?]?\s*$",
        re.IGNORECASE,
    )

    SUBJECT_MISSING_PATTERNS = [
        re.compile(r"^\s*работаю\s+(?P<content>.+?)\s*[.!?]?\s*$", re.IGNORECASE),
        re.compile(r"^\s*учусь\s+(?P<content>.+?)\s*[.!?]?\s*$", re.IGNORECASE),
        re.compile(r"^\s*живу\s+в\s+(?P<location>.+?)\s*[.!?]?\s*$", re.IGNORECASE),
    ]

    PRONOUNS = {
        "он", "она", "они", "его", "ее", "её", "ему", "ей", "им", "ним",
        "ней", "них", "них", "него", "неё", "него", "ней",
    }

    def __init__(self, qwen: QwenModel) -> None:
        self.qwen = qwen

    # =========================================================
    # CONTEXT
    # =========================================================

    @staticmethod
    def _format_memory_context(context: MemoryContext | None) -> str:
        if context is None or not context.memories:
            return "NONE"

        lines: list[str] = []

        for memory in context.memories:
            lines.append(f"- [{memory.type.value}] {memory.content}")

            metadata = memory.metadata or {}

            subject = metadata.get("subject")
            if subject:
                lines.append(f"  subject={subject}")

            relations = metadata.get("relations", [])
            if isinstance(relations, list):
                for relation in relations:
                    if not isinstance(relation, dict):
                        continue
                    lines.append(
                        "  relation: "
                        f"{relation.get('subject')} "
                        f"--[{relation.get('predicate')}]--> "
                        f"{relation.get('object')}"
                    )

        return "\n".join(lines)

    @staticmethod
    def _context_entities(context: MemoryContext | None) -> list[str]:
        if context is None:
            return []

        entities: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            if not isinstance(value, str):
                return
            value = value.strip()
            if not value:
                return
            normalized = value.lower()
            if normalized in {
                "пользователь", "я", "он", "она", "они", "ним", "ему", "ей",
                "его", "ее", "её", "им", "ней", "них",
            }:
                return
            if normalized not in seen:
                seen.add(normalized)
                entities.append(value)

        # First collect entities that are explicitly represented as people.
        # We deliberately do NOT collect arbitrary capitalized words such as
        # "Москва": they are often objects/locations, not antecedents for
        # pronouns like "он" / "ним".
        person_entities: list[str] = []
        person_seen: set[str] = set()

        def add_person(value: Any) -> None:
            if not isinstance(value, str):
                return
            value = value.strip()
            if not value:
                return
            normalized = value.lower()
            if normalized in {
                "пользователь", "я", "он", "она", "они", "ним", "ему",
                "ей", "его", "ее", "её", "им", "ней", "них", "него",
            }:
                return
            # A person/name candidate should look like a proper name, not
            # a common noun such as "программистом" or a location.
            if not re.fullmatch(r"[А-ЯЁ][а-яё-]+", value):
                return
            if normalized not in person_seen:
                person_seen.add(normalized)
                person_entities.append(value)

        for memory in context.memories:
            metadata = memory.metadata or {}

            # A non-user subject is the strongest signal that it is a
            # concrete entity/person mentioned in memory.
            subject = metadata.get("subject")
            if isinstance(subject, str) and subject.lower() != "пользователь":
                add_person(subject)

            relations = metadata.get("relations", [])
            if isinstance(relations, list):
                for relation in relations:
                    if not isinstance(relation, dict):
                        continue
                    predicate = str(relation.get("predicate", "")).lower()
                    rel_subject = relation.get("subject")
                    rel_object = relation.get("object")

                    # Relationship subjects/objects are useful entity anchors.
                    if predicate in {
                        "быть_братом", "быть_сестрой", "быть_другом",
                        "быть_подругой", "быть_родителем", "быть_отцом",
                        "быть_матерью", "быть_сыном", "быть_дочерью",
                        "быть_мужем", "быть_женой",
                    }:
                        add_person(rel_subject)
                        add_person(rel_object)

        # Keep the order in which entities appeared in memory: the most
        # recent explicit person is the default antecedent.
        if person_entities:
            return person_entities

        return entities

    # =========================================================
    # RESPONSE CLEANING
    # =========================================================

    @staticmethod
    def _clean_response(response: str) -> str:
        response = response.strip()

        if response.startswith("```"):
            lines = response.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response = "\n".join(lines).strip()

        # Remove accidental leading explanation before a JSON array.
        start = response.find("[")
        end = response.rfind("]")
        if start >= 0 and end > start:
            candidate = response[start : end + 1].strip()
            if candidate.startswith("["):
                response = candidate

        return response

    # =========================================================
    # NORMALIZATION
    # =========================================================

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = re.sub(r"\s+", " ", text.strip())
        return text

    @staticmethod
    def _replace_pronouns(text: str, antecedent: str | None) -> str:
        if not antecedent:
            return text

        # Preserve grammar where possible for the common masculine name Иван.
        replacements = {
            "ним": f"{antecedent}",
            "него": f"{antecedent}",
            "ему": f"{antecedent}",
            "им": f"{antecedent}",
            "он": antecedent,
            "его": antecedent,
        }

        result = text
        for pronoun, replacement in replacements.items():
            result = re.sub(
                rf"\b{re.escape(pronoun)}\b",
                replacement,
                result,
                flags=re.IGNORECASE,
            )

        return result

    @staticmethod
    def _extract_relation_dict(line: str) -> dict[str, Any] | None:
        match = re.match(
            r"^relation\s*:\s*(?P<subject>.+?)\s*--\[(?P<predicate>[^\]]+)\]-->\s*(?P<object>.+?)\s*$",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None

        return {
            "subject": match.group("subject").strip(),
            "predicate": match.group("predicate").strip(),
            "object": match.group("object").strip(),
        }

    @staticmethod
    def _infer_type(memory_type: str, content: str) -> MemoryType:
        content_lower = content.lower()

        # Do not let Qwen turn simple work facts into preferences.
        if memory_type == MemoryType.PREFERENCE.value:
            if any(
                marker in content_lower
                for marker in ("предпочит", "нравится", "люблю", "не люблю")
            ):
                return MemoryType.PREFERENCE
            if any(
                marker in content_lower
                for marker in ("работает", "работаю", "работал", "работать", "професс")
            ):
                return MemoryType.FACT

        if memory_type in {item.value for item in MemoryType}:
            return MemoryType(memory_type)

        return MemoryType.FACT

    def _choose_antecedent(
        self,
        text: str,
        context: MemoryContext | None,
    ) -> str | None:
        if context is None:
            return None

        has_pronoun = bool(
            re.search(
                r"\b(он|она|они|его|её|ее|ему|ей|им|ним|ней|них|него)\b",
                text,
                re.IGNORECASE,
            )
        )

        if not has_pronoun:
            return None

        entities = self._context_entities(context)
        if not entities:
            return None

        # Prefer the most recently represented explicit person/entity.
        return entities[-1]

    def _canonicalize_candidate(
        self,
        candidate: MemoryCandidate,
        context: MemoryContext | None,
    ) -> MemoryCandidate:
        antecedent = self._choose_antecedent(candidate.content, context)

        content = self._normalize_text(candidate.content)
        subject = candidate.metadata.get("subject") if candidate.metadata else None

        if isinstance(subject, str):
            subject = subject.strip()

        if antecedent:
            content = self._replace_pronouns(content, antecedent)
            if isinstance(subject, str):
                subject = self._replace_pronouns(subject, antecedent)

        # Missing first-person subject.
        if not subject:
            if re.match(r"^\s*(работаю|живу|учусь|переехал|переехала|планирую|встретился|встретилась)\b", content, re.IGNORECASE):
                subject = "Пользователь"

        if subject and subject.lower() in {"я", "мне", "меня", "пользователь"}:
            subject = "Пользователь"

        candidate.content = content
        candidate.metadata["subject"] = subject

        relations = candidate.metadata.get("relations", [])
        normalized_relations: list[dict[str, Any]] = []
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                rel = dict(relation)
                for key in ("subject", "object"):
                    value = rel.get(key)
                    if isinstance(value, str):
                        value = self._replace_pronouns(value, antecedent)
                        if value.lower() in {"я", "мне", "меня", "пользователь"}:
                            value = "Пользователь"
                        rel[key] = value
                normalized_relations.append(rel)

        candidate.metadata["relations"] = normalized_relations
        return candidate

    # =========================================================
    # PARSE JSON / TEXT
    # =========================================================

    def _build_candidate(
        self,
        *,
        item: dict[str, Any],
        user_id: str,
        source: Source,
        source_text: str,
    ) -> MemoryCandidate | None:
        content = item.get("content", "")
        if not isinstance(content, str):
            return None

        content = content.strip()
        if not content:
            return None

        raw_type = str(item.get("type", "fact")).strip().lower()
        memory_type = self._infer_type(raw_type, content)

        try:
            confidence = float(item.get("confidence", 0.95))
        except (TypeError, ValueError):
            confidence = 0.95
        confidence = max(0.0, min(1.0, confidence))

        subject = item.get("subject")
        if not isinstance(subject, str):
            subject = None

        relations = item.get("relations", [])
        if not isinstance(relations, list):
            relations = []

        clean_relations: list[dict[str, Any]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            clean_relations.append(
                {
                    "subject": relation.get("subject"),
                    "predicate": relation.get("predicate"),
                    "object": relation.get("object"),
                }
            )

        return MemoryCandidate(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=memory_type,
            content=content,
            confidence=confidence,
            source=source,
            created_at=datetime.utcnow(),
            source_text=source_text,
            metadata={
                "subject": subject,
                "relations": clean_relations,
            },
        )

    def _parse(
        self,
        response: str,
        user_id: str,
        source: Source,
        source_text: str,
    ) -> list[MemoryCandidate]:
        response = self._clean_response(response)
        if not response:
            return []

        # ---------------------------------------------------------
        # 1. JSON
        # ---------------------------------------------------------
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                data = [data]

            if isinstance(data, list):
                candidates: list[MemoryCandidate] = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    candidate = self._build_candidate(
                        item=item,
                        user_id=user_id,
                        source=source,
                        source_text=source_text,
                    )
                    if candidate is not None:
                        candidates.append(candidate)
                return candidates
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # ---------------------------------------------------------
        # 2. Text fallback
        # ---------------------------------------------------------
        candidates: list[MemoryCandidate] = []
        current: dict[str, Any] | None = None

        def flush() -> None:
            nonlocal current
            if current is None:
                return
            candidate = self._build_candidate(
                item=current,
                user_id=user_id,
                source=source,
                source_text=source_text,
            )
            if candidate is not None:
                candidates.append(candidate)
            current = None

        for raw_line in response.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            header = re.match(
                r"^\[(?P<type>[a-zA-Z_]+)\]\s*(?P<content>.+?)\s*$",
                line,
            )
            if header:
                flush()
                current = {
                    "type": header.group("type").lower(),
                    "content": header.group("content").strip(),
                    "confidence": 0.95,
                    "subject": None,
                    "relations": [],
                }
                continue

            if current is None:
                # Accept a bare fallback candidate like:
                # "Я встретился с ним"
                if line and not re.match(r"^(confidence|subject)\s*:", line, re.IGNORECASE):
                    current = {
                        "type": "fact",
                        "content": line,
                        "confidence": 0.95,
                        "subject": None,
                        "relations": [],
                    }
                continue

            match = re.match(
                r"^subject\s*=\s*(?P<subject>.+?)\s*$",
                line,
                re.IGNORECASE,
            )
            if match:
                current["subject"] = match.group("subject").strip()
                continue

            match = re.match(
                r"^subject\s*:\s*(?P<subject>.+?)\s*$",
                line,
                re.IGNORECASE,
            )
            if match:
                current["subject"] = match.group("subject").strip()
                continue

            relation = self._extract_relation_dict(line)
            if relation is not None:
                current.setdefault("relations", []).append(relation)
                continue

            match = re.match(
                r"^confidence\s*:\s*(?P<confidence>[0-9]*\.?[0-9]+)\s*$",
                line,
                re.IGNORECASE,
            )
            if match:
                current["confidence"] = float(match.group("confidence"))
                continue

        flush()

        if not candidates:
            # Last-resort: use the full response as a fact instead of crashing.
            candidates.append(
                self._candidate(
                    user_id=user_id,
                    memory_type=MemoryType.FACT,
                    content=response,
                    confidence=0.5,
                    source=source,
                    source_text=source_text,
                )
            )

        return candidates

    # =========================================================
    # CREATE CANDIDATE
    # =========================================================

    @staticmethod
    def _candidate(
        *,
        user_id: str,
        memory_type: MemoryType,
        content: str,
        confidence: float,
        source: Source,
        source_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=memory_type,
            content=content.strip(),
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
            created_at=datetime.utcnow(),
            source_text=source_text,
            metadata=metadata or {},
        )

    # =========================================================
    # RULE-BASED EXTRACTION
    # =========================================================

    def _extract_rule_based(
        self,
        text: str,
        user_id: str,
        source: Source,
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []

        # Preference
        for pattern in self.PREFERENCE_PATTERNS:
            match = pattern.match(text)
            if match is None:
                continue

            content = match.group("content").strip()
            if content:
                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.PREFERENCE,
                        content="Пользователь предпочитает " + content,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata={
                            "subject": "Пользователь",
                            "relations": [
                                {
                                    "subject": "Пользователь",
                                    "predicate": "предпочитать",
                                    "object": content,
                                }
                            ],
                        },
                    )
                )
                return candidates

        # Living
        match = self.LIVING_PATTERN.match(text)
        if match:
            location = match.group("location").strip()
            if location:
                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.FACT,
                        content="Пользователь живёт в " + location,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata={
                            "subject": "Пользователь",
                            "relations": [
                                {
                                    "subject": "Пользователь",
                                    "predicate": "жить_в",
                                    "object": location,
                                }
                            ],
                        },
                    )
                )
                return candidates

        # Moved
        match = self.MOVED_PATTERN.match(text)
        if match:
            location = match.group("location").strip()
            if location:
                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.STATE,
                        content="Пользователь переехал в " + location,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata={
                            "subject": "Пользователь",
                            "relations": [
                                {
                                    "subject": "Пользователь",
                                    "predicate": "переехать_в",
                                    "object": location,
                                }
                            ],
                        },
                    )
                )
                return candidates

        # Plan
        match = self.PLAN_PATTERN.match(text)
        if match:
            content = match.group("content").strip()
            if content:
                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.GOAL,
                        content="Пользователь планирует " + content,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata={
                            "subject": "Пользователь",
                            "relations": [
                                {
                                    "subject": "Пользователь",
                                    "predicate": "планировать",
                                    "object": content,
                                }
                            ],
                        },
                    )
                )
                return candidates

        return []


    # =========================================================
    # CONTEXTUAL RULE-BASED EXTRACTION
    # =========================================================

    def _extract_context_rule_based(
        self,
        text: str,
        user_id: str,
        source: Source,
        context: MemoryContext | None,
    ) -> list[MemoryCandidate]:
        """
        Deterministic handling of the small set of constructions where
        resolving the subject/object from memory is more reliable than
        asking the LLM to do it implicitly.
        """
        candidates: list[MemoryCandidate] = []

        antecedent = self._choose_antecedent(text, context)

        # ---------------------------------------------------------
        # Мой брат Иван живёт в Москве.
        # Save both the relationship and the fact about Ivan.
        # ---------------------------------------------------------
        match = re.match(
            r"^\s*мой\s+брат\s+(?P<name>[А-ЯЁ][а-яё-]+)\s+"
            r"жив[её]т\s+в\s+(?P<location>.+?)\s*[.!?]?\s*$",
            text,
            re.IGNORECASE,
        )
        if match:
            name = match.group("name").strip()
            location = match.group("location").strip()

            candidates.append(
                self._candidate(
                    user_id=user_id,
                    memory_type=MemoryType.RELATIONSHIP,
                    content=f"{name} — брат пользователя",
                    confidence=0.99,
                    source=source,
                    source_text=text,
                    metadata={
                        "subject": name,
                        "relations": [
                            {
                                "subject": name,
                                "predicate": "быть_братом",
                                "object": "Пользователь",
                            }
                        ],
                    },
                )
            )

            candidates.append(
                self._candidate(
                    user_id=user_id,
                    memory_type=MemoryType.FACT,
                    content=f"{name} живёт в {location}",
                    confidence=0.99,
                    source=source,
                    source_text=text,
                    metadata={
                        "subject": name,
                        "relations": [
                            {
                                "subject": name,
                                "predicate": "жить_в",
                                "object": location,
                            }
                        ],
                    },
                )
            )

            return candidates

        # ---------------------------------------------------------
        # Он работает программистом. / Она работает программистом.
        # ---------------------------------------------------------
        if antecedent:
            match = re.match(
                r"^\s*(?:он|она)\s+работа(?:ет|л|ла)\s+"
                r"(?P<profession>.+?)\s*[.!?]?\s*$",
                text,
                re.IGNORECASE,
            )
            if match:
                profession = match.group("profession").strip()
                content = f"{antecedent} работает {profession}"

                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.FACT,
                        content=content,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata={
                            "subject": antecedent,
                            "relations": [
                                {
                                    "subject": antecedent,
                                    "predicate": "работать_как",
                                    "object": profession,
                                }
                            ],
                        },
                    )
                )
                return candidates

            # -----------------------------------------------------
            # Вчера я встретился с ним.
            # -----------------------------------------------------
            match = re.match(
                r"^\s*(?:(?P<temporal>вчера|сегодня|позавчера)\s+)?"
                r"я\s+встретил(?:ся|ась)\s+с\s+"
                r"(?:ним|ней|ним(?:и)?)\s*[.!?]?\s*$",
                text,
                re.IGNORECASE,
            )
            if match:
                temporal = match.group("temporal")
                content = f"Пользователь встретился с {antecedent}"

                metadata = {
                    "subject": "Пользователь",
                    "relations": [
                        {
                            "subject": "Пользователь",
                            "predicate": "встретиться_с",
                            "object": antecedent,
                        }
                    ],
                }
                if temporal:
                    metadata["temporal_text"] = temporal

                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.EPISODE,
                        content=content,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata=metadata,
                    )
                )
                return candidates

        # ---------------------------------------------------------
        # Завершение задачи: создаём state candidate, который сможет
        # закрыть соответствующую active goal на этапе consolidation.
        # ---------------------------------------------------------
        completion_match = re.match(
            r"^\s*отчет\s+(?:я\s+)?(?:уже\s+)?(?:закончил|закончила|завершил|завершила)\s*[.!?]?\s*$",
            text,
            re.IGNORECASE,
        )
        if completion_match:
            candidates.append(
                self._candidate(
                    user_id=user_id,
                    memory_type=MemoryType.STATE,
                    content="Отчёт завершён",
                    confidence=0.99,
                    source=source,
                    source_text=text,
                    metadata={
                        "subject": "Пользователь",
                        "relations": [
                            {
                                "subject": "Пользователь",
                                "predicate": "статус_задачи",
                                "object": "отчёт:завершён",
                            }
                        ],
                    },
                )
            )
            return candidates

        # ---------------------------------------------------------
        # Коллега Сергей написал мне, что ...
        # General form: colleague <Name> told me that ...
        # ---------------------------------------------------------
        colleague_match = re.match(
            r"^\s*коллег(?:а|у)\s+(?P<name>[А-ЯЁ][а-яё-]+)\s+написал(?:а)?\s+мне,?\s+что\s+(?P<fact>.+?)\s*[.!?]?\s*$",
            text,
            re.IGNORECASE,
        )
        if colleague_match:
            name = colleague_match.group("name").strip()
            fact = colleague_match.group("fact").strip()
            candidates.append(
                self._candidate(
                    user_id=user_id,
                    memory_type=MemoryType.STATE,
                    content=fact,
                    confidence=0.99,
                    source=source,
                    source_text=text,
                    metadata={
                        "subject": name,
                        "relations": [
                            {
                                "subject": name,
                                "predicate": "сообщить",
                                "object": fact,
                            }
                        ],
                    },
                )
            )
            return candidates

        # ---------------------------------------------------------
        # Он ещё сообщил, что ...
        # Resolve the antecedent from prior memory context.
        # ---------------------------------------------------------
        if antecedent:
            report_match = re.match(
                r"^\s*(?:он|она)\s+ещ[её]\s+сообщил(?:а)?\s*,?\s+что\s+(?P<fact>.+?)\s*[.!?]?\s*$",
                text,
                re.IGNORECASE,
            )
            if report_match:
                fact = report_match.group("fact").strip()
                candidates.append(
                    self._candidate(
                        user_id=user_id,
                        memory_type=MemoryType.STATE,
                        content=fact,
                        confidence=0.99,
                        source=source,
                        source_text=text,
                        metadata={
                            "subject": antecedent,
                            "relations": [
                                {
                                    "subject": antecedent,
                                    "predicate": "сообщить",
                                    "object": fact,
                                }
                            ],
                        },
                    )
                )
                return candidates

        # ---------------------------------------------------------
        # Работаю программистом.
        # ---------------------------------------------------------
        match = re.match(
            r"^\s*работаю\s+(?P<profession>.+?)\s*[.!?]?\s*$",
            text,
            re.IGNORECASE,
        )
        if match:
            profession = match.group("profession").strip()
            candidates.append(
                self._candidate(
                    user_id=user_id,
                    memory_type=MemoryType.FACT,
                    content=f"Пользователь работает {profession}",
                    confidence=0.99,
                    source=source,
                    source_text=text,
                    metadata={
                        "subject": "Пользователь",
                        "relations": [
                            {
                                "subject": "Пользователь",
                                "predicate": "работать_как",
                                "object": profession,
                            }
                        ],
                    },
                )
            )
            return candidates

        return []

    # =========================================================
    # MAIN
    # =========================================================

    def extract(
        self,
        text: str,
        user_id: str,
        source: Source | None = None,
        context: MemoryContext | None = None,
    ) -> list[MemoryCandidate]:
        text = text.strip()
        if not text:
            return []

        if source is None:
            source = Source(
                type="text",
                id=str(uuid.uuid4()),
            )

        # 1. Deterministic rules for obvious user statements.
        rule_candidates = self._extract_rule_based(
            text=text,
            user_id=user_id,
            source=source,
        )
        if rule_candidates:
            for candidate in rule_candidates:
                candidate.metadata["extraction_source"] = "rule"
            return rule_candidates

        # 2. Deterministic contextual rules for pronouns and implicit subjects.
        context_rule_candidates = self._extract_context_rule_based(
            text=text,
            user_id=user_id,
            source=source,
            context=context,
        )
        if context_rule_candidates:
            for candidate in context_rule_candidates:
                candidate.metadata["extraction_source"] = "context_rule"
            return context_rule_candidates

        # 3. LLM extraction with contextual grounding.
        memory_context_text = self._format_memory_context(context)
        context_entities = self._context_entities(context)
        entity_hint = ", ".join(context_entities) if context_entities else "NONE"

        user_prompt = f"""
CURRENT USER MESSAGE:
{text}

MEMORY CONTEXT:
{memory_context_text}

KNOWN ENTITIES FROM MEMORY CONTEXT:
{entity_hint}

Задача:
1. Извлеки новую долговременную информацию только из CURRENT USER MESSAGE.
2. Если в сообщении есть местоимение (например, "он", "ним", "ему"),
   разреши его по MEMORY CONTEXT и запиши конкретное имя сущности.
3. Если субъект не указан явно (например, "Работаю программистом"),
   восстанови субъект как "Пользователь".
4. Для отношений указывай конкретные subject/object, а не местоимения.
5. "Работаю программистом" — это fact, не preference.
6. "Он работает программистом" при antecedent=Иван должно стать
   "Иван работает программистом".
7. "Вчера я встретился с ним" при antecedent=Иван должно стать
   "Пользователь встретился с Иваном".
8. Не создавай candidate только из MEMORY CONTEXT.

Верни только JSON-массив.
""".strip()

        response = self.qwen.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_new_tokens=512,
        )

        candidates = self._parse(
            response=response,
            user_id=user_id,
            source=source,
            source_text=text,
        )

        # 4. Canonicalize every candidate using current context.
        result: list[MemoryCandidate] = []
        for candidate in candidates:
            result.append(
                self._canonicalize_candidate(
                    candidate=candidate,
                    context=context,
                )
            )

        return result
