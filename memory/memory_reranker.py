from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import torch
from sentence_transformers import CrossEncoder


@dataclass(frozen=True)
class RerankedHit:
    memory_id: str
    score: float
    original_rank: int


class Qwen3Reranker:
    """
    Qwen3-Reranker-0.6B in FP16.

    We intentionally do not use bitsandbytes here:
    the current SentenceTransformers + Qwen3 reranker path can fail
    with partially quantized Qwen lm_head weights.
    Quality/stability has priority because the model is only 0.6B.
    """

    DEFAULT_MODEL = "Qwen/Qwen3-Reranker-0.6B"
    DEFAULT_PROMPT = (
        "Given a user query, retrieve relevant memories from "
        "the user's long-term memory"
    )

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

        print(
            f"Loading reranker model: "
            f"{model_name} on {self.device}..."
        )

        self.model = CrossEncoder(
            model_name,
            max_length=max_length,
            device=self.device,
            prompts={
                "memory": self.DEFAULT_PROMPT,
            },
            default_prompt_name="memory",
        )

    def score_pairs(
        self,
        query: str,
        documents: Sequence[str],
    ) -> np.ndarray:
        if not documents:
            return np.empty(
                (0,),
                dtype=np.float32,
            )

        pairs = [
            (query, document)
            for document in documents
        ]

        scores = self.model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )

        return np.asarray(
            scores,
            dtype=np.float32,
        ).reshape(-1)

    def rerank(
        self,
        query: str,
        items: Sequence,
        document_getter,
        id_getter,
        top_k: int = 5,
    ) -> list[RerankedHit]:
        if not items:
            return []

        documents = [
            document_getter(item)
            for item in items
        ]

        scores = self.score_pairs(
            query=query,
            documents=documents,
        )

        ranked_indices = np.argsort(
            -scores
        )

        return [
            RerankedHit(
                memory_id=str(
                    id_getter(items[int(index)])
                ),
                score=float(scores[int(index)]),
                original_rank=int(index) + 1,
            )
            for index in ranked_indices[:top_k]
        ]

