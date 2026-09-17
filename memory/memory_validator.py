from __future__ import annotations

import json
from dataclasses import dataclass

from models import MemoryCandidate, MemoryType
from qwen_model import QwenModel


@dataclass(frozen=True)
class ValidationResult:
    candidate: MemoryCandidate
    keep: bool
    reason: str


class MemoryValidator:
    """Validates extracted memory candidates before enrichment/storage."""

    SYSTEM_PROMPT = """
Ты проверяешь кандидатов долговременной памяти персонального AI-агента.

Для каждого кандидата реши, действительно ли его нужно сохранять в
долговременную память пользователя.

СОХРАНЯЙ кандидата, только если одновременно выполняются условия:
1. Это информация именно о пользователе или его устойчивых отношениях,
   целях, предпочтениях, состояниях, привычках или значимых событиях.
2. Информация потенциально полезна в будущих диалогах.
3. Это не обычный вопрос пользователя.
4. Это не команда ассистенту выполнить действие.
5. Это не кратковременное состояние процесса выполнения команды.
6. Это не служебная фраза и не бессодержательный фрагмент.
7. Кандидат действительно подтверждается CURRENT USER MESSAGE.

НЕ СОХРАНЯЙ, например:
- "Поставь будильник на 18 часов";
- "Стоит ли сейчас будильник?";
- "Открой приложение";
- бессодержательные фрагменты вроде "модный", если непонятно,
  что именно пользователь предпочитает;
- разовые операционные действия, которые не имеют долговременной
  ценности.

СОХРАНЯЙ, например:
- "Я живу в Москве";
- "Я люблю кофе без сахара";
- "Мой брат Антон живёт в Казани";
- "Я планирую начать новый проект в следующем месяце";
- "Сегодня я встретился с братом" — как episode, если событие
  действительно значимо и относится к пользователю.

КРИТИЧЕСКИ ВАЖНО:
- Не придумывай новую информацию.
- Не расширяй смысл кандидата.
- Не превращай вопрос в факт.
- Не превращай команду в цель или воспоминание.
- Не сохраняй только потому, что кандидат имеет допустимый type.
- Если кандидат можно сделать корректнее без изменения смысла,
  можешь вернуть исправленный content.
- Исправленный content должен быть кратким, каноническим и
  грамматически корректным.
- Если смысл кандидата уже корректен, оставь content без изменений.
- Не меняй source_text.

Верни ТОЛЬКО JSON-массив:
[
  {
    "id": "candidate_id",
    "keep": true,
    "reason": "persistent_personal_information",
    "type": "fact",
    "content": "..."
  }
]

Для keep=false поля type и content должны повторять исходный кандидат.
Для keep=true type должен быть одним из разрешённых типов:
fact, preference, episode, entity, relationship, goal, state, procedure.
""".strip()

    def __init__(self, qwen: QwenModel) -> None:
        self.qwen = qwen

    @staticmethod
    def _clean_response(response: str) -> str:
        response = response.strip()
        if response.startswith("```"):
            lines = response.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "````":
                lines = lines[:-1]
            elif lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response = "\n".join(lines).strip()

        start = response.find("[")
        end = response.rfind("]")
        if start != -1 and end != -1 and end >= start:
            return response[start : end + 1]

        return response

    @staticmethod
    def _allowed_type(value: object, fallback: MemoryType) -> MemoryType:
        if isinstance(value, str):
            value = value.strip().lower()
            try:
                return MemoryType(value)
            except ValueError:
                pass
        return fallback

    @staticmethod
    def _normalized_content(value: object, fallback: str) -> str:
        if not isinstance(value, str):
            return fallback
        value = value.strip()
        return value if value else fallback

    def validate(
        self,
        candidates: list[MemoryCandidate],
        source_text: str,
    ) -> list[ValidationResult]:
        if not candidates:
            return []

        candidate_payload = [
            {
                "id": candidate.id,
                "type": candidate.type.value,
                "content": candidate.content,
            }
            for candidate in candidates
        ]

        user_prompt = (
            "CURRENT USER MESSAGE:\n"
            f"{source_text}\n\n"
            "CANDIDATES:\n"
            f"{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}"
        )

        response = self.qwen.generate(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_new_tokens=768,
        )

        try:
            data = json.loads(self._clean_response(response))
        except (json.JSONDecodeError, TypeError, ValueError):
            # Conservative fallback: do not let validator failure create
            # new behavior. Keep original extractor output unchanged.
            return [
                ValidationResult(
                    candidate=candidate,
                    keep=True,
                    reason="validator_parse_failed",
                )
                for candidate in candidates
            ]

        if not isinstance(data, list):
            return [
                ValidationResult(
                    candidate=candidate,
                    keep=True,
                    reason="validator_invalid_format",
                )
                for candidate in candidates
            ]

        by_id = {
            candidate.id: candidate
            for candidate in candidates
        }

        results: list[ValidationResult] = []
        seen_ids: set[str] = set()

        for item in data:
            if not isinstance(item, dict):
                continue

            candidate_id = item.get("id")
            if not isinstance(candidate_id, str):
                continue

            candidate = by_id.get(candidate_id)
            if candidate is None or candidate_id in seen_ids:
                continue

            seen_ids.add(candidate_id)

            keep = bool(item.get("keep", False))
            reason = str(item.get("reason", "unspecified")).strip()

            if keep:
                candidate.type = self._allowed_type(
                    item.get("type"),
                    candidate.type,
                )
                candidate.content = self._normalized_content(
                    item.get("content"),
                    candidate.content,
                )

            results.append(
                ValidationResult(
                    candidate=candidate,
                    keep=keep,
                    reason=reason,
                )
            )

        # Be conservative about missing validator decisions:
        # keep original candidates rather than silently dropping them.
        for candidate in candidates:
            if candidate.id in seen_ids:
                continue
            results.append(
                ValidationResult(
                    candidate=candidate,
                    keep=True,
                    reason="validator_missing_candidate",
                )
            )

        return results
