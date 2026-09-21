from __future__ import annotations

import os
from typing import Sequence

import numpy as np
from openai import OpenAI

# GIGA_CHAT_EMBEDDING holds a cloud.ru foundation-models API key, NOT a
# Sberbank GigaChat one — GigaChat's own /v1/embeddings needs paid access
# (see model_registry.py's GIGACHAT_USE_EMBEDDINGS comment), so this hits
# cloud.ru's OpenAI-compatible endpoint instead, serving the exact same
# Qwen/Qwen3-Embedding-0.6B checkpoint the local embedder uses — same
# dimension, no FAISS reindex-format concerns beyond the normal
# local-vs-remote numerical differences.
CLOUD_RU_API_KEY = os.environ.get("GIGA_CHAT_EMBEDDING")
CLOUD_RU_BASE_URL = os.environ.get("CLOUD_RU_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
CLOUD_RU_EMBEDDING_MODEL = os.environ.get("CLOUD_RU_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")


def cloud_ru_embedding_configured() -> bool:
    return bool(CLOUD_RU_API_KEY)


class CloudRuEmbedder:
    """Drop-in replacement for the local Qwen3Embedder (same
    encode_documents/encode_query/dimension surface), calling cloud.ru's
    hosted copy of the same model instead of loading it locally.

    Keeps the same query-side instruction prefix as memory_embedder.py's
    Qwen3Embedder, since it's the same underlying model and that prefix is
    part of how it was trained/evaluated for retrieval."""

    DEFAULT_TASK = (
        "Given a user query, retrieve the most relevant memories from "
        "the user's long-term memory"
    )

    def __init__(
        self,
        model: str = CLOUD_RU_EMBEDDING_MODEL,
        base_url: str = CLOUD_RU_BASE_URL,
        batch_size: int = 16,
    ) -> None:
        if not CLOUD_RU_API_KEY:
            raise RuntimeError("GIGA_CHAT_EMBEDDING is not set — CloudRuEmbedder needs a cloud.ru API key.")

        self.model = model
        self.batch_size = batch_size
        self.client = OpenAI(api_key=CLOUD_RU_API_KEY, base_url=base_url)
        print(f"Using cloud.ru embeddings API (model={model}) instead of local Qwen3-Embedding-0.6B...")
        self.dimension = len(self._embed_raw(["probe"])[0])

    @staticmethod
    def _instruction(query: str, task: str) -> str:
        return f"Instruct: {task}\nQuery: {query}"

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        by_index = {item.index: item.embedding for item in resp.data}
        return [by_index[i] for i in range(len(texts))]

    def _encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_raw(texts[start : start + self.batch_size]))

        array = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return array / norms

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(list(texts))

    def encode_query(self, query: str, task: str | None = None) -> np.ndarray:
        text = self._instruction(query, task or self.DEFAULT_TASK)
        return self._encode([text])[0]
