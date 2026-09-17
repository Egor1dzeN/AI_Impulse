from __future__ import annotations

from enum import Enum

from models import MemoryCandidate


class CandidateRoute(str, Enum):
    FAST = "fast"
    FULL = "full"


class CandidateRouter:
    """
    Маршрутизация каждого MemoryCandidate независимо от остальных.

    FAST:
        candidate уже получен deterministic/context rule.

    FULL:
        candidate создан LLM и требует дополнительного анализа.
    """

    FAST_SOURCES = {"rule", "context_rule"}

    @classmethod
    def route(cls, candidate: MemoryCandidate) -> CandidateRoute:
        metadata = candidate.metadata or {}
        source = str(metadata.get("extraction_source", "llm"))

        if source in cls.FAST_SOURCES:
            return CandidateRoute.FAST

        return CandidateRoute.FULL
