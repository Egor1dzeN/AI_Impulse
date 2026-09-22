from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import faiss
import numpy as np

from models import Memory


@dataclass(frozen=True)
class FaissHit:
    memory_id: str
    score: float


class FaissMemoryIndex:
    """FAISS cosine-similarity index over memory embeddings."""

    def __init__(
        self,
        dimension: int,
        index_dir: str | Path = "memory_index",
    ) -> None:
        self.dimension = int(dimension)
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        self.index_path = self.index_dir / "memories.faiss"
        self.mapping_path = self.index_dir / "memory_ids.json"

        self.index = faiss.IndexFlatIP(self.dimension)
        self.memory_ids: list[str] = []
        self.memory_signatures: dict[str, str] = {}

    @staticmethod
    def memory_signature(memory: Memory) -> str:
        payload = memory.content.strip().encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def clear(self) -> None:
        self.index = faiss.IndexFlatIP(self.dimension)
        self.memory_ids = []
        self.memory_signatures = {}

    def add(
        self,
        memory_ids: list[str],
        embeddings: np.ndarray,
        signatures: dict[str, str] | None = None,
    ) -> None:
        if len(memory_ids) != len(embeddings):
            raise ValueError(
                "memory_ids and embeddings must have the same length"
            )

        embeddings = np.asarray(
            embeddings,
            dtype=np.float32,
        )

        if embeddings.ndim != 2:
            raise ValueError("embeddings must have shape [N, D]")

        if embeddings.shape[1] != self.dimension:
            raise ValueError(
                f"Expected dimension {self.dimension}, "
                f"got {embeddings.shape[1]}"
            )

        if any(memory_id in self.memory_ids for memory_id in memory_ids):
            raise ValueError("Attempted to add an already indexed memory id")

        faiss.normalize_L2(embeddings)
        self.index.add(embeddings)
        self.memory_ids.extend(memory_ids)

        if signatures:
            self.memory_signatures.update(signatures)

    def remove(self, memory_ids: Iterable[str]) -> int:
        """Drop vectors by memory id without re-embedding anything: the flat
        index keeps the (already normalized) vectors, so the survivors are
        copied into a fresh index as-is."""
        drop = set(memory_ids) & set(self.memory_ids)
        if not drop:
            return 0

        keep = [i for i, memory_id in enumerate(self.memory_ids) if memory_id not in drop]
        vectors = self.index.reconstruct_n(0, self.index.ntotal)

        index = faiss.IndexFlatIP(self.dimension)
        if keep:
            index.add(np.ascontiguousarray(vectors[keep], dtype=np.float32))

        self.index = index
        self.memory_ids = [self.memory_ids[i] for i in keep]
        for memory_id in drop:
            self.memory_signatures.pop(memory_id, None)
        return len(drop)

    def build(
        self,
        memories: Iterable[Memory],
        embedder,
    ) -> None:
        memories = list(memories)

        self.clear()

        if not memories:
            return

        texts = [memory.content for memory in memories]
        embeddings = embedder.encode_documents(texts)

        self.add(
            [memory.id for memory in memories],
            embeddings,
            signatures={
                memory.id: self.memory_signature(memory)
                for memory in memories
            },
        )

    def is_in_sync(self, memories: Iterable[Memory]) -> bool:
        memories = list(memories)
        expected_ids = {memory.id for memory in memories}
        actual_ids = set(self.memory_ids)

        if expected_ids != actual_ids:
            return False

        for memory in memories:
            if self.memory_signatures.get(memory.id) != self.memory_signature(memory):
                return False

        return True

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
    ) -> list[FaissHit]:
        if self.index.ntotal == 0:
            return []

        query = np.asarray(
            query_embedding,
            dtype=np.float32,
        )

        if query.ndim == 1:
            query = query.reshape(1, -1)

        if query.shape[1] != self.dimension:
            raise ValueError(
                f"Expected query dimension {self.dimension}, "
                f"got {query.shape[1]}"
            )

        faiss.normalize_L2(query)

        scores, indices = self.index.search(
            query,
            min(top_k, self.index.ntotal),
        )

        hits: list[FaissHit] = []

        for score, index in zip(
            scores[0].tolist(),
            indices[0].tolist(),
        ):
            if index < 0:
                continue

            hits.append(
                FaissHit(
                    memory_id=self.memory_ids[index],
                    score=float(score),
                )
            )

        return hits

    def save(self) -> None:
        faiss.write_index(
            self.index,
            str(self.index_path),
        )

        self.mapping_path.write_text(
            json.dumps(
                {
                    "dimension": self.dimension,
                    "memory_ids": self.memory_ids,
                    "memory_signatures": self.memory_signatures,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load(self) -> None:
        if not self.index_path.exists():
            raise FileNotFoundError(
                f"FAISS index not found: {self.index_path}"
            )

        if not self.mapping_path.exists():
            raise FileNotFoundError(
                f"FAISS mapping not found: {self.mapping_path}"
            )

        index = faiss.read_index(str(self.index_path))
        mapping = json.loads(
            self.mapping_path.read_text(encoding="utf-8")
        )

        dimension = int(mapping["dimension"])
        if dimension != self.dimension:
            raise ValueError(
                f"Index dimension {dimension} does not match "
                f"configured dimension {self.dimension}"
            )

        memory_ids = mapping["memory_ids"]
        if len(memory_ids) != index.ntotal:
            raise ValueError(
                "FAISS index size does not match memory ID mapping"
            )

        raw_signatures = mapping.get("memory_signatures", {})
        if not isinstance(raw_signatures, dict):
            raw_signatures = {}

        self.index = index
        self.memory_ids = [str(value) for value in memory_ids]
        self.memory_signatures = {
            str(key): str(value)
            for key, value in raw_signatures.items()
        }

    @property
    def size(self) -> int:
        return int(self.index.ntotal)
