from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from extractor_contextual import MemoryExtractor
from memory_context import MemoryContext
from models import MemoryCandidate, MemoryType, Source
from qwen_model import QwenModel
from memory_input_router import MemoryInputRouter


@dataclass
class UnifiedAnalysis:
    candidates: list[MemoryCandidate]
    relations: list[dict[str, Any]]
    used_llm: bool


class UnifiedMemoryAnalyzer:
    """One Qwen3-4B call for extraction, validation and relations."""

    SYSTEM_PROMPT = """
Ты извлекаешь долговременную память из CURRENT USER MESSAGE.

Разрешённые типы:
 fact, preference, episode, entity, relationship, goal, state, procedure.

Сохраняй только то, что полезно в будущих диалогах.
Не сохраняй вопросы, команды, разовые операционные действия и пустые фразы.
Не сохраняй информацию только из MEMORY CONTEXT.

Типизация:
- факт о пользователе или другом человеке -> fact;
- явное "люблю", "нравится", "предпочитаю" -> preference;
- желание, намерение, план -> goal;
- текущее измеримое состояние -> state;
- значимое завершённое событие -> episode;
- устойчивое отношение между сущностями -> relationship;
- повторяемая привычка/действие без явного предпочтения -> procedure.

Правила:
- "Я живу в Москве" -> fact.
- "Я работаю аналитиком" -> fact.
- "Сейчас я вешу 82,5 килограмма" -> state.
- "Я хочу начать бегать" -> goal, не preference.
- "Мне нравится бегать" -> preference.
- "По выходным стараюсь ездить на велосипеде" -> procedure.
- "По выходным люблю ездить на велосипеде" -> preference.
- "Поэтому времени немного" -> не сохраняй отдельно.
- Команды не являются памятью.
- В смешанном сообщении сохраняй только полезные сведения и полностью игнорируй командную часть.
- Каждое независимое утверждение = отдельный candidate.
- Не объединяй разные факты в один candidate.

СУБЪЕКТ:
- Если действие относится к пользователю, используй "Пользователь".
- Разрешай местоимения по MEMORY CONTEXT.
- "Мой брат Антон" даёт relationship: Антон -> быть_братом -> Пользователь.

ПЕРЕПИСКА, ПИСЬМА, СОБЫТИЯ (текст начинается с «Переписка в Telegram», «Входящее письмо», «Событие в календаре»):
- «Пользователь» — ТОЛЬКО автор реплик с пометкой «(пользователь)», автор исходящего письма
  или адресат входящего. Собеседник, говорящий о СЕБЕ («я в Москве закрутился», «я переезжаю
  в Казань», «мне предложили работу»), сообщает факт о СЕБЕ: пиши его имя
  («Антон живёт в Москве», «Лена переезжает в Казань»), НИКОГДА не «Пользователь».
- Вопросы собеседника («тебе уже 30?», «ты переезжаешь?») — не факты, пока пользователь не подтвердил.
- Шутки, сарказм, гипотетическое («если бы», «чисто гипотетически», «а если»), подколки
  («ты теперь миллионерша») — не сохраняй.
- Реклама, рассылки и письма незнакомых отправителей не меняют личные данные пользователя
  (город, адрес, дату рождения, работу) — такие утверждения не сохраняй.
- Измеримое состояние (вес, норма калорий, баланс, цель) сохраняй с датой из текста
  («В августе 2026 вес пользователя — 57,8 кг»), чтобы было видно, какое значение новее.
- Не сохраняй голые даты и служебные строки события без смысла («Дата события: 24 сентября»):
  факт должен говорить, ЧТО и КОГДА («Демо для клиента перенесено на 29.09.2026»).

НОРМАЛИЗАЦИЯ:
- короткая естественная русская фраза;
- субъект должен быть указан, когда он известен;
- не используй "является", если естественнее тире: "Антон — брат пользователя";
- не добавляй факты, которых нет в тексте;
- confidence 0..1.

ОТНОШЕНИЯ НЕ вкладывай в candidates.
Все отношения помещай только в верхнеуровневый массив "relations".

Верни ТОЛЬКО компактный JSON БЕЗ MARKDOWN:
{
 "candidates":[
   {"type":"fact","content":"Пользователь живёт в Москве","confidence":0.95},
   {"type":"goal","content":"Пользователь хочет начать бегать по утрам","confidence":0.94},
   {"type":"procedure","content":"Пользователь ездит на велосипеде по выходным","confidence":0.88}
 ],
 "relations":[
   {"subject":"Антон","predicate":"быть_братом","object":"Пользователь","source_fragment":"Мой брат Антон"}
 ]
}

Не добавляй поля reason, keep, relations внутрь candidate и другие поля.
Если сохранять нечего: {"candidates":[],"relations":[]}.
""".strip()

    def __init__(self, qwen: QwenModel, extractor: MemoryExtractor) -> None:
        self.qwen = qwen
        self.extractor = extractor
        self.input_router = MemoryInputRouter(extractor)

    @staticmethod
    def _clean_response(response: str) -> str:
        text = (response or "").strip()
        text = re.sub(
            r"<think>.*?</think>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if text.startswith("json"):
            text = text[4:].lstrip()

        return text

    @classmethod
    def _parse_json(cls, response: str) -> dict[str, Any]:
        text = cls._clean_response(response)

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        if start < 0:
            raise ValueError("Unified analyzer returned no JSON object")

        depth = 0
        in_string = False
        escaped = False
        last_complete = -1

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    last_complete = index
                    break

        if last_complete >= 0:
            data = json.loads(text[start:last_complete + 1])
            if isinstance(data, dict):
                return data

        # Qwen may stop in the middle of a long candidate. In that case,
        # try to recover complete candidate objects from the array.
        marker = '"candidates"'
        candidates_pos = text.find(marker, start)
        if candidates_pos >= 0:
            array_start = text.find("[", candidates_pos)
            if array_start >= 0:
                objects: list[dict[str, Any]] = []
                depth = 0
                object_start = -1
                in_string = False
                escaped = False

                for index in range(array_start + 1, len(text)):
                    char = text[index]

                    if in_string:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == '"':
                            in_string = False
                        continue

                    if char == '"':
                        in_string = True
                        continue

                    if char == "{":
                        if depth == 0:
                            object_start = index
                        depth += 1
                    elif char == "}":
                        if depth > 0:
                            depth -= 1
                            if depth == 0 and object_start >= 0:
                                fragment = text[object_start:index + 1]
                                try:
                                    item = json.loads(fragment)
                                    if isinstance(item, dict):
                                        objects.append(item)
                                except json.JSONDecodeError:
                                    pass
                                object_start = -1

                if objects:
                    return {
                        "candidates": objects,
                        "relations": [],
                        "recovered_truncated": True,
                    }

        raise ValueError(
            "Unified analyzer returned incomplete JSON:\n" + response
        )

    @staticmethod
    def _source(source: Source | None) -> Source:
        return source or Source(
            type="text",
            id=str(uuid.uuid4()),
        )

    @staticmethod
    def _safe_confidence(value: Any, default: float = 0.5) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value))

    @staticmethod
    def _safe_type(value: Any) -> MemoryType | None:
        if not isinstance(value, str):
            return None
        try:
            return MemoryType(value.strip().lower())
        except ValueError:
            return None

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        return [
            part.strip()
            for part in re.split(
                r"(?<=[.!?])\s+",
                text.strip(),
            )
            if part.strip()
        ]

    @classmethod
    def _is_command_sentence(cls, sentence: str) -> bool:
        sentence = sentence.strip(" .!?\n")
        if not sentence:
            return False

        first = re.findall(r"[А-Яа-яЁёA-Za-z]+", sentence)
        if not first:
            return False

        word = first[0].lower()
        return bool(
            re.search(
                r"(?:й|и|ь|йте|тесь|ись)$",
                word,
            )
        )

    @classmethod
    def _remove_command_sentences(cls, content: str) -> str:
        parts = cls._split_sentences(content)
        if len(parts) < 2:
            return content.strip()

        kept = [
            part
            for part in parts
            if not cls._is_command_sentence(part)
        ]
        return " ".join(kept).strip()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stop = {
            "пользователь", "я", "мне", "меня", "мой", "моя", "мои",
            "его", "ее", "её", "он", "она", "они", "ему", "ей", "им",
            "ним", "ней", "них", "в", "во", "на", "по", "с", "со",
            "к", "у", "для", "из", "от", "до", "за", "и", "а", "но",
            "это", "тот", "та", "те", "что", "как", "сейчас", "уже",
            "теперь", "обычно", "еще", "ещё", "очень",
        }
        return {
            token
            for token in re.findall(
                r"[a-zа-яё0-9]+",
                (text or "").lower().replace("ё", "е"),
            )
            if len(token) >= 3 and token not in stop
        }

    @classmethod
    def _has_source_evidence(cls, candidate: MemoryCandidate) -> bool:
        source = cls._tokens(candidate.source_text)
        content = cls._tokens(candidate.content)
        return bool(source and content and source & content)

    @classmethod
    def _normalize_candidate_content(
        cls,
        memory_type: MemoryType,
        content: str,
    ) -> str:
        content = re.sub(r"\s+", " ", content).strip(" .!?")

        replacements = (
            (r"^Пользователь\s+хочет\s+начать\s+", "Пользователь хочет начать "),
            (r"^Пользователь\s+хотел\s+начать\s+", "Пользователь хотел начать "),
            (r"^Пользователь\s+желает\s+", "Пользователь хочет "),
            (r"^Пользователь\s+предпочитает\s+", "Пользователь предпочитает "),
            (r"^Пользователь\s+любит\s+", "Пользователь любит "),
        )

        for pattern, replacement in replacements:
            content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

        if memory_type == MemoryType.RELATIONSHIP:
            match = re.match(
                r"^(.+?)\s+(?:является|есть)\s+(.+?)$",
                content,
                flags=re.IGNORECASE,
            )
            if match:
                subject = match.group(1).strip()
                predicate = match.group(2).strip()
                return f"{subject} — {predicate}"

        return content

    @staticmethod
    def _drop_low_value_candidates(
        candidates: list[MemoryCandidate],
    ) -> list[MemoryCandidate]:
        low_value_patterns = (
            r"\bвремени\s+(?:немного|мало)\b",
            r"\bвремени\s+не хватает\b",
            r"\bсейчас\s+много\s+дел\b",
        )

        result: list[MemoryCandidate] = []

        for candidate in candidates:
            content = candidate.content.lower().replace("ё", "е")
            if any(re.search(pattern, content) for pattern in low_value_patterns):
                continue
            result.append(candidate)

        return result

    @staticmethod
    def _reclassify_candidate(
        candidate: MemoryCandidate,
    ) -> MemoryCandidate:
        content = candidate.content.lower().replace("ё", "е")

        if candidate.type == MemoryType.PROCEDURE:
            explicit_preference = re.search(
                r"\b(люблю|нравится|предпочитаю|предпочитает|любит)\b",
                content,
            )
            if explicit_preference:
                candidate.type = MemoryType.PREFERENCE

        if candidate.type == MemoryType.PREFERENCE:
            goal_markers = re.search(
                r"\b(хочу|хочет|планирую|планирует|собираюсь|собирается|намерен|намерена)\b",
                content,
            )
            if goal_markers:
                candidate.type = MemoryType.GOAL

        return candidate

    @staticmethod
    def _normalize_relation(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None

        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        object_value = str(item.get("object") or "").strip()

        if not subject or not predicate or not object_value:
            return None

        return {
            "subject": subject,
            "predicate": re.sub(r"\s+", "_", predicate.lower()),
            "object": object_value,
            "source_fragment": str(
                item.get("source_fragment") or ""
            ).strip(),
            "confidence": UnifiedMemoryAnalyzer._safe_confidence(
                item.get("confidence"),
                0.8,
            ),
        }

    def _build_candidate(
        self,
        item: dict[str, Any],
        user_id: str,
        source: Source,
        source_text: str,
    ) -> MemoryCandidate | None:
        memory_type = self._safe_type(item.get("type"))
        content = item.get("content")

        if (
            memory_type is None
            or not isinstance(content, str)
            or not content.strip()
        ):
            return None

        content = self._remove_command_sentences(content)
        if not content:
            return None

        content = self._normalize_candidate_content(
            memory_type=memory_type,
            content=content,
        )
        if not content:
            return None

        raw_relations = item.get("relations", [])
        relations: list[dict[str, Any]] = []

        if isinstance(raw_relations, list):
            for raw in raw_relations:
                normalized = self._normalize_relation(raw)
                if normalized is not None:
                    if not normalized["source_fragment"]:
                        normalized["source_fragment"] = source_text
                    relations.append(normalized)

        metadata = {
            "extraction_source": "unified_llm",
            "validation_source": "unified_llm",
        }
        if relations:
            metadata["relations"] = relations

        return MemoryCandidate(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=memory_type,
            content=content,
            confidence=self._safe_confidence(
                item.get("confidence"),
                0.7,
            ),
            source=source,
            created_at=datetime.utcnow(),
            source_text=source_text,
            metadata=metadata,
        )

    @staticmethod
    def _has_command_like_fragment(text: str) -> bool:
        sentences = re.split(
            r"(?<=[.!?])\s+",
            text.strip(),
        )
        if len(sentences) < 2:
            return False

        pattern = re.compile(
            r"^(?:[А-Яа-яЁё]+(?:й|и|ь|йте|тесь|ись))\b",
            re.IGNORECASE,
        )

        return any(
            pattern.search(sentence.strip())
            for sentence in sentences
        )

    def _looks_like_safe_rule_result(
        self,
        text: str,
        candidates: list[MemoryCandidate],
    ) -> bool:
        if not candidates:
            return False

        if self._has_command_like_fragment(text):
            return False

        # Do not use rules for multi-sentence/context-heavy input.
        if len(self._split_sentences(text)) > 1:
            return False

        if any(
            len(self._split_sentences(candidate.content)) > 1
            for candidate in candidates
        ):
            return False

        return True

    def _rule_path(
        self,
        text: str,
        user_id: str,
        source: Source,
        context: MemoryContext | None,
    ) -> UnifiedAnalysis | None:
        route = self.input_router.route(
            text=text,
            user_id=user_id,
            context=context,
            source=source,
        )

        print(
            f"[ROUTER] {route.kind} | {route.reason}"
        )

        if route.kind == "DROP":
            return UnifiedAnalysis(
                candidates=[],
                relations=[],
                used_llm=False,
            )

        if route.kind == "FAST":
            for candidate in route.candidates:
                candidate.metadata[
                    "extraction_source"
                ] = "rule_router"

            return UnifiedAnalysis(
                candidates=route.candidates,
                relations=[],
                used_llm=False,
            )

        return None

    def _context_entities(
        self,
        context: MemoryContext | None,
    ) -> str:
        entities = self.extractor._context_entities(context)
        return ", ".join(entities) if entities else "NONE"

    def analyze(
        self,
        text: str,
        user_id: str,
        context: MemoryContext | None = None,
        source: Source | None = None,
    ) -> UnifiedAnalysis:
        text = text.strip()
        if not text:
            return UnifiedAnalysis([], [], False)

        source = self._source(source)

        rule_result = self._rule_path(
            text=text,
            user_id=user_id,
            source=source,
            context=context,
        )
        if rule_result is not None:
            return rule_result

        memory_context = self.extractor._format_memory_context(context)
        entity_hint = self._context_entities(context)

        user_prompt = f"""
CURRENT USER MESSAGE:
{text}

MEMORY CONTEXT:
{memory_context}

KNOWN ENTITIES:
{entity_hint}

Проанализируй только CURRENT USER MESSAGE.
Каждое независимое утверждение сохрани отдельно.
Команды и вопросы игнорируй.
""".strip()

        response = self.qwen.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_new_tokens=320,
        )

        data = self._parse_json(response)
        raw_candidates = data.get("candidates", [])

        if not isinstance(raw_candidates, list):
            raise ValueError(
                "Unified analyzer: candidates must be a list"
            )

        candidates: list[MemoryCandidate] = []
        all_relations: list[dict[str, Any]] = []

        for item in raw_candidates:
            if not isinstance(item, dict):
                continue

            candidate = self._build_candidate(
                item=item,
                user_id=user_id,
                source=source,
                source_text=text,
            )
            if candidate is None:
                continue

            if not self._has_source_evidence(candidate):
                continue

            candidate = self._reclassify_candidate(candidate)
            candidates.append(candidate)

            relations = (candidate.metadata or {}).get(
                "relations",
                [],
            )
            if isinstance(relations, list):
                all_relations.extend(relations)

        # Top-level relations are also accepted by the compact schema.
        top_relations = data.get("relations", [])
        if isinstance(top_relations, list):
            for raw_relation in top_relations:
                relation = self._normalize_relation(raw_relation)
                if relation is not None:
                    if not relation["source_fragment"]:
                        relation["source_fragment"] = text
                    all_relations.append(relation)

        candidates = self._drop_low_value_candidates(candidates)

        return UnifiedAnalysis(
            candidates=candidates,
            relations=all_relations,
            used_llm=True,
        )
