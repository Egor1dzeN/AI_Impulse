from __future__ import annotations

import json
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

_repo_lock = Lock()
_repository = None

_index_manager_lock = Lock()
_index_manager = None


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


def _get_repository():
    """Lazily build just the SQLite repository — no ML models involved.

    Listing memories is a plain SELECT and shouldn't have to wait for the
    full model stack (Qwen3-4B + GLiNER + grammar normalizer + embedder +
    reranker, ~14GB) that get_memory_system() loads for enrichment/search.
    Safe to run alongside the full system: MemoryRepository is just a
    sqlite3 connection to the same file, and SQLite handles that fine.
    """
    global _repository
    if _repository is not None:
        return _repository

    with _repo_lock:
        if _repository is not None:
            return _repository

        from memory_repository import MemoryRepository

        _repository = MemoryRepository(str(DATA_DIR / "memory.db"))

    return _repository


def _get_lightweight_index_manager():
    """Lazily build a MemoryIndexManager backed by just the embedder — no
    Qwen3-4B, GLiNER or grammar normalizer. delete()'s FAISS rebuild only
    ever re-embeds existing memory content, it never runs extraction, so it
    doesn't need the enrichment side of the stack.

    Only used when the full system hasn't been built yet in this process —
    see delete_memory(). Loading all 5 models at once (as delete used to,
    via get_memory_system()) was observed to raise MemoryError on this
    machine, on top of being needless for a delete+reindex.
    """
    global _index_manager
    if _index_manager is not None:
        return _index_manager

    with _index_manager_lock:
        if _index_manager is not None:
            return _index_manager

        from memory_embedder import Qwen3Embedder
        from faiss_memory_index import FaissMemoryIndex
        from memory_index_manager import MemoryIndexManager

        embedder = Qwen3Embedder()
        index = FaissMemoryIndex(dimension=embedder.dimension, index_dir=str(DATA_DIR / "memory_index"))
        manager = MemoryIndexManager(repository=_get_repository(), embedder=embedder, index=index)
        manager.ensure_ready()
        _index_manager = manager

    return _index_manager


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


def delete_memory(memory_id: str, user_id: str) -> None:
    # If the full system is already loaded (e.g. a chat already ran in this
    # process), route through it so its own in-memory FAISS index stays
    # consistent — that index only reflects disk state as of when it was
    # last loaded/rebuilt, so rebuilding via a second, separate index
    # manager here would leave the full system's copy silently stale for
    # search_memory. Only take the lightweight path when nothing heavy has
    # loaded yet, so a bare delete doesn't force it to.
    if _system is not None:
        _system.delete(memory_id, user_id)
        return

    repo = _get_repository()
    memory = repo.get(memory_id)
    if memory is None:
        raise ValueError(f"Memory with id '{memory_id}' does not exist.")
    if memory.user_id != user_id:
        raise ValueError("Memory does not belong to the specified user.")

    repo.delete(memory_id)
    _get_lightweight_index_manager().rebuild()


def search_memories(query: str, user_id: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Structured semantic search over one user's memories — the same
    embed -> FAISS -> rerank pipeline the search_memory tool uses, but
    returning ranked hits (with scores) instead of a flattened text blob
    meant for the LLM. Used for a manual search box in the UI. Needs the
    full model stack (embedder + reranker), same as retrieve_memory_context."""
    system = get_memory_system()
    hits = system.retrieve(query, user_id, top_k)
    return [
        {
            "id": hit.memory.id,
            "type": hit.memory.type.value,
            "content": hit.memory.content,
            "confidence": hit.memory.confidence,
            "importance": hit.memory.importance,
            "status": hit.memory.status.value,
            "created_at": hit.memory.created_at.isoformat() if hit.memory.created_at else "",
            "relevance": hit.reranker_score,
        }
        for hit in hits
    ]


_DREAM_PROMPT = """Ты анализируешь список сохранённых фактов о пользователе и ищешь ЯВНЫЕ дубликаты и противоречия.

Дубликат — два факта говорят одно и то же, возможно разными словами.
Противоречие — два факта не могут быть верны одновременно (например разные города проживания).
Факты идут от старых к новым.

Верни JSON-массив действий, без какого-либо текста вокруг. Для каждой найденной пары:
{{"keep_id": "id факта, который оставить (обычно более новый)", "remove_ids": ["id", "..."], "reason": "коротко почему"}}

Если дубликатов и противоречий нет — верни пустой массив [].
Используй ТОЛЬКО id из списка ниже, слово в слово. Никогда не придумывай id.

Факты:
{items}"""


def _parse_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        return []

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []

    return data if isinstance(data, list) else []


def force_dream(user_id: str) -> dict[str, Any]:
    """Batch memory consolidation ("dreaming"): review all of a user's
    active memories together in one LLM pass and merge obvious duplicates /
    resolve contradictions that incremental per-message consolidation
    (MemoryConsolidator, which only ever compares one new candidate against
    its single best match) can miss.

    Deliberately conservative given how unreliable a small local model can
    be at free-form batch judgment calls (seen repeatedly with tool
    arguments elsewhere in this app): the model may only reference ids it
    was actually shown — anything else is silently dropped rather than
    trusted — and "removed" memories are marked SUPERSEDED, never hard
    deleted, so a bad merge is always recoverable.
    """
    from datetime import datetime as _datetime

    from models import MemoryStatus

    system = get_memory_system()
    memories = system.repository.find_by_user(user_id=user_id, active_only=True)
    memories.sort(key=lambda m: m.created_at or _datetime.min)

    if len(memories) < 2:
        return {"merged": [], "summary": "Недостаточно записей для анализа."}

    known_ids = {m.id for m in memories}
    items = "\n".join(f"{m.id}: {m.content}" for m in memories)

    qwen = get_qwen_model()
    response = qwen.generate(
        system_prompt="Отвечай только валидным JSON, без пояснений.",
        user_prompt=_DREAM_PROMPT.format(items=items),
        max_new_tokens=512,
    )
    actions = _parse_json_array(response)

    merged: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue

        keep_id = action.get("keep_id")
        remove_ids = action.get("remove_ids")
        reason = str(action.get("reason") or "")

        if keep_id not in known_ids or not isinstance(remove_ids, list):
            continue

        applied_ids = [
            rid for rid in remove_ids
            if isinstance(rid, str) and rid in known_ids and rid != keep_id
        ]
        if not applied_ids:
            continue

        for rid in applied_ids:
            memory = system.repository.get(rid)
            if memory is None or memory.user_id != user_id:
                continue
            memory.status = MemoryStatus.SUPERSEDED
            system.repository.update(memory)

        merged.append({"keep_id": keep_id, "removed_ids": applied_ids, "reason": reason})

    if merged:
        system.index_manager.rebuild()

    summary = (
        f"Объединено записей: {sum(len(m['removed_ids']) for m in merged)}."
        if merged
        else "Дубликатов и противоречий не найдено."
    )
    return {"merged": merged, "summary": summary}


def list_memories(user_id: str) -> list[dict[str, Any]]:
    memories = _get_repository().find_by_user(user_id=user_id, active_only=False)
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
