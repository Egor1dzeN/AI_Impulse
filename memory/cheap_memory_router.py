from __future__ import annotations

import re
from enum import Enum


class MemoryRoute(str, Enum):
    SKIP = "skip"
    EXTRACT = "extract"


class CheapMemoryRouter:
    """
    Дешёвый deterministic router перед MemoryExtractor.

    Он НЕ определяет, что именно нужно сохранить.
    Его задача только мгновенно отсечь:
    - команды;
    - вопросы;
    - короткие служебные/разговорные сообщения.
    """

    COMMAND_PATTERN = re.compile(
        r"^\s*(поставь|поставьте|включи|включите|"
        r"открой|откройте|запусти|запустите|"
        r"покажи|покажите|выключи|выключите|"
        r"перезвони|напомни|напомните|"
        r"отправь|отправьте|скажи|скажите)\b",
        re.IGNORECASE,
    )

    QUESTION_START_PATTERN = re.compile(
        r"^\s*(кто|что|где|когда|почему|зачем|как|"
        r"какой|какая|какое|какие|"
        r"можно|можешь|можете|стоит ли|"
        r"есть ли|нужно ли|правда ли)\b",
        re.IGNORECASE,
    )

    QUESTION_MARK_PATTERN = re.compile(r"[?؟]")

    CASUAL_EXACT = {
        "спасибо", "спасибо!", "спасибо.",
        "ок", "ок!", "ок.",
        "окей", "окей!", "окей.",
        "хорошо", "хорошо!", "хорошо.",
        "понятно", "понятно!", "понятно.",
        "ясно", "ясно!", "ясно.",
        "ладно", "ладно!", "ладно.",
        "отлично", "отлично!", "отлично.",
        "круто", "круто!", "круто.",
        "понял", "понял!", "понял.",
        "поняла", "поняла!", "поняла.",
        "да", "да!", "да.",
        "нет", "нет!", "нет.",
    }

    def route(self, text: str) -> MemoryRoute:
        text = (text or "").strip()

        if not text:
            return MemoryRoute.SKIP

        normalized = text.lower().replace("ё", "е").strip()
        normalized = re.sub(r"\s+", " ", normalized)

        if normalized in self.CASUAL_EXACT:
            return MemoryRoute.SKIP

        if self.QUESTION_START_PATTERN.search(text):
            return MemoryRoute.SKIP

        if self.COMMAND_PATTERN.search(text):
            return MemoryRoute.SKIP

        return MemoryRoute.EXTRACT
