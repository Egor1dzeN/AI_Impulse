from __future__ import annotations

import os
from typing import Sequence

import httpx
import numpy as np
from openai import OpenAI

from cloud_ru_embedder import CLOUD_RU_API_KEY
from memory_reranker import RerankedHit

# Same cloud.ru account/key as CloudRuEmbedder (GIGA_CHAT_EMBEDDING) — its
# custom /score endpoint isn't part of the OpenAI embeddings/chat surface,
# so this base_url has no /v1 suffix (the SDK's low-level .post() is used
# to hit it directly).
CLOUD_RU_RERANK_BASE_URL = os.environ.get("CLOUD_RU_RERANK_BASE_URL", "https://foundation-models.api.cloud.ru")
CLOUD_RU_RERANK_MODEL = os.environ.get("CLOUD_RU_RERANK_MODEL", "Qwen/Qwen3-Reranker-0.6B")


def cloud_ru_rerank_configured() -> bool:
    return bool(CLOUD_RU_API_KEY)


class CloudRuReranker:
    """Drop-in replacement for the local Qwen3Reranker (same
    score_pairs()/rerank() surface), calling cloud.ru's hosted
    Qwen/Qwen3-Reranker-0.6B /score endpoint instead of loading it locally."""

    def __init__(
        self,
        model: str = CLOUD_RU_RERANK_MODEL,
        base_url: str = CLOUD_RU_RERANK_BASE_URL,
    ) -> None:
        if not CLOUD_RU_API_KEY:
            raise RuntimeError("GIGA_CHAT_EMBEDDING is not set — CloudRuReranker needs a cloud.ru API key.")

        self.model = model
        self.client = OpenAI(api_key=CLOUD_RU_API_KEY, base_url=base_url)
        print(f"Using cloud.ru reranker API (model={model}) instead of local Qwen3-Reranker-0.6B...")

    def score_pairs(self, query: str, documents: Sequence[str]) -> np.ndarray:
        if not documents:
            return np.empty((0,), dtype=np.float32)

        response = self.client.post(
            path="/score",
            cast_to=httpx.Response,
            body={
                "model": self.model,
                "encoding_format": "float",
                "text_1": query,
                "text_2": list(documents),
            },
        )

        by_index = {item["index"]: item["score"] for item in response.json()["data"]}
        return np.array([by_index[i] for i in range(len(documents))], dtype=np.float32)

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

        documents = [document_getter(item) for item in items]
        scores = self.score_pairs(query=query, documents=documents)
        ranked_indices = np.argsort(-scores)

        return [
            RerankedHit(
                memory_id=str(id_getter(items[int(index)])),
                score=float(scores[int(index)]),
                original_rank=int(index) + 1,
            )
            for index in ranked_indices[:top_k]
        ]
