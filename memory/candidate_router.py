from __future__ import annotations

from enum import Enum

from models import MemoryCandidate


class CandidateRoute(str, Enum):
    FAST = "fast"
    FULL = "full"


class CandidateRouter:
    """
    Routes each MemoryCandidate independently of the others.

    FAST:
        the candidate was already produced by a deterministic/context rule.

    FULL:
        the candidate was created by the LLM and needs further analysis.
    """

    FAST_SOURCES = {"rule", "context_rule"}

    @classmethod
    def route(cls, candidate: MemoryCandidate) -> CandidateRoute:
        metadata = candidate.metadata or {}
        source = str(metadata.get("extraction_source", "llm"))

        if source in cls.FAST_SOURCES:
            return CandidateRoute.FAST

        return CandidateRoute.FULL
