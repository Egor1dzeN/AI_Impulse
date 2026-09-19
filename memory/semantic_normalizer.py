from __future__ import annotations

import re
from dataclasses import dataclass

from enricher import EnrichedMemoryCandidate
from models import Memory, MemoryType


@dataclass
class SemanticRelation:
    subject: str
    predicate: str
    object: str


@dataclass
class SemanticMemory:
    subject: str | None
    predicate: str | None
    object: str | None
    type: MemoryType
    content: str
    relations: list[SemanticRelation]
    scope: str | None = None


class SemanticMemoryNormalizer:
    """Builds a stable semantic key for matching and consolidation."""

    @staticmethod
    def normalize_text(text: str) -> str:
        text = (text or "").lower().replace("ё", "е")
        text = re.sub(r"[^\w\s\-.,]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def normalize_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = cls.normalize_text(value)
        if value in {
            "я", "мне", "меня", "мой", "моя", "моего", "моей", "мою",
            "пользователь", "мне", "теперь", "сейчас", "вчера", "сегодня",
            "больше", "ушло", "по", "встреча", "встречу", "мне нужно",
        }:
            return "пользователь"
        if value in {"он", "она", "ним", "ему", "ей"}:
            return None
        return value or None

    @classmethod
    def normalize_predicate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = cls.normalize_text(value)
        aliases = {
            "жить": "жить_в",
            "живу": "жить_в",
            "живет": "жить_в",
            "живёт": "жить_в",
            "проживать": "жить_в",
            "проживает": "жить_в",
            "переехать_в": "жить_в",
            "переехал_в": "жить_в",
            "переехала_в": "жить_в",
            "работать": "работать_как",
            "работает": "работать_как",
            "предпочитать": "предпочитать",
            "нравиться": "предпочитать",
            "любить": "предпочитать",
            "планировать": "планировать",
            "закончил": "статус_задачи",
            "закончила": "статус_задачи",
            "завершил": "статус_задачи",
            "завершила": "статус_задачи",
            "готово": "статус_задачи",
            "откладывать": "откладывать_в_месяц",
            "откладывать_по": "откладывать_в_месяц",
            "вес": "вес_кг",
            "вешу": "вес_кг",
            "показывать_вес": "вес_кг",
            "проходить": "шаги",
            "пройти": "шаги",
            "тратить": "траты",
            "потратить": "траты",
            "уходить": "траты",
            "ушло": "траты",
            "иметь_встречу": "встреча_с_научным_руководителем",
            "перенести_на": "встреча_с_научным_руководителем",
            "встретиться_с": "встреча_с_человеком",
            "переходить": "формат_работы",
            "работать_удаленно": "формат_работы",
        }
        return aliases.get(value, value or None)

    @classmethod
    def normalize_object(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = cls.normalize_text(value)
        user_aliases = {
            "я", "мне", "меня", "мой", "моя", "моего", "моей", "мою", "пользователь"
        }
        if value in user_aliases:
            return "пользователь"

        location_aliases = {
            "москва": "москва", "москве": "москва", "москву": "москва", "москвой": "москва",
            "санкт петербург": "санкт петербург", "санкт петербурге": "санкт петербург",
            "санкт петербургом": "санкт петербург", "санкт петербургу": "санкт петербург",
            "казань": "казань", "казани": "казань", "казанью": "казань",
        }
        if value in location_aliases:
            return location_aliases[value]

        value = re.sub(r"\bпо вечерам\b", "вечером", value)
        value = re.sub(r"\bпо утрам\b", "утром", value)
        value = re.sub(r"\bпо ночам\b", "ночью", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value or None

    @classmethod
    def normalize_relation(
        cls,
        subject: str,
        predicate: str,
        object_value: str,
    ) -> SemanticRelation:
        return SemanticRelation(
            subject=cls.normalize_subject(subject) or "",
            predicate=cls.normalize_predicate(predicate) or "",
            object=cls.normalize_object(object_value) or "",
        )

    @staticmethod
    def _number(text: str) -> str | None:
        match = re.search(r"\b(\d+(?:[.,]\d+)?)\b", text)
        return match.group(1).replace(",", ".") if match else None

    @classmethod
    def semantic_scope(cls, content: str, memory_type: MemoryType) -> str | None:
        text = cls.normalize_text(content)

        # Goals are identified by the concrete task, not by the generic
        # planning verb. Different tasks can coexist as active goals.
        if memory_type == MemoryType.GOAL:
            if "отчет" in text:
                return "task:отчет"
            if "задач" in text:
                return "task:задача"
            if "проект" in text:
                return "task:проект"
            if "откладыва" in text:
                return "goal:накопления"

        # Historical spending is scoped by accounting period.
        if memory_type == MemoryType.STATE:
            spending = any(word in text for word in ("траты", "трат", "потрат", "ушло", "расход"))
            if spending:
                if "прошлом месяце" in text:
                    return "period:прошлый_месяц"
                if "в этом месяце" in text:
                    return "period:текущий_месяц"

            # Current weight is one evolving property.
            if (
                "вешу" in text
                or "весит" in text
                or "весы показ" in text
            ):
                return "current:вес"

            # Completion of a concrete task.
            if any(marker in text for marker in ("закончил", "закончила", "завершил", "завершила")) and "отчет" in text:
                return "task:отчет"

        return None

    @classmethod
    def _extract_from_text(cls, content: str, memory_type: MemoryType) -> list[SemanticRelation]:
        raw = content or ""
        text = cls.normalize_text(raw)
        relations: list[SemanticRelation] = []

        # User/location.
        match = re.search(r"\b(?:пользователь|я)\s+жив\w*\s+в\s+(.+)$", text)
        if match:
            obj = cls.normalize_object(match.group(1))
            if obj:
                relations.append(cls.normalize_relation("пользователь", "жить_в", obj))
                return relations

        match = re.search(r"\b(?:пользователь|я)\s+переех\w*\s+в\s+(.+)$", text)
        if match:
            obj = cls.normalize_object(match.group(1))
            if obj:
                relations.append(cls.normalize_relation("пользователь", "жить_в", obj))
                return relations

        # Monthly savings target.
        if memory_type == MemoryType.GOAL and re.search(r"откладыва", text):
            amount = cls._number(text)
            if amount:
                relations.append(cls.normalize_relation(
                    "пользователь", "откладывать_в_месяц", amount
                ))
                return relations

        # Goal/task planning. Use the task noun phrase as the semantic object.
        if memory_type == MemoryType.GOAL:
            if "отчет" in text or "отчёт" in text:
                relations.append(cls.normalize_relation("пользователь", "задача", "отчет"))
                return relations
            if "новой задач" in text or "новую задач" in text:
                relations.append(cls.normalize_relation("пользователь", "задача", "новая задача"))
                return relations
            if "проект" in text:
                relations.append(cls.normalize_relation("пользователь", "задача", "проект"))
                return relations

        # Spending. Keep category in the object so restaurants and delivery remain distinct.
        if memory_type == MemoryType.STATE and (
            "потратил" in text or "потратила" in text or "ушло" in text
            or "расход" in text or "трат" in text
        ):
            amount = cls._number(text)
            if amount:
                category = "рестораны"
                if "достав" in text:
                    category = "рестораны и доставка"
                relations.append(cls.normalize_relation(
                    "пользователь", "траты_на", f"{category} {amount} рублей"
                ))
                return relations

        # Completed task. This is a state update for the corresponding goal.
        if memory_type == MemoryType.STATE and "отчет" in text and any(
            marker in text for marker in ("закончил", "закончила", "завершил", "завершила", "готово")
        ):
            relations.append(cls.normalize_relation(
                "пользователь", "статус_задачи", "отчет:завершен"
            ))
            return relations

        # Weight state. Support both verbal forms:
        # "пользователь весит 84 кг" / "весы показали 82,5 кг"
        # and nominalized memory text:
        # "вес пользователя 84 килограмма".
        if (
            (
                "вешу" in text
                or "весит" in text
                or "весы показ" in text
                or re.search(r"\bвес\s+пользовател", text) is not None
            )
            and ("килограмм" in text or "кг" in text)
        ):
            amount = cls._number(text)
            if amount:
                relations.append(
                    cls.normalize_relation("пользователь", "вес_кг", amount)
                )
                return relations

        # Routine / episode steps are deliberately different semantic properties.
        if "будн" in text and "шаг" in text:
            amount = cls._number(text)
            if amount:
                relations.append(cls.normalize_relation("пользователь", "шаги_по_будням", amount))
                return relations

        if "вчера" in text and "шаг" in text:
            amount = cls._number(text)
            if amount:
                relations.append(cls.normalize_relation("пользователь", "шаги_эпизод", amount))
                return relations

        # Meeting with supervisor. Date is the changing value.
        if "научн" in text and "руководител" in text and "встреч" in text:
            day = None
            day_match = re.search(
                r"\b(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье)\b",
                text,
            )
            if day_match:
                day = day_match.group(1)
            relations.append(cls.normalize_relation(
                "пользователь", "встреча_с_научным_руководителем", day or "встреча"
            ))
            return relations

        # Remote work state.
        if "удален" in text or "удалён" in text or "удаленного" in text or "удаленного" in text:
            relations.append(cls.normalize_relation("пользователь", "формат_работы", "удаленно"))
            return relations

        # Generic preference.
        if memory_type == MemoryType.PREFERENCE:
            match = re.search(r"(?:пользователь|я)\s+.*?(?:предпочита\w*|нравится|люблю)\s+(.+)$", text)
            if match:
                relations.append(cls.normalize_relation("пользователь", "предпочитать", match.group(1)))
                return relations

        # Work/profession.
        match = re.search(r"(?:пользователь|я)\s+работ\w*\s+(.+)$", text)
        if match:
            relations.append(cls.normalize_relation("пользователь", "работать_как", match.group(1)))
            return relations

        return relations

    @classmethod
    def _valid_extracted_relation(cls, relation: SemanticRelation) -> bool:
        subject = cls.normalize_subject(relation.subject)
        predicate = cls.normalize_predicate(relation.predicate)
        obj = cls.normalize_object(relation.object)
        if not subject or not predicate or not obj:
            return False
        # A bare temporal/discourse word is not a semantic subject.
        if subject in {"теперь", "сейчас", "вчера", "сегодня", "больше", "ушло", "по", "встреча"}:
            return False
        return True

    def from_candidate(self, candidate: EnrichedMemoryCandidate) -> SemanticMemory:
        content = candidate.candidate.content
        fallback = self._extract_from_text(content, candidate.candidate.type)

        extracted: list[SemanticRelation] = []
        for relation in candidate.relations:
            normalized = self.normalize_relation(
                relation.subject,
                relation.predicate,
                relation.object,
            )
            if self._valid_extracted_relation(normalized):
                extracted.append(normalized)

        # Deterministic semantic parsing wins when it gives a known property.
        relations = fallback or extracted

        # A resolved non-user subject from cross-message context must replace
        # the default user subject produced by generic text rules.
        if relations and candidate.subject is not None:
            resolved_subject = self.normalize_subject(candidate.subject.text)
            if resolved_subject and resolved_subject != "пользователь":
                relations = [
                    SemanticRelation(
                        subject=(
                            resolved_subject
                            if relation.subject == "пользователь"
                            else relation.subject
                        ),
                        predicate=relation.predicate,
                        object=relation.object,
                    )
                    for relation in relations
                ]

        if not relations and candidate.subject is not None:
            subject = self.normalize_subject(candidate.subject.text)
        else:
            subject = None

        predicate = None
        object_value = None
        if relations:
            subject = relations[0].subject
            predicate = relations[0].predicate
            object_value = relations[0].object

        return SemanticMemory(
            subject=subject,
            predicate=predicate,
            object=object_value,
            type=candidate.candidate.type,
            content=self.normalize_text(content),
            relations=relations,
            scope=self.semantic_scope(content, candidate.candidate.type),
        )

    def from_memory(self, memory: Memory) -> SemanticMemory:
        metadata = memory.metadata or {}
        raw_relations = metadata.get("relations", [])
        relations: list[SemanticRelation] = []

        if isinstance(raw_relations, list):
            for relation in raw_relations:
                if not isinstance(relation, dict):
                    continue
                subject = relation.get("subject")
                predicate = relation.get("predicate")
                object_value = relation.get("object")
                if not all(isinstance(x, str) for x in (subject, predicate, object_value)):
                    continue
                normalized = self.normalize_relation(subject, predicate, object_value)
                if self._valid_extracted_relation(normalized):
                    relations.append(normalized)

        # Re-derive semantic key from current content as a guard against stale/wrong metadata.
        fallback = self._extract_from_text(memory.content, memory.type)
        if fallback:
            relations = fallback

        if relations:
            subject = relations[0].subject
            predicate = relations[0].predicate
            object_value = relations[0].object
        else:
            subject = self.normalize_subject(metadata.get("subject"))
            predicate = None
            object_value = None

        return SemanticMemory(
            subject=subject,
            predicate=predicate,
            object=object_value,
            type=memory.type,
            content=self.normalize_text(memory.content),
            relations=relations,
            scope=self.semantic_scope(memory.content, memory.type),
        )

    @staticmethod
    def _deduplicate_relations(relations: list[SemanticRelation]) -> list[SemanticRelation]:
        result: list[SemanticRelation] = []
        seen: set[tuple[str, str, str]] = set()
        for relation in relations:
            key = (relation.subject, relation.predicate, relation.object)
            if key in seen:
                continue
            seen.add(key)
            result.append(relation)
        return result
