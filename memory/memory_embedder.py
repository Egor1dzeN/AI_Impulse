from __future__ import annotations

from typing import Sequence

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer, BitsAndBytesConfig


class Qwen3Embedder:
    """Qwen3 multilingual bi-encoder for memory retrieval.

    CUDA:
      - Linear weights: 8-bit LLM.int8()
      - remaining modules: float16
    CPU:
      - float32

    8-bit is intentionally chosen instead of 4-bit to keep semantic
    retrieval quality as close as possible to the original model.
    """

    DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"
    DEFAULT_TASK = (
        "Given a user query, retrieve the most relevant memories from "
        "the user's long-term memory"
    )

    USE_8BIT = True

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str | None = None,
        max_length: int = 2048,
        batch_size: int = 8,
    ) -> None:
        self.model_name = model_name
        self.device = device or (
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        self.max_length = max_length
        self.batch_size = batch_size
        self.quantized = False

        print(
            f"Loading embedding model: "
            f"{model_name} on {self.device}..."
        )

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            padding_side="left",
        )

        if self.device.startswith("cuda") and self.USE_8BIT:
            quantization_config = BitsAndBytesConfig(
                load_in_8bit=True,
                llm_int8_threshold=6.0,
            )

            self.model = AutoModel.from_pretrained(
                model_name,
                dtype=torch.float16,
                device_map="auto",
                quantization_config=quantization_config,
            )

            self.quantized = True

            print(
                "Qwen3-Embedding-0.6B: "
                "8-bit LLM.int8()"
            )

        elif self.device.startswith("cuda"):
            self.model = AutoModel.from_pretrained(
                model_name,
                dtype=torch.float16,
            )
            self.model.to(self.device)

        else:
            self.model = AutoModel.from_pretrained(
                model_name,
                dtype=torch.float32,
            )
            self.model.to(self.device)

        self.model.eval()

        self.dimension = int(
            getattr(
                self.model.config,
                "hidden_size",
                1024,
            )
        )

    @staticmethod
    def _last_token_pool(
        last_hidden_states: torch.Tensor,
        attention_mask: torch.Tensor,
    ) -> torch.Tensor:
        left_padding = bool(
            attention_mask[:, -1].sum().item()
            == attention_mask.shape[0]
        )

        if left_padding:
            return last_hidden_states[:, -1]

        sequence_lengths = attention_mask.sum(dim=1) - 1

        batch_size = last_hidden_states.shape[0]

        return last_hidden_states[
            torch.arange(
                batch_size,
                device=last_hidden_states.device,
            ),
            sequence_lengths,
        ]

    @staticmethod
    def _instruction(
        query: str,
        task: str,
    ) -> str:
        return (
            f"Instruct: {task}\n"
            f"Query: {query}"
        )

    @torch.no_grad()
    def encode_documents(
        self,
        texts: Sequence[str],
    ) -> np.ndarray:
        return self._encode(
            list(texts),
        )

    @torch.no_grad()
    def encode_query(
        self,
        query: str,
        task: str | None = None,
    ) -> np.ndarray:
        text = self._instruction(
            query,
            task or self.DEFAULT_TASK,
        )

        return self._encode(
            [text],
        )[0]

    @torch.no_grad()
    def _encode(
        self,
        texts: list[str],
    ) -> np.ndarray:
        if not texts:
            return np.empty(
                (0, self.dimension),
                dtype=np.float32,
            )

        batches: list[np.ndarray] = []

        for start in range(
            0,
            len(texts),
            self.batch_size,
        ):
            batch_texts = texts[
                start : start + self.batch_size
            ]

            batch = self.tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )

            input_device = (
                self.model
                .get_input_embeddings()
                .weight.device
            )

            batch = {
                key: value.to(input_device)
                for key, value in batch.items()
            }

            outputs = self.model(**batch)

            embeddings = self._last_token_pool(
                outputs.last_hidden_state,
                batch["attention_mask"],
            )

            embeddings = F.normalize(
                embeddings,
                p=2,
                dim=1,
            )

            batches.append(
                embeddings.float()
                .cpu()
                .numpy()
            )

        return np.concatenate(
            batches,
            axis=0,
        ).astype(np.float32)
