from __future__ import annotations

import os
from typing import Sequence

import numpy as np

from gigachat_client import get_client

GIGACHAT_EMBEDDINGS_MODEL = os.environ.get("GIGACHAT_EMBEDDINGS_MODEL", "Embeddings-2")


class GigaChatEmbedder:
    """Drop-in replacement for Qwen3Embedder (same encode_documents/
    encode_query/dimension surface) backed by GigaChat's /embeddings API,
    so memory retrieval no longer needs a local Qwen3-Embedding-0.6B
    checkpoint resident in VRAM/RAM.

    GigaChat's embeddings endpoint has no query/document distinction (unlike
    Qwen3-Embedding's instruction-prefixed queries), so encode_query() just
    embeds the raw query text and ignores `task`. Vectors are L2-normalized
    here to match Qwen3Embedder's output, since FaissMemoryIndex uses
    IndexFlatIP and treats inner product as cosine similarity.
    """

    def __init__(self, model: str = GIGACHAT_EMBEDDINGS_MODEL, batch_size: int = 16) -> None:
        self.model = model
        self.batch_size = batch_size
        self.client = get_client()
        print(f"Using GigaChat embeddings API (model={model}) instead of local Qwen3-Embedding-0.6B...")
        self.dimension = len(self._embed_raw(["probe"])[0])

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings(texts, model=self.model)
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
        return self._encode([query])[0]
