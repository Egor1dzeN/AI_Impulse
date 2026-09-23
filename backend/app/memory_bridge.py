from __future__ import annotations

import logging
import sys
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

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
            qwen=qwen,
            dream_every_n_memories=5,
            dream_max_memories=50,
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

        from model_registry import get_embedder
        from faiss_memory_index import FaissMemoryIndex
        from memory_index_manager import MemoryIndexManager

        # Was `Qwen3Embedder()` directly here, bypassing model_registry's
        # cloud.ru/GigaChat switch entirely — deleting a memory as the
        # FIRST action in a fresh process (before any chat turn had called
        # get_memory_system()) silently loaded the local Qwen3-Embedding-0.6B
        # even with GIGA_CHAT_EMBEDDING set. get_embedder() applies the same
        # precedence as chat's embedder AND shares the one cached instance
        # with it, so calling this first never causes a second load later.
        embedder = get_embedder()
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


def update_memory(text: str, user_id: str, source: "Source | None" = None) -> None:
    """`source` (memory/models.py's Source — type/id/title/created_at)
    identifies where `text` came from and when the underlying event
    happened, for bulk-ingested data (email/telegram/calendar) that isn't
    something the user just typed in chat right now. Plain chat messages
    (the only caller before ingest_email() below existed) omit it and get
    the extractor's default bare "text" source, unchanged from before."""
    get_memory_system().update(text, user_id, source=source)


def ingest_email(user_id: str, email: dict[str, Any]) -> None:
    """Feed one email record (emails_mock_teamlead.jsonl's schema: id,
    date, subject, body, from/to as [{name, email}, ...]) through the same
    extraction pipeline as a live chat message, tagged with a real
    source=email and the email's own date — see update_memory()'s
    docstring and memory_updater.py's valid_from handling for why that
    matters. Framed as "incoming email" text (not first-person) since
    that's what it actually is; the extractor still resolves facts to the
    named recipient via the subject/body's own "Мария"/"ваша" wording."""
    from datetime import datetime

    from models import Source

    date_str = email.get("date")
    try:
        event_date = datetime.fromisoformat(date_str) if date_str else None
    except ValueError:
        event_date = None

    sender = (email.get("from") or [{}])[0]
    sender_label = f"{sender.get('name', '')} <{sender.get('email', '')}>".strip()
    subject = email.get("subject", "")
    body = email.get("body", "")

    if str(email.get("folder") or "").upper() == "SENT":
        recipients = ", ".join(
            f"{p.get('name', '')} <{p.get('email', '')}>".strip() for p in email.get("to") or [] if isinstance(p, dict)
        )
        text = f"Исходящее письмо, написанное самим пользователем, кому: {recipients}. Тема: {subject}\n\n{body}"
    else:
        text = f"Входящее письмо от {sender_label}. Тема: {subject}\n\n{body}"

    source = Source(
        type="email",
        id=str(email.get("id", "")),
        title=subject,
        created_at=event_date,
    )

    update_memory(text, user_id, source=source)


def ingest_telegram_message(user_id: str, message: dict[str, Any]) -> None:
    """Feed one Telegram message (telegram_mock_teamlead.jsonl's schema:
    chat, msg_id, date, sender, out, text, reply_to, media, forwarded,
    edited, service) through the extraction pipeline, same idea as
    ingest_email() above. Returns without doing anything for a message with
    no text (a pure media/service entry) — there's nothing for the
    extractor to read a fact out of."""
    from datetime import datetime

    from models import Source

    if message.get("messages"):
        _ingest_telegram_chunk(user_id, message)
        return

    text = (message.get("text") or "").strip()
    if not text:
        return

    date_str = message.get("date")
    try:
        event_date = datetime.fromisoformat(date_str) if date_str else None
    except ValueError:
        event_date = None

    chat = message.get("chat", "")
    sender = message.get("sender", "")
    direction = "Исходящее" if message.get("out") else "Входящее"
    body = f"{direction} сообщение в Telegram (чат «{chat}»), от {sender}: {text}"

    source = Source(
        type="telegram",
        id=str(message.get("msg_id", "")),
        title=chat,
        created_at=event_date,
    )

    update_memory(body, user_id, source=source)


