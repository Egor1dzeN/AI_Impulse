from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from memory_reranker import Qwen3Reranker
from memory_retriever import MemoryRetriever
from models import Memory, MemoryStatus, MemoryType


@dataclass(frozen=True)
class ExpandedMemory:
    memory: Memory
    vector_score: float
    reranker_score: float
    vector_rank: int
    retrieval_reason: str


class MemoryRetrieverV2:
    """
    Retrieval pipeline:

        Qwen3-Embedding -> FAISS -> Qwen3-Reranker
        -> temporal constraints / entity expansion

    top_k is a MAXIMUM, not a promise to return unrelated memories.
    """

    TEMPORAL_PATTERNS = {
        "next_week": (
            r"\bследующ\w*\s+недел\w*\b",
            r"\bна\s+следующ\w*\s+недел\w*\b",
        ),
        "this_month": (
            r"\bэт\w*\s+месяц\w*\b",
            r"\bдо\s+конца\s+месяца\b",
        ),
        "next_year": (
            r"\bследующ\w*\s+год\w*\b",
        ),
    }

    ENTITY_CENTRIC_PATTERNS = (
        r"\bчто\s+известно\s+о\b",
        r"\bчто\s+ты\s+знаешь\s+о\b",
        r"\bрасскажи\s+о\b",
        r"\bчто\s+можешь\s+рассказать\s+о\b",
    )

    GOAL_PATTERNS = (
        r"\bцел\w*\b",
        r"\bплан\w*\b",
        r"\bхоч\w*\b",
        r"\bсобира\w*\b",
    )

    TYPE_HINTS = {
        MemoryType.GOAL: GOAL_PATTERNS,
    }

    def __init__(
        self,
        retriever: MemoryRetriever,
        reranker: Qwen3Reranker,
        min_semantic_probability: float = 0.01,
        semantic_score_drop_threshold: float = 1.0,
    ) -> None:
        self.retriever = retriever
        self.reranker = reranker

        if not 0.0 <= min_semantic_probability < 1.0:
            raise ValueError(
                "min_semantic_probability must be in [0, 1)"
            )

        if semantic_score_drop_threshold <= 0.0:
            raise ValueError(
                "semantic_score_drop_threshold must be > 0"
            )

        self.min_semantic_probability = min_semantic_probability
        self.semantic_score_drop_threshold = semantic_score_drop_threshold

    @staticmethod
    def _sigmoid(value: float) -> float:
        # Stable sigmoid for reranker logits.
        if value >= 0.0:
            z = float(__import__("math").exp(-value))
            return 1.0 / (1.0 + z)

        z = float(__import__("math").exp(value))
        return z / (1.0 + z)

    # =========================================================
    # NORMALIZATION
    # =========================================================

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().replace("ё", "е")
        text = re.sub(r"[^\w\s-]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    # =========================================================
    # QUERY INTENT
    # =========================================================

    def _detect_temporal_scope(
        self,
        query: str,
    ) -> str | None:
        q = self._normalize(query)

        for scope, patterns in self.TEMPORAL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, q):
                    return scope

        return None

    def _is_entity_centric_query(
        self,
        query: str,
    ) -> bool:
        q = self._normalize(query)

        return any(
            re.search(pattern, q)
            for pattern in self.ENTITY_CENTRIC_PATTERNS
        )

    def _query_type_hint(
        self,
        query: str,
    ) -> MemoryType | None:
        q = self._normalize(query)

        for memory_type, patterns in self.TYPE_HINTS.items():
            if any(
                re.search(pattern, q)
                for pattern in patterns
            ):
                return memory_type

        return None

    # =========================================================
    # MEMORY STRUCTURE
    # =========================================================

    @staticmethod
    def _subject(memory: Memory) -> str | None:
        value = (memory.metadata or {}).get("subject")

        if value is None:
            return None

        value = str(value).strip()

        return value or None

    @staticmethod
    def _relations(memory: Memory) -> list[dict]:
        relations = (memory.metadata or {}).get(
            "relations",
            [],
        )

        if not isinstance(relations, list):
            return []

        return [
            relation
            for relation in relations
            if isinstance(relation, dict)
        ]

    @staticmethod
    def _entities(memory: Memory) -> set[str]:
        entities = (memory.metadata or {}).get(
            "entities",
            [],
        )

        if not isinstance(entities, list):
            return set()

        return {
            str(entity).strip().lower()
            for entity in entities
            if str(entity).strip()
        }

    # =========================================================
    # TEMPORAL
    # =========================================================

    def _matches_temporal_scope(
        self,
        memory: Memory,
        scope: str,
    ) -> bool:
        text = self._normalize(memory.content)

        if scope == "next_week":
            if "следующ" in text and "недел" in text:
                return True

        elif scope == "this_month":
            if (
                "до конца месяца" in text
                or "этом месяце" in text
            ):
                return True

        elif scope == "next_year":
            if (
                "следующем году" in text
                or "следующий год" in text
            ):
                return True

        temporal = (memory.metadata or {}).get(
            "temporal",
            [],
        )

        if not isinstance(temporal, list):
            return False

        for item in temporal:
            if not isinstance(item, dict):
                continue

            value = str(
                item.get(
                    "normalized_value",
                    "",
                )
            ).lower()

            item_text = self._normalize(
                str(item.get("text", ""))
            )

            if scope == "next_week":
                if "next_week" in value:
                    return True
                if (
                    "следующ" in item_text
                    and "недел" in item_text
                ):
                    return True

            elif scope == "this_month":
                if "this_month" in value:
                    return True
                if "конца месяца" in item_text:
                    return True
                if (
                    "эт" in item_text
                    and "месяц" in item_text
                ):
                    return True

            elif scope == "next_year":
                if "next_year" in value:
                    return True
                if (
                    "следующ" in item_text
                    and "год" in item_text
                ):
                    return True

        return False

    # =========================================================
    # RELATION EXPANSION
    # =========================================================

    def _connected_memories(
        self,
        seed: Memory,
        all_memories: Iterable[Memory],
        limit: int,
    ) -> list[Memory]:
        """
        Expands a seed through explicit graph metadata.

        It does NOT blindly return every memory of the same user.
        """
        seed_subject = self._subject(seed)

        seed_entities = self._entities(seed)

        connected_values: set[str] = set()

        # For entity-centric retrieval we expand around the seed's subject.
        # A relationship memory may mention several people (for example,
        # "Антон — брат Артёма"), but that does not mean every mentioned
        # person belongs to the same expansion node.
        if seed_subject:
            connected_values.add(seed_subject.strip().lower())
        else:
            # When subject metadata is absent, fall back to entities.
            connected_values.update(seed_entities)

        for relation in self._relations(seed):
            relation_subject = relation.get("subject")

            if relation_subject:
                connected_values.add(
                    str(relation_subject).strip().lower()
                )

        if not connected_values:
            return []

        candidates: list[tuple[int, Memory]] = []

        for memory in all_memories:
            if memory.id == seed.id:
                continue

            if memory.status != MemoryStatus.ACTIVE:
                continue

            score = 0

            subject = self._subject(memory)

            if subject:
                if subject.lower() in connected_values:
                    score += 3

            entities = self._entities(memory)

            if entities & connected_values:
                score += 2

            for relation in self._relations(memory):
                relation_values = {
                    str(
                        relation.get(key)
                    ).strip().lower()
                    for key in (
                        "subject",
                        "object",
                    )
                    if relation.get(key)
                }

                if relation_values & connected_values:
                    score += 3

            if score > 0:
                candidates.append(
                    (score, memory)
                )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return [
            memory
            for _, memory in candidates[:limit]
        ]

    # =========================================================
    # SEARCH
    # =========================================================

    def search(
            self,
            query: str,
            user_id: str,
            top_k: int = 5,
            candidate_k: int = 20,
            max_related_per_seed: int = 3,
    ) -> list[ExpandedMemory]:
        """
        Retrieval order:

            1. FAISS candidate generation
            2. Qwen3 reranking
            3. hard temporal/type constraints
            4. graph expansion for entity-centric queries
            5. thresholded semantic fallback

        For normal semantic retrieval, at least 3 results are returned
        when 3 or more FAISS candidates exist.

        top_k is the maximum number of results.
        """

        top_k = max(1, top_k)
        candidate_k = max(top_k, candidate_k)
        max_related_per_seed = max(0, max_related_per_seed)

        vector_results = self.retriever.search_active(
            query=query,
            user_id=user_id,
            top_k=candidate_k,
            candidate_k=candidate_k,
        )

        if not vector_results:
            return []

        # We want at least 3 results whenever there are at least
        # 3 candidates available.
        minimum_results = min(3, len(vector_results))

        all_memories = self.retriever.repository.find_by_user(
            user_id=user_id,
            active_only=True,
        )

        documents = [
            result.memory.content
            for result in vector_results
        ]

        reranker_scores = self.reranker.score_pairs(
            query=query,
            documents=documents,
        )

        ranked_indices = sorted(
            range(len(vector_results)),
            key=lambda index: float(reranker_scores[index]),
            reverse=True,
        )

        temporal_scope = self._detect_temporal_scope(query)
        entity_centric = self._is_entity_centric_query(query)
        query_type = self._query_type_hint(query)

        # Stable lookup for vector/reranker metadata.
        result_by_id = {
            result.memory.id: (
                result,
                float(reranker_scores[index]),
                index + 1,
            )
            for index, result in enumerate(vector_results)
        }

        selected: list[ExpandedMemory] = []
        seen: set[str] = set()

        def append_result(
                memory: Memory,
                vector_score: float,
                reranker_score: float,
                vector_rank: int,
                reason: str,
        ) -> bool:
            if memory.id in seen:
                return False

            seen.add(memory.id)

            selected.append(
                ExpandedMemory(
                    memory=memory,
                    vector_score=vector_score,
                    reranker_score=reranker_score,
                    vector_rank=vector_rank,
                    retrieval_reason=reason,
                )
            )

            return len(selected) >= top_k

        def append_vector_result(
                index: int,
                reason: str,
                require_semantic_threshold: bool = False,
        ) -> bool:
            result = vector_results[index]
            score = float(reranker_scores[index])

            if require_semantic_threshold:
                probability = self._sigmoid(score)

                if probability < self.min_semantic_probability:
                    return False

            return append_result(
                memory=result.memory,
                vector_score=result.vector_score,
                reranker_score=score,
                vector_rank=index + 1,
                reason=reason,
            )

        # ---------------------------------------------------------
        # 1. Temporal query = hard filter.
        # ---------------------------------------------------------
        if temporal_scope is not None:
            for index in ranked_indices:
                result = vector_results[index]

                if not self._matches_temporal_scope(
                        result.memory,
                        temporal_scope,
                ):
                    continue

                if (
                        query_type is not None
                        and result.memory.type != query_type
                ):
                    continue

                if append_vector_result(
                        index,
                        "temporal_match",
                ):
                    return selected

            return selected

        # ---------------------------------------------------------
        # 2. Type-aware retrieval.
        # ---------------------------------------------------------
        if query_type is not None:
            for index in ranked_indices:
                result = vector_results[index]

                if result.memory.type != query_type:
                    continue

                if append_vector_result(
                        index,
                        "type_filtered",
                ):
                    return selected

        # ---------------------------------------------------------
        # 3. Entity-centric query:
        #    choose strongest semantic seed,
        #    then expand through explicit graph metadata.
        # ---------------------------------------------------------
        if entity_centric:
            primary_seed_index: int | None = None

            for index in ranked_indices:
                result = vector_results[index]

                if result.memory.id in seen:
                    continue

                primary_seed_index = index
                break

            if primary_seed_index is not None:
                seed_result = vector_results[primary_seed_index]

                if append_vector_result(
                        primary_seed_index,
                        "entity_seed",
                ):
                    return selected

                related = self._connected_memories(
                    seed=seed_result.memory,
                    all_memories=all_memories,
                    limit=max_related_per_seed,
                )

                for memory in related:
                    if memory.id in seen:
                        continue

                    vector_score = 0.0
                    reranker_score = 0.0
                    vector_rank = 0

                    if memory.id in result_by_id:
                        result, score, rank = result_by_id[
                            memory.id
                        ]

                        vector_score = result.vector_score
                        reranker_score = score
                        vector_rank = rank

                    if append_result(
                            memory=memory,
                            vector_score=vector_score,
                            reranker_score=reranker_score,
                            vector_rank=vector_rank,
                            reason="related_memory",
                    ):
                        return selected

                # If graph expansion already produced the required
                # minimum number of results, return them.
                if len(selected) >= minimum_results:
                    return selected

        # ---------------------------------------------------------
        # 4. Semantic fallback.
        #
        # The first 3 reranked candidates are accepted without
        # probability / score-drop filtering.
        #
        # Starting from the 4th candidate, the original semantic
        # filtering rules are applied.
        # ---------------------------------------------------------
        previous_score: float | None = None

        for index in ranked_indices:
            score = float(reranker_scores[index])

            # Guarantee the first 3 available reranked candidates.
            if len(selected) < minimum_results:
                append_vector_result(
                    index,
                    "semantic_top3",
                )

                previous_score = score

                if len(selected) >= top_k:
                    return selected

                continue

            # Original semantic filtering starts from result #4.
            probability = self._sigmoid(score)

            if probability < self.min_semantic_probability:
                break

            if (
                    previous_score is not None
                    and previous_score - score
                    >= self.semantic_score_drop_threshold
            ):
                break

            append_vector_result(
                index,
                "semantic",
            )

            previous_score = score

            if len(selected) >= top_k:
                return selected

        return selected

