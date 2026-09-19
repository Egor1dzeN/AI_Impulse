from __future__ import annotations

import os
import time
from threading import Thread
from typing import Iterator

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TextIteratorStreamer

_THINK_CLOSE = "</think>"


def _strip_thinking(text: str) -> str:
    """Drop a leading <think>...</think> reasoning block, keeping only the
    final answer. A no-op when thinking was disabled (nothing to strip —
    the tag is simply absent). If the model never closed the tag (e.g. it
    got stuck in a degenerate reasoning loop and hit max_new_tokens before
    producing a real answer), return "" rather than dumping the raw
    reasoning on the caller — callers already treat an empty response as a
    failed generation and retry/fall back accordingly, which is far better
    UX than showing a wall of unfinished chain-of-thought."""
    idx = text.find(_THINK_CLOSE)
    if idx == -1:
        return "" if "<think>" in text else text
    return text[idx + len(_THINK_CLOSE):].lstrip("\n").strip()


class QwenModel:
    MODEL_NAME = "Qwen/Qwen3-4B"
    USE_4BIT = os.getenv("QWEN_4BIT", "1").strip() != "0"

    def __init__(self) -> None:
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        print(f"Loading {self.MODEL_NAME} on {self.device}...")
        self.tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)

        if self.device == "cuda" and self.USE_4BIT:
            print("Qwen3-4B: 4-bit NF4 + double quantization")

            config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )

            self.model = AutoModelForCausalLM.from_pretrained(
                self.MODEL_NAME,
                dtype=torch.float16,
                device_map="auto",
                quantization_config=config,
            )

        elif self.device == "cuda":
            print("Qwen3-4B: FP16")

            self.model = AutoModelForCausalLM.from_pretrained(
                self.MODEL_NAME,
                dtype=torch.float16,
                device_map="auto",
            )

        else:
            print("Qwen3-4B: CPU FP32")

            self.model = AutoModelForCausalLM.from_pretrained(
                self.MODEL_NAME,
                dtype=torch.float32,
            )

    @staticmethod
    def _gpu_memory() -> tuple[float, float]:
        if not torch.cuda.is_available():
            return 0.0, 0.0

        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        return allocated, reserved

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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        inputs = self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
            return_tensors="pt",
        )

        input_ids = inputs["input_ids"]
        attention_mask = inputs.get("attention_mask")

        input_device = self.model.get_input_embeddings().weight.device
        input_ids = input_ids.to(input_device)

        if attention_mask is not None:
            attention_mask = attention_mask.to(input_device)

        generation_kwargs = dict(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
            use_cache=True,
            pad_token_id=self.tokenizer.eos_token_id,
        )
        # Qwen recommends sampling (not greedy) whenever thinking is on —
        # greedy decoding tends to repeat/degenerate on this model family.
        # HF warns if temperature/top_p/top_k are passed with do_sample=False.
        if do_sample:
            generation_kwargs.update(temperature=temperature, top_p=top_p, top_k=top_k)

        with torch.no_grad():
            outputs = self.model.generate(**generation_kwargs)

        generated_tokens = outputs[0][input_ids.shape[1]:]
        response = self.tokenizer.decode(
            generated_tokens,
            skip_special_tokens=True,
        ).strip()

        elapsed = time.perf_counter() - start
        allocated, reserved = self._gpu_memory()

        print(
            f"[QWEN] {elapsed:.3f}s | "
            f"new_tokens={generated_tokens.shape[0]} | "
            f"GPU={allocated:.2f}/{reserved:.2f} GB"
        )
        print(f"[QWEN] response: {response!r}")

        return _strip_thinking(response)

    def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        max_new_tokens: int = 512,
        enable_thinking: bool = False,
        do_sample: bool = False,
        temperature: float = 0.6,
        top_p: float = 0.95,
        top_k: int = 20,
    ) -> Iterator[str]:
        """Same as `generate`, but yields text pieces as they're produced
        instead of returning the full response at once."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        inputs = self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
            return_tensors="pt",
        )

        input_ids = inputs["input_ids"]
        attention_mask = inputs.get("attention_mask")

        input_device = self.model.get_input_embeddings().weight.device
        input_ids = input_ids.to(input_device)

        if attention_mask is not None:
            attention_mask = attention_mask.to(input_device)

        streamer = TextIteratorStreamer(
            self.tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )

        generation_kwargs = dict(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
            use_cache=True,
            pad_token_id=self.tokenizer.eos_token_id,
            streamer=streamer,
        )
        if do_sample:
            generation_kwargs.update(temperature=temperature, top_p=top_p, top_k=top_k)

        start = time.perf_counter()
        thread = Thread(target=self.model.generate, kwargs=generation_kwargs)
        thread.start()

        # When thinking is on, the model's own <think>...</think> block is
        # part of the generated text (unlike the disabled case, where the
        # chat template bakes an already-closed empty block into the PROMPT
        # instead, so it never reaches this streamer at all). Buffer pieces
        # until the closing tag shows up and only yield what comes after —
        # callers should see the final answer, not the raw reasoning.
        in_thinking = enable_thinking
        pending = ""

        new_tokens = 0
        full_text_parts: list[str] = []
        for piece in streamer:
            full_text_parts.append(piece)
            if not piece:
                continue
            new_tokens += 1
            if not in_thinking:
                yield piece
                continue
            pending += piece
            idx = pending.find(_THINK_CLOSE)
            if idx != -1:
                in_thinking = False
                remainder = pending[idx + len(_THINK_CLOSE):].lstrip("\n")
                pending = ""
                if remainder:
                    yield remainder
        thread.join()

        # Generation ended without ever closing <think> — the model got
        # stuck reasoning (e.g. a degenerate repetition loop) and never
        # reached a real answer before hitting max_new_tokens. Swallow the
        # raw reasoning instead of dumping it on the user: yielding nothing
        # here makes this look like an empty reply to the caller, which
        # already retries once and then falls back to a clean message —
        # much better than a wall of unfinished chain-of-thought.
        if in_thinking and pending:
            print("[QWEN stream] generation ended without closing <think>, discarding raw reasoning")

        elapsed = time.perf_counter() - start
        allocated, reserved = self._gpu_memory()
        full_text = "".join(full_text_parts)
        print(
            f"[QWEN stream] {elapsed:.3f}s | "
            f"pieces={new_tokens} | "
            f"GPU={allocated:.2f}/{reserved:.2f} GB"
        )
        print(f"[QWEN stream] response: {full_text!r}")
