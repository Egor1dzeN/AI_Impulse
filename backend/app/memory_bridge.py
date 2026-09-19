from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# The `memory/` package (written outside this backend) uses flat, non-relative
# imports internally (e.g. `from enricher import MemoryEnricher`), so it must
# sit on sys.path as a plain directory rather than be imported as a
# sub-package.
MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory"
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))

_lock = Lock()
_system = None


def _build_enricher(qwen, entity_enricher, grammar_normalizer):
    from extractor_contextual import MemoryExtractor
    from coreference import CoreferenceResolver
    from temporal import TemporalEnricher
    from candidate_grounder import CandidateGrounder
    from subject_resolver import SubjectResolver
    from relation_extractor import RelationExtractor
    from relation_linker import RelationLinker
    from enricher import MemoryEnricher
    from unified_memory_analyzer import UnifiedMemoryAnalyzer

    extractor = MemoryExtractor(qwen)

    analyzer = UnifiedMemoryAnalyzer(
        qwen=qwen,
        extractor=extractor,
    )

    return MemoryEnricher(
        extractor=extractor,
        entity_enricher=entity_enricher,
        coreference_resolver=CoreferenceResolver(qwen),
        temporal_enricher=TemporalEnricher(),
        grounder=CandidateGrounder(),
        subject_resolver=SubjectResolver(),
        relation_extractor=RelationExtractor(qwen),
        relation_linker=RelationLinker(),
        validator=None,
        grammar_normalizer=grammar_normalizer,
        unified_analyzer=analyzer,
    )


def get_memory_system():
    """Lazily build the single process-wide MemorySystem instance.

    Heavy (loads/reuses the shared Qwen3-4B + embedder + reranker + GLiNER
    models), so this must only run once per process.
    """
    global _system
    if _system is not None:
        return _system

    with _lock:
        if _system is not None:
            return _system

        from model_registry import (
            get_entity_enricher,
            get_grammar_normalizer,
            get_qwen,
            get_retrieval_models,
        )
        from memory_consolidator import MemoryConsolidator
        from memory_system import MemorySystem

        qwen = get_qwen()
        retrieval_models = get_retrieval_models()
        entity_enricher = get_entity_enricher()
        grammar_normalizer = get_grammar_normalizer()
        enricher = _build_enricher(qwen, entity_enricher, grammar_normalizer)

        _system = MemorySystem(
            enricher=enricher,
            consolidator=MemoryConsolidator(),
            db_path=str(DATA_DIR / "memory.db"),
            index_dir=str(DATA_DIR / "memory_index"),
            retrieval_models=retrieval_models,
        )

    return _system


def get_qwen_model():
    """Return the same shared Qwen3-4B instance used for memory extraction,
    so the chat LLM never loads a second copy of the model into VRAM."""
    from model_registry import get_qwen

    return get_qwen()


def update_memory(text: str, user_id: str) -> None:
    get_memory_system().update(text, user_id)


def retrieve_memory_context(query: str, user_id: str, top_k: int = 5) -> str:
    system = get_memory_system()
    hits = system.retrieve(query, user_id, top_k)
    return system.context_formatter.format(
        [hit.memory for hit in hits]
    )


def list_memories(user_id: str) -> list[dict[str, Any]]:
    memories = get_memory_system().get_all(user_id)
    return [
        {
            "id": m.id,
            "type": m.type.value,
            "content": m.content,
            "confidence": m.confidence,
            "importance": m.importance,
            "status": m.status.value,
            "created_at": m.created_at.isoformat() if m.created_at else "",
        }
        for m in memories
    ]
