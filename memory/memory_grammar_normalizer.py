from __future__ import annotations

import json
import os
import re

import torch
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)


class MemoryGrammarNormalizer:
    """Grammar normalization with a mild 8-bit Qwen3-0.6B backend.

    Runs on CPU by default: it only does one batched pass over already
    extracted candidates in the background memory-write pipeline (never the
    interactive chat path), so its latency matters far less than leaving
    GPU VRAM free for the chat model — important on smaller cards where
    five resident models (chat LLM + embedder + reranker + GLiNER +
    grammar normalizer) can otherwise leave too little VRAM for the chat
    model's own generation and make it fail silently. Set
    GRAMMAR_DEVICE=cuda to put it back on GPU if you have room to spare.
    """

    MODEL_NAME = "Qwen/Qwen3-0.6B"
    USE_8BIT = True
    DEFAULT_DEVICE = os.getenv("GRAMMAR_DEVICE", "cpu").strip().lower()

    PROMPT = """
Ты — компонент исправления грамматики для долговременной памяти пользователя.

Исправь ОДНО русское предложение.

ТВОЯ ЗАДАЧА:
Исправить только реальные грамматические ошибки:
- неправильный падеж;
- неправильное согласование рода или числа;
- неправильную форму слова;
- неправильное согласование подлежащего и сказуемого.

СТРОГИЕ ОГРАНИЧЕНИЯ:
- Не меняй смысл.
- Не добавляй новые сведения.
- Не удаляй сведения.
- Не заменяй слова синонимами.
- Не делай стилистический рерайт.
- Не сокращай предложение.
- Не меняй порядок слов, если это не требуется для грамматики.
- Сохраняй имена, города, страны, числа, даты и факты без изменений.
- Если предложение уже грамматически правильное, верни его ТОЧНО как есть.
- Не добавляй номера, маркеры, кавычки или пояснения.
- Верни только исправленное предложение.

ПРИМЕРЫ:
Моя брат живёт в Казани.
→ Мой брат живёт в Казани.

Пользователь интересуется искусственный интеллект.
→ Пользователь интересуется искусственным интеллектом.

Пользователь живёт в Москве и изучаю искусственный интеллект.
→ Пользователь живёт в Москве и изучает искусственный интеллект.

Вход:
{text}""".strip()

    BATCH_PROMPT = """
Исправь грамматические ошибки в каждом русском предложении.

Для каждого входного предложения верни только его исправленную версию.

Строго соблюдай:
- исправляй только грамматику, падежи, согласование и формы слов;
- не меняй смысл;
- не добавляй и не удаляй информацию;
- сохраняй имена, числа, даты, города, страны и факты;
- не делай стилистический рерайт;
- не добавляй нумерацию;
- не добавляй пояснения;
- одно входное предложение = одна выходная строка;
- если предложение уже правильное, верни его без изменений.

Входные предложения:
{texts}

Верни только исправленные предложения, по одному на строку.""".strip()

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        requested = device or self.DEFAULT_DEVICE
        self.device = (
            requested
            if requested != "cuda" or torch.cuda.is_available()
            else "cpu"
        )
        self.quantized = False

        print(
            f"Loading grammar normalizer: "
            f"{self.model_name} on {self.device}..."
        )

        self.tokenizer = (
            AutoTokenizer.from_pretrained(
                self.model_name
            )
        )

        if self.device.startswith("cuda") and self.USE_8BIT:
            quantization_config = BitsAndBytesConfig(
                load_in_8bit=True,
                llm_int8_threshold=6.0,
                llm_int8_skip_modules=[
                    "lm_head",
                ],
            )

            self.model = (
                AutoModelForCausalLM.from_pretrained(
                    self.model_name,
                    dtype=torch.float16,
                    device_map="auto",
                    quantization_config=quantization_config,
                )
            )

            self.quantized = True

            print(
                "Qwen3-0.6B Grammar Normalizer: "
                "8-bit LLM.int8()"
            )

        elif self.device.startswith("cuda"):
            self.model = (
                AutoModelForCausalLM.from_pretrained(
                    self.model_name,
                    dtype=torch.float16,
                    device_map="auto",
                )
            )

        else:
            self.model = (
                AutoModelForCausalLM.from_pretrained(
                    self.model_name,
                    dtype=torch.float32,
                ).to(self.device)
            )

        self.model.eval()

    def _input_device(self):
        return (
            self.model
            .get_input_embeddings()
            .weight.device
        )

    def _generate(
        self,
        prompt: str,
        max_new_tokens: int,
    ) -> str:
        messages = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        try:
            rendered = (
                self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            )
        except TypeError:
            rendered = (
                self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            )

        inputs = self.tokenizer(
            rendered,
            return_tensors="pt",
        )

        input_device = self._input_device()

        inputs = {
            key: value.to(input_device)
            for key, value in inputs.items()
        }

        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                num_beams=1,
                pad_token_id=(
                    self.tokenizer.eos_token_id
                ),
            )

        generated = output[
            0
        ][inputs["input_ids"].shape[1]:]

        return self.tokenizer.decode(
            generated,
            skip_special_tokens=True,
        ).strip()

    @staticmethod
    def _clean_result(
        result: str,
        fallback: str,
    ) -> str:
        result = result.strip()

        if not result:
            return fallback

        result = result.splitlines()[0].strip()

        result = re.sub(
            r"^(?:ответ|исправленный текст|"
            r"исправленный вариант)\s*:\s*",
            "",
            result,
            flags=re.IGNORECASE,
        )

        result = re.sub(
            r"^\s*\d+[.)]\s*",
            "",
            result,
        )

        result = re.sub(
            r"^\s*[-*•]\s*",
            "",
            result,
        )

        if len(result) >= 2 and (
            (result[0] == '"' and result[-1] == '"')
            or (result[0] == "«" and result[-1] == "»")
        ):
            result = result[1:-1].strip()

        # The model sometimes answers about the sentence instead of returning
        # it ("Предложение не содержит ошибок, оставляю без изменения.") —
        # that meta-comment used to be stored as the fact itself.
        if re.search(
            r"(не содержит (грамматических )?ошибок|ошибок (нет|не обнаружено|не найдено)|"
            r"(оставляю|возвращаю)\s+(его\s+)?без изменени|исправлени[йя] не требу|"
            r"без изменений\s*[:.]?\s*$)",
            result,
            flags=re.IGNORECASE,
        ):
            return fallback

        return result or fallback

    def correct(
        self,
        text: str,
    ) -> str:
        if not text or not text.strip():
            return text

        prompt = self.PROMPT.format(
            text=text.strip()
        )

        input_tokens = len(
            self.tokenizer.encode(
                text,
                add_special_tokens=False,
            )
        )

        result = self._generate(
            prompt=prompt,
            max_new_tokens=max(
                8,
                input_tokens + 8,
            ),
        )

        return self._clean_result(
            result,
            text.strip(),
        )

    def correct_many(
        self,
        texts: list[str],
    ) -> list[str]:
        if not texts:
            return []

        return [
            self.correct(text.strip() if text else text)
            for text in texts
        ]
