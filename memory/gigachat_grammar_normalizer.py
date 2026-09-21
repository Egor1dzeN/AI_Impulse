from __future__ import annotations

import os

from gigachat.models import Chat, Messages, MessagesRole

from gigachat_client import get_client
from memory_grammar_normalizer import MemoryGrammarNormalizer

GIGACHAT_LITE_MODEL = os.environ.get("GIGACHAT_LITE_MODEL", "GigaChat-2")


class GigaChatGrammarNormalizer:
    """Drop-in replacement for MemoryGrammarNormalizer (same correct()/
    correct_many() surface) backed by a small GigaChat model instead of a
    local Qwen3-0.6B checkpoint. Reuses MemoryGrammarNormalizer's PROMPT and
    _clean_result() — both are plain string/staticmethod, so referencing
    them doesn't load the local model."""

    def __init__(self, model: str = GIGACHAT_LITE_MODEL) -> None:
        self.model = model
        self.client = get_client()
        print(f"Using GigaChat chat API (model={model}) for grammar normalization instead of local Qwen3-0.6B...")

    def correct(self, text: str) -> str:
        if not text or not text.strip():
            return text

        prompt = MemoryGrammarNormalizer.PROMPT.format(text=text.strip())
        # GigaChat rejects temperature=0 (must be > 0); 0.1 keeps this close
        # to the deterministic greedy decoding the local model used.
        chat = Chat(
            model=self.model,
            messages=[Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.1,
        )
        resp = self.client.chat(chat)
        result = resp.choices[0].message.content or ""

        return MemoryGrammarNormalizer._clean_result(result, text.strip())

    def correct_many(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        return [self.correct(text.strip() if text else text) for text in texts]