def _ingest_telegram_chunk(user_id: str, chunk: dict[str, Any]) -> None:
    """A conversation window from personal_data._conversation_chunks: the
    whole exchange goes through extraction at once, so short replies keep
    their context and facts are attributed to the right speaker."""
    from datetime import datetime

    from models import Source

    try:
        started = datetime.fromisoformat(chunk["date"]) if chunk.get("date") else None
    except ValueError:
        started = None
    lines = [
        f"{m['sender']}{' (пользователь)' if m.get('out') else ''}: {m['text']}"
        for m in chunk["messages"] if m.get("text")
    ]
    if not lines:
        return
    when = started.strftime("%d.%m.%Y") if started else "дата неизвестна"
    text = (
        f"Переписка в Telegram, чат «{chunk['chat']}», {when}. Реплики с пометкой «(пользователь)» написал сам "
        f"пользователь, остальные — его собеседники:\n" + "\n".join(lines)
    )
    update_memory(
        text,
        user_id,
        source=Source(type="telegram", id=f"{chunk['chat']}:{chunk['msg_id']}", title=chunk["chat"], created_at=started),
    )


def ingest_calendar_event(user_id: str, event: dict[str, Any]) -> None:
    """Feed one Google Calendar event resource (google_calendar_mock.jsonl's
    schema — the same shape the real Calendar API returns: id, summary,
    description, start/end as {date} or {dateTime}, attendees, location,
    organizer/creator) through the extraction pipeline, same idea as
    ingest_email() above."""
    from datetime import datetime

    from models import Source

    start = event.get("start") or {}
    end = event.get("end") or {}
    start_str = start.get("dateTime") or start.get("date")
    end_str = end.get("dateTime") or end.get("date")

    try:
        event_date = datetime.fromisoformat(start_str) if start_str else None
    except ValueError:
        event_date = None

    try:
        event_end = datetime.fromisoformat(end_str) if end_str else None
    except ValueError:
        event_end = None

    summary = event.get("summary", "(без названия)")
    description = event.get("description") or ""
    location = event.get("location")
    attendees = event.get("attendees") or []
    attendee_names = ", ".join(a.get("displayName") or a.get("email", "") for a in attendees)

    parts = [f"Событие в календаре: «{summary}»."]
    if start_str:
        parts.append(f"Начало: {start_str}" + (f", окончание: {end_str}" if end_str else "") + ".")
    if location:
        parts.append(f"Место: {location}.")
    if attendee_names:
        parts.append(f"Участники: {attendee_names}.")
    if description:
        parts.append(f"Описание: {description}")
    text = " ".join(parts)

    source = Source(
        type="calendar",
        id=str(event.get("id", "")),
        title=summary,
        created_at=event_date,
        end_at=event_end,
    )

    update_memory(text, user_id, source=source)


_INGEST_BY_SOURCE = {
    "email": ingest_email,
    "telegram": ingest_telegram_message,
    "calendar": ingest_calendar_event,
}


