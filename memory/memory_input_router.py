from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import uuid

from extractor_contextual import MemoryExtractor
from memory_context import MemoryContext
from models import MemoryCandidate, MemoryType, Source


@dataclass
class InputRoute:
    kind: str
    candidates: list[MemoryCandidate]
    reason: str


class MemoryInputRouter:
    """
    Быстрый маршрутизатор входного сообщения.

    DROP:
        очевидный вопрос или команда.

    FAST:
        сообщение можно обработать существующими deterministic rules
        без Qwen3-4B.

    UNIFIED:
        требуется UnifiedMemoryAnalyzer / Qwen3-4B.
    """

    COMMAND_FIRST_WORD = re.compile(
        r"^[А-Яа-яЁёA-Za-z]+(?:й|и|ь|йте|тесь|ись)$",
        re.IGNORECASE,
    )

    QUESTION_WORDS = {
        "кто",
        "что",
        "где",
        "куда",
        "откуда",
        "когда",
        "почему",
        "зачем",
        "как",
        "какой",
        "какая",
        "какие",
        "сколько",
        "чей",
        "чья",
        "чьи",
    }

    PRONOUN_PATTERN = re.compile(
        r"\b(?:"
        r"он|она|они|его|её|ее|ему|ей|им|ним|ней|них|"
        r"него|неё|нее"
        r")\b",
        re.IGNORECASE,
    )

    COMPLETION_PATTERN = re.compile(
        r"^\s*(?P<object>[^.!?]+?)\s+"
        r"(?:я\s+)?"
        r"(?:уже\s+)?"
        r"(?:"
        r"закончил|закончила|"
        r"завершил|завершила|"
        r"выполнил|выполнила|"
        r"сделал|сделала"
        r")"
        r"\s*[.!?]?\s*$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        extractor: MemoryExtractor,
    ) -> None:
        self.extractor = extractor

    @staticmethod
    def split_sentences(
        text: str,
    ) -> list[str]:
        return [
            part.strip()
            for part in re.split(
                r"(?<=[.!?])\s+",
                text.strip(),
            )
            if part.strip()
        ]

    @staticmethod
    def first_word(
        text: str,
    ) -> str:
        words = re.findall(
            r"[А-Яа-яЁёA-Za-z]+",
            text.strip(),
        )

        return (
            words[0].lower()
            if words
            else ""
        )

    @classmethod
    def is_command(
        cls,
        text: str,
    ) -> bool:
        first = cls.first_word(text)

        if not first:
            return False

        return bool(
            cls.COMMAND_FIRST_WORD.fullmatch(
                first
            )
        )

    @classmethod
    def is_question(
        cls,
        text: str,
    ) -> bool:
        text = text.strip()

        if not text:
            return False

        if "?" in text:
            return True

        first = cls.first_word(text)

        return first in cls.QUESTION_WORDS

    @classmethod
    def is_casual_acknowledgement(
        cls,
        text: str,
    ) -> bool:
        """
        Drop short conversational acknowledgements that are not memory.

        This is intentionally a small morphological/pattern rule, not a
        large list of aliases. It only fires when the entire short message
        consists of acknowledgement/thanks fragments.
        """
        normalized = re.sub(
            r"[^А-Яа-яЁёA-Za-z]+",
            " ",
            text.lower(),
        ).strip()

        if not normalized:
            return False

        words = normalized.split()

        if len(words) > 5:
            return False

        # Thanks + acknowledgement.
        if re.fullmatch(
            r"(?:спасибо|благодарю|понял|поняла|понятно|ясно|"
            r"хорошо|окей|ок|ладно|договорились|согласен|согласна)+",
            normalized,
        ):
            return True

        # Short standalone confirmations/negations.
        if len(words) <= 2 and all(
            re.fullmatch(
                r"(?:да|нет|ага|угу|хорошо|окей|ок|ясно|понятно|"
                r"ладно|понял|поняла|спасибо|благодарю)",
                word,
            )
            for word in words
        ):
            return True

        return False

    @classmethod
    def contains_pronoun(
        cls,
        text: str,
    ) -> bool:
        return bool(
            cls.PRONOUN_PATTERN.search(text)
        )

    @staticmethod
    def strip_subject_prefix(
        text: str,
    ) -> str:
        return re.sub(
            r"^\s*я\s+",
            "",
            text.strip(),
            count=1,
            flags=re.IGNORECASE,
        )

    @classmethod
    def make_completion_candidate(
        cls,
        text: str,
        user_id: str,
        source: Source,
    ) -> MemoryCandidate | None:
        match = cls.COMPLETION_PATTERN.match(
            text.strip()
        )

        if not match:
            return None

        object_text = match.group(
            "object"
        ).strip()

        object_text = re.sub(
            r"^\s*я\s+",
            "",
            object_text,
            count=1,
            flags=re.IGNORECASE,
        ).strip()

        if not object_text:
            return None

        return MemoryCandidate(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=MemoryType.STATE,
            content=(
                f"Пользователь завершил "
                f"{object_text}"
            ),
            confidence=0.96,
            source=source,
            created_at=datetime.utcnow(),
            source_text=text,
            metadata={
                "extraction_source": "rule_router",
                "event_type": "goal_completion",
                "completed": True,
            },
        )

    def safe_extract(
        self,
        sentence: str,
        user_id: str,
        source: Source,
        context: MemoryContext | None,
    ) -> list[MemoryCandidate]:
        original = sentence.strip()

        candidates = (
            self.extractor._extract_rule_based(
                text=original,
                user_id=user_id,
                source=source,
            )
        )

        if candidates:
            return candidates

        normalized = self.strip_subject_prefix(
            original
        )

        if normalized != original:
            candidates = (
                self.extractor._extract_rule_based(
                    text=normalized,
                    user_id=user_id,
                    source=source,
                )
            )

            for candidate in candidates:
                candidate.source_text = original

            if candidates:
                return candidates

        candidates = (
            self.extractor._extract_context_rule_based(
                text=original,
                user_id=user_id,
                source=source,
                context=context,
            )
        )

        if candidates:
            return candidates

        if normalized != original:
            candidates = (
                self.extractor._extract_context_rule_based(
                    text=normalized,
                    user_id=user_id,
                    source=source,
                    context=context,
                )
            )

            for candidate in candidates:
                candidate.source_text = original

        return candidates

    def route(
        self,
        text: str,
        user_id: str,
        context: MemoryContext | None,
        source: Source,
    ) -> InputRoute:
        text = text.strip()

        if not text:
            return InputRoute(
                kind="DROP",
                candidates=[],
                reason="empty",
            )

        if self.is_casual_acknowledgement(text):
            return InputRoute(
                kind="DROP",
                candidates=[],
                reason="casual_acknowledgement",
            )

        if self.is_question(text):
            return InputRoute(
                kind="DROP",
                candidates=[],
                reason="question",
            )

        sentences = self.split_sentences(text)

        # ---------------------------------------------------------
        # One sentence
        # ---------------------------------------------------------
        if len(sentences) == 1:
            if self.is_command(text):
                return InputRoute(
                    kind="DROP",
                    candidates=[],
                    reason="command",
                )

            completion = self.make_completion_candidate(
                text=text,
                user_id=user_id,
                source=source,
            )

            if completion is not None:
                return InputRoute(
                    kind="FAST",
                    candidates=[completion],
                    reason="goal_completion_rule",
                )

            candidates = self.safe_extract(
                sentence=text,
                user_id=user_id,
                source=source,
                context=context,
            )

            if candidates:
                return InputRoute(
                    kind="FAST",
                    candidates=candidates,
                    reason="deterministic_rule",
                )

            return InputRoute(
                kind="UNIFIED",
                candidates=[],
                reason="semantic_or_ambiguous",
            )

        # ---------------------------------------------------------
        # Multiple sentences
        # ---------------------------------------------------------
        fast_candidates: list[
            MemoryCandidate
        ] = []

        for sentence in sentences:
            if self.is_question(sentence):
                continue

            if self.is_command(sentence):
                continue

            # Pronoun may refer to an entity from another sentence.
            if self.contains_pronoun(sentence):
                return InputRoute(
                    kind="UNIFIED",
                    candidates=[],
                    reason="cross_sentence_pronoun",
                )

            candidates = self.safe_extract(
                sentence=sentence,
                user_id=user_id,
                source=source,
                context=context,
            )

            if not candidates:
                return InputRoute(
                    kind="UNIFIED",
                    candidates=[],
                    reason="mixed_message_needs_semantics",
                )

            fast_candidates.extend(
                candidates
            )

        if fast_candidates:
            return InputRoute(
                kind="FAST",
                candidates=fast_candidates,
                reason="sentence_level_rules",
            )

        return InputRoute(
            kind="DROP",
            candidates=[],
            reason="only_commands_or_questions",
        )
