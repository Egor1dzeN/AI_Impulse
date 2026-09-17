# from __future__ import annotations
#
# import torch
# from transformers import (
#     AutoModelForCausalLM,
#     AutoTokenizer,
# )
#
#
# # ============================================================
# # SHARED QWEN MODEL
# # ============================================================
#
# class QwenModel:
#
#     MODEL_NAME = "Qwen/Qwen3-4B"
#
#     def __init__(self) -> None:
#
#         device = (
#             "cuda"
#             if torch.cuda.is_available()
#             else "cpu"
#         )
#
#         print(
#             f"Loading {self.MODEL_NAME} on {device}..."
#         )
#
#         self.tokenizer = (
#             AutoTokenizer.from_pretrained(
#                 self.MODEL_NAME
#             )
#         )
#
#         if device == "cuda":
#
#             self.model = (
#                 AutoModelForCausalLM.from_pretrained(
#                     self.MODEL_NAME,
#                     dtype=torch.float16,
#                     device_map="auto",
#                 )
#             )
#
#         else:
#
#             self.model = (
#                 AutoModelForCausalLM.from_pretrained(
#                     self.MODEL_NAME,
#                     dtype=torch.float32,
#                 )
#             )
#
#         self.device = device
#
#     # ========================================================
#     # GENERATE
#     # ========================================================
#
#     def generate(
#         self,
#         system_prompt: str,
#         user_prompt: str,
#         max_new_tokens: int = 512,
#     ) -> str:
#
#         messages = [
#             {
#                 "role": "system",
#                 "content": system_prompt,
#             },
#             {
#                 "role": "user",
#                 "content": user_prompt,
#             },
#         ]
#
#         inputs = (
#             self.tokenizer.apply_chat_template(
#                 messages,
#                 tokenize=True,
#                 add_generation_prompt=True,
#                 enable_thinking=False,
#                 return_tensors="pt",
#             )
#         )
#
#         input_ids = inputs["input_ids"]
#
#         attention_mask = inputs.get(
#             "attention_mask"
#         )
#
#         input_device = (
#             self.model
#             .get_input_embeddings()
#             .weight.device
#         )
#
#         input_ids = input_ids.to(
#             input_device
#         )
#
#         if attention_mask is not None:
#
#             attention_mask = (
#                 attention_mask.to(
#                     input_device
#                 )
#             )
#
#         with torch.no_grad():
#
#             outputs = self.model.generate(
#                 input_ids=input_ids,
#                 attention_mask=attention_mask,
#                 max_new_tokens=max_new_tokens,
#                 do_sample=False,
#                 pad_token_id=(
#                     self.tokenizer.eos_token_id
#                 ),
#             )
#
#         generated_tokens = (
#             outputs[0][input_ids.shape[1]:]
#         )
#
#         response = self.tokenizer.decode(
#             generated_tokens,
#             skip_special_tokens=True,
#         )
#
#         return response.strip()
from __future__ import annotations

import os
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


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
            enable_thinking=False,
            return_tensors="pt",
        )

        input_ids = inputs["input_ids"]
        attention_mask = inputs.get("attention_mask")

        input_device = self.model.get_input_embeddings().weight.device
        input_ids = input_ids.to(input_device)

        if attention_mask is not None:
            attention_mask = attention_mask.to(input_device)

        with torch.no_grad():
            outputs = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )

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

        return response