def import_records(user_id: str, source: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Bulk entry point for the Data Import panel (see main.py's
    POST /api/import): run every record in `records` through the ingest_*
    function for `source`, one memory-extraction call per record. A single
    malformed record is logged and skipped rather than failing the whole
    batch — with dozens/hundreds of records in a real export, one bad line
    shouldn't lose everything after it."""
    ingest = _INGEST_BY_SOURCE.get(source)
    if ingest is None:
        raise ValueError(f"Unknown import source: {source!r}")

    imported = 0
    errors: list[str] = []
    for i, record in enumerate(records):
        try:
            ingest(user_id, record)
            imported += 1
        except Exception as exc:  # noqa: BLE001 — one bad record must not sink the batch
            logger.exception("import_records: user_id=%s source=%s record[%d] failed", user_id, source, i)
            errors.append(f"record {i}: {exc}")

    return {"imported": imported, "failed": len(errors), "errors": errors}


def handmade_memory(
    text: str,
    user_id: str,
    memory_type: str = "fact",
    confidence: float = 1.0,
    importance: float = 1.0,
    freshness: float = 1.0,
) -> dict[str, Any]:
    from models import MemoryType

    system = get_memory_system()

    memory = system.add_memory(
        content=text,
        user_id=user_id,
        memory_type=MemoryType(memory_type),
        confidence=confidence,
        importance=importance,
        freshness=freshness,
    )

    return {
        "id": memory.id,
        "user_id": memory.user_id,
        "type": memory.type.value,
        "content": memory.content,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "freshness": memory.freshness,
        "status": memory.status.value,
    }


def retrieve_memory_context(query: str, user_id: str, top_k: int = 20) -> str:
    system = get_memory_system()
    hits = system.retrieve(query, user_id, top_k)
    context = system.context_formatter.format(
        [hit.memory for hit in hits]
    )
    logger.info(
        "search_memory tool: user_id=%s query=%r -> %d hit(s), context=%r",
        user_id, query, len(hits), context,
    )
    return context


def delete_memory(memory_id: str, user_id: str) -> None:
    # If the full system is already loaded (e.g. a chat already ran in this
    # process), route through it so its own in-memory FAISS index stays
    # consistent — that index only reflects disk state as of when it was
    # last loaded/rebuilt, so rebuilding via a second, separate index
    # manager here would leave the full system's copy silently stale for
    # search_memory. Only take the lightweight path when nothing heavy has
    # loaded yet, so a bare delete doesn't force it to.
    logger.info("delete_memory: user_id=%s memory_id=%s", user_id, memory_id)
    if _system is not None:
        _system.delete(memory_id, user_id)
        return

    repo = _get_repository()
    memory = repo.get(memory_id)
    if memory is None:
        raise ValueError(f"Memory with id '{memory_id}' does not exist.")
    if memory.user_id != user_id:
        raise ValueError("Memory does not belong to the specified user.")

    index_manager = _get_lightweight_index_manager()  # before the delete, so its startup sync check can't trigger a rebuild
    repo.delete(memory_id)
    index_manager.remove([memory_id])


def delete_all_memories(user_id: str) -> int:
    """Delete every memory this user has, for the account-wide "forget me"
    reset — see main.py's DELETE /api/account/data. Same dual-path choice
    as delete_memory() above (route through the loaded full system if one
    exists, otherwise a lightweight repository + index rebuild)."""
    logger.info("delete_all_memories: user_id=%s", user_id)
    if _system is not None:
        return _system.delete_all(user_id)

    repo = _get_repository()
    index_manager = _get_lightweight_index_manager()
    ids = [m.id for m in repo.find_by_user(user_id=user_id, active_only=False)]
    count = repo.delete_by_user(user_id)
    index_manager.remove(ids)
    return count


def search_memories(query: str, user_id: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Structured semantic search over one user's memories — the same
    embed -> FAISS -> rerank pipeline the search_memory tool uses, but
    returning ranked hits (with scores) instead of a flattened text blob
    meant for the LLM. Used for a manual search box in the UI. Needs the
    full model stack (embedder + reranker), same as retrieve_memory_context."""
    system = get_memory_system()
    hits = system.retrieve(query, user_id, top_k)
    logger.info(
        "manual memory search: user_id=%s query=%r -> %d hit(s): %s",
        user_id, query, len(hits),
        [(h.memory.type.value, h.memory.content, round(h.reranker_score, 3)) for h in hits],
    )
    return [
        {**_memory_to_dict(hit.memory), "relevance": hit.reranker_score}
        for hit in hits
    ]


def force_dream(user_id: str) -> dict[str, Any]:
    """Thin wrapper — the actual batch-consolidation logic lives on
    MemorySystem itself (memory/memory_system.py::force_dream), alongside
    update/retrieve/delete/get_all, rather than reimplemented here."""
    logger.info("force_dream (memory consolidation) start: user_id=%s", user_id)
    try:
        result = get_memory_system().force_dream(user_id)
    except Exception:
        # Logged with the full traceback here (unlike the generic tool-call
        # logging in agent.py) since this is the one call site where a
        # genuine failure and a "nothing to change" outcome could otherwise
        # look the same from the frontend's perspective — this makes a real
        # error unambiguous in the logs when one does happen.
        logger.exception("force_dream failed: user_id=%s", user_id)
        raise
    logger.info(
        "force_dream done: user_id=%s merged=%d archived=%d summary=%r",
        user_id, len(result["merged"]), len(result["archived"]), result["summary"],
    )
    return result


PROFILE_BUILDER_TAG = "profile_builder"


def replace_profile_facts(user_id: str, facts: list[dict[str, Any]]) -> int:
    """Swap the user's previously generated profile facts for `facts`
    (each: content, type, sources [Source], valid_from, valid_until,
    confidence, dimension). A rebuild therefore never duplicates facts."""
    from models import MemoryType

    system = get_memory_system()
    stale = [m.id for m in system.get_all(user_id) if (m.metadata or {}).get("generated_by") == PROFILE_BUILDER_TAG]
    for memory_id in stale:
        system.repository.delete(memory_id)
    system.index_manager.remove(stale)

    for fact in facts:
        system.add_memory(
            content=fact["content"],
            user_id=user_id,
            memory_type=MemoryType(fact.get("type", "fact")),
            confidence=fact.get("confidence", 0.9),
            importance=fact.get("importance", 0.8),
            sources=fact.get("sources") or [],
            valid_from=fact.get("valid_from"),
            valid_until=fact.get("valid_until"),
            metadata={
                "generated_by": PROFILE_BUILDER_TAG,
                "profile_dimension": fact.get("dimension"),
                "subject": "user",
                "subject_is_user": True,
                "subject_evidence": "profile_builder",
            },
        )
    logger.info("replace_profile_facts: user_id=%s facts=%d", user_id, len(facts))
    return len(facts)


_DIGEST_ORDER = ["identity", "location", "biography", "relationships", "work_goals", "health_sport", "preferences",
                 "routine", "finance"]


def profile_digest(user_id: str, today: "Any") -> str:
    """The profile builder's facts as a compact block for the system prompt —
    the verified core of the profile is always in front of the model, so a
    question like "когда у меня день рождения" doesn't depend on which 20
    facts a similarity search happens to return. Age is computed here, on
    today's date, never by the model."""
    import re as _re
    from datetime import date as _date

    facts = [m for m in get_memory_system().get_all(user_id) if (m.metadata or {}).get("generated_by") == PROFILE_BUILDER_TAG]
    if not facts:
        return ""
    rank = {d: i for i, d in enumerate(_DIGEST_ORDER)}
    facts.sort(key=lambda m: rank.get((m.metadata or {}).get("profile_dimension"), len(rank)))
    lines = []
    for m in facts:
        source = m.sources[0] if m.sources else None
        where = f" [{source.title}]" if source and source.title else ""
        lines.append(f"- {m.content}{where}")
    for m in facts:
        if (m.metadata or {}).get("profile_dimension") not in ("identity", "biography"):
            continue
        match = _re.search(r"(?:родил\w*|рождени\w*)[^.\d]{0,20}(\d{2})\.(\d{2})\.(\d{4})", m.content)
        if match:
            born = _date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            nxt = born.replace(year=today.year)
            if nxt < today:
                nxt = born.replace(year=today.year + 1)
            lines.append(f"- ВЫЧИСЛЕНО на {today:%d.%m.%Y}: пользователю {age} (полных лет); следующий день рождения "
                         f"{nxt:%d.%m.%Y}, тогда исполнится {age + 1}.")
            break
    return "\n".join(lines)


def list_memories(user_id: str) -> list[dict[str, Any]]:
    memories = _get_repository().find_by_user(
        user_id=user_id,
        active_only=True,
    )

    return [_memory_to_dict(m) for m in memories]


def _memory_to_dict(m: Any) -> dict[str, Any]:
    """Shared Memory -> API dict shape for list_memories/search_memories.

    Case criterion "источник и период действия": sources[i].type is the
    origin (email/telegram/calendar/text=chat), sources[i].created_at
    there is the ORIGINAL event date; valid_from/valid_until is this
    fact's own validity period. See update_memory()/ingest_email()'s
    docstrings for how these get populated."""
    return {
        "id": m.id,
        "type": m.type.value,
        "content": m.content,
        "confidence": m.confidence,
        "importance": m.importance,
        "status": m.status.value,
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "sources": [
            {
                "type": s.type,
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in m.sources
        ],
        "valid_from": m.valid_from.isoformat() if m.valid_from else None,
        "valid_until": m.valid_until.isoformat() if m.valid_until else None,
    }
