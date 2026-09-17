from __future__ import annotations

from memory_embedder import Qwen3Embedder
from memory_reranker import Qwen3Reranker


class RetrievalModelManager:
    """
    Owns retrieval models for one process.

    Each checkpoint is instantiated exactly once:
      - Qwen3-Embedding-0.6B
      - Qwen3-Reranker-0.6B

    Other components receive these objects instead of constructing models
    themselves.
    """

    def __init__(
        self,
        embedder: Qwen3Embedder | None = None,
        reranker: Qwen3Reranker | None = None,
    ) -> None:
        self.embedder = embedder or Qwen3Embedder()
        self.reranker = reranker or Qwen3Reranker()
