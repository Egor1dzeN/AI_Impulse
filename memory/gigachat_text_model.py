from __future__ import annotations

import json
import os
import re
import time

import httpx
from gigachat.exceptions import RateLimitError, ServerError
from gigachat.models import Chat, Messages, MessagesRole

from gigachat_client import get_client

# Same knob as gigachat_grammar_normalizer.py — GigaChat-2 draws from the
# "GigaChat" (Lite) free-quota bucket, confirmed working and effectively
# unused (250M free tokens) on this account, unlike GigaChat's embeddings
# endpoint which needs separate paid access.
GIGACHAT_LITE_MODEL = os.environ.get("GIGACHAT_LITE_MODEL", "GigaChat-2")

_RETRIES = 6

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```$")


def _repair_json_array(text: str) -> str:
    """GigaChat-2 sometimes ignores a "return ONLY a JSON array" system
    prompt and instead emits several top-level {...} objects separated by
    commas/newlines with no enclosing []. Every memory-pipeline JSON parser
    (extractor_contextual, relation_extractor, ...) then breaks on the
    first stray '{'. Only kicks in when the raw text fails to parse on its
    own but parses once wrapped in brackets, so a valid array/object
    response (or genuinely malformed text) passes through unchanged."""
    text = _FENCE_RE.sub("", text.strip()).strip()

    try:
        json.loads(text)
        return text
    except (json.JSONDecodeError, ValueError):
        pass

    if text.startswith("{") and text.endswith("}"):
        candidate = f"[{text}]"
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            pass

    return text


class GigaChatTextModel:
    """Drop-in replacement for QwenModel's generate() surface — the only
    method every memory-pipeline consumer calls (extractor_contextual,
    coreference, relation_extractor, unified_memory_analyzer,
    dreaming_engine, memory_validator) — backed by GigaChat's chat API
    instead of a local Qwen3-4B checkpoint. generate_stream() isn't
    implemented since no current caller uses it."""

    def __init__(self, model: str = GIGACHAT_LITE_MODEL) -> None:
        self.model = model
        self.client = get_client()
        print(f"Using GigaChat chat API (model={model}) for memory extraction instead of local Qwen3-4B...")

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_new_tokens: int = 512,
        enable_thinking: bool = False,
        do_sample: bool = False,
        temperature: float = 0.6,
        top_p: float = 0.95,
        top_k: int = 20,
    ) -> str:
        start = time.perf_counter()

        # GigaChat rejects temperature=0; every current caller uses
        # do_sample=False (greedy), so this maps that to the lowest valid
        # temperature to stay as close to deterministic as the API allows.
        chat = Chat(
            model=self.model,
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=system_prompt),
                Messages(role=MessagesRole.USER, content=user_prompt),
            ],
            max_tokens=max_new_tokens,
            temperature=temperature if do_sample else 0.1,
            top_p=top_p if do_sample else None,
        )
        # A 429/5xx during a dataset import used to drop a whole profile area
        # (e.g. where the user lives) or a record's facts: wait and resend.
        for attempt in range(_RETRIES + 1):
            try:
                resp = self.client.chat(chat)
                break
            except Exception as exc:  # noqa: BLE001
                if (getattr(exc, "status_code", None) == 402 or "Payment Required" in str(exc)) and chat.model != "GigaChat-2":
                    print(f"[GIGACHAT] {chat.model} quota exhausted (402) — switching to GigaChat-2")
                    self.model = chat.model = "GigaChat-2"
                    continue
                if not isinstance(exc, (RateLimitError, ServerError, httpx.TransportError)):
                    raise
                if attempt == _RETRIES:
                    raise
                delay = float(getattr(exc, "retry_after", 0) or 0) or min(2.0 ** (attempt + 1), 30.0)
                print(f"[GIGACHAT] {type(exc).__name__}, retry {attempt + 1}/{_RETRIES} in {delay:.0f}s")
                time.sleep(delay)
        response = _repair_json_array((resp.choices[0].message.content or "").strip())

        elapsed = time.perf_counter() - start
        print(f"[GIGACHAT] {elapsed:.3f}s | response: {response!r}")

        return response
