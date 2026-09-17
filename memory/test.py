"""
MemorySystem — examples of запуск and public API usage.

Current public API:

    update(text, user_id)
    get_all(user_id)
    retrieve(query, user_id, top_k=5)
    delete(memory_id, user_id)
    clear(user_id)
    close()

Run:

    python memory_system_examples.py

The file is a demo/reference. It uses the current MemorySystem
and model registry from the project.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from enricher import MemoryEnricher
from memory_consolidator import MemoryConsolidator
from memory_system import MemorySystem
from model_registry import (
    get_entity_enricher,
    get_qwen,
    get_retrieval_models,
    get_grammar_normalizer,
)


# ============================================================
# 1. SETTINGS
# ============================================================

USER_ID = "memory-example-user"

DB_PATH = Path("memory_examples.db")
INDEX_DIR = Path("memory_examples_index")


# ============================================================
# 2. DEMO CLEANUP
# ============================================================

def clear_demo_data() -> None:
    """
    Remove only the database and FAISS index used by this demo.
    """
    if DB_PATH.exists():
        DB_PATH.unlink()

    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)


# ============================================================
# 3. BUILD ENRICHER
# ============================================================

def build_enricher(
    qwen,
    entity_enricher,
    grammar_normalizer,
) -> MemoryEnricher:
    """
    Build MemoryEnricher using the current project components.
    """
    from candidate_grounder import CandidateGrounder
    from coreference import CoreferenceResolver
    from extractor_contextual import MemoryExtractor
    from relation_extractor import RelationExtractor
    from relation_linker import RelationLinker
    from subject_resolver import SubjectResolver
    from temporal import TemporalEnricher

    extractor = MemoryExtractor(qwen)

    return MemoryEnricher(
        extractor=extractor,
        entity_enricher=entity_enricher,
        coreference_resolver=CoreferenceResolver(qwen),
        temporal_enricher=TemporalEnricher(),
        grounder=CandidateGrounder(),
        subject_resolver=SubjectResolver(),
        relation_extractor=RelationExtractor(qwen),
        relation_linker=RelationLinker(),
        grammar_normalizer=grammar_normalizer,
    )


# ============================================================
# 4. BUILD MEMORY SYSTEM
# ============================================================

def build_system() -> MemorySystem:
    """
    Build the complete MemorySystem using shared models.
    """
    qwen = get_qwen()
    retrieval_models = get_retrieval_models()
    entity_enricher = get_entity_enricher()
    grammar_normalizer = get_grammar_normalizer()

    enricher = build_enricher(
        qwen=qwen,
        entity_enricher=entity_enricher,
        grammar_normalizer=grammar_normalizer,
    )

    return MemorySystem(
        enricher=enricher,
        consolidator=MemoryConsolidator(),
        db_path=DB_PATH,
        index_dir=INDEX_DIR,
        retrieval_models=retrieval_models,
    )


# ============================================================
# 5. PRINT MEMORIES
# ============================================================

def print_memories(
    system: MemorySystem,
    user_id: str,
    title: str = "MEMORIES",
    active_only: bool = False,
) -> None:
    """
    Print user's memories.

    active_only=False is useful for demonstrating the lifecycle,
    because superseded/archived memories are also visible.
    """
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    memories = system.get_all(user_id)

    if active_only:
        memories = [
            memory
            for memory in memories
            if memory.status.value == "active"
        ]

    if not memories:
        print("Memory is empty.")
        return

    for memory in memories:
        print(
            f"[{memory.status.value:10}] "
            f"[{memory.type.value:12}] "
            f"{memory.content}"
        )


# ============================================================
# 6. EXAMPLE 1 — update()
# ============================================================

def example_update(system: MemorySystem) -> None:
    """
    update() is the main write operation.

    The message goes through:
        CheapMemoryRouter
        → Memory Pipeline
        → SQLite
        → FAISS synchronization
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 1 — update()")
    print("=" * 80)

    text = (
        "Я живу в Москве, изучаю искусственный интеллект "
        "и хочу начать новый проект."
    )

    result = system.update(
        text=text,
        user_id=USER_ID,
    )

    print(f"INPUT: {text}")
    print(f"Processed memories: {len(result.memories)}")

    for memory in result.memories:
        if memory is None:
            continue

        print(
            f"  [{memory.type.value}] "
            f"[{memory.status.value}] "
            f"{memory.content}"
        )


# ============================================================
# 7. EXAMPLE 2 — sequence of update()
# ============================================================

def example_updates(system: MemorySystem) -> None:
    """
    Several messages in sequence.

    The pipeline compares new candidates with existing memory,
    so duplicates can be confirmed and changing states can be
    superseded.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 2 — MULTIPLE update() CALLS")
    print("=" * 80)

    messages = [
        "Мой брат Антон живёт в Казани.",
        "Он работает аналитиком.",
        "Антон теперь работает удалённо.",
        "Сейчас я вешу 84 килограмма.",
        "Сегодня весы показали 82,5 килограмма.",
        "На следующей неделе я хочу начать новый проект.",
        "На следующей неделе я планирую начать новый проект.",
    ]

    for text in messages:
        result = system.update(
            text=text,
            user_id=USER_ID,
        )

        print(f"\nINPUT: {text}")

        for memory in result.memories:
            if memory is None:
                continue

            print(
                f"  [{memory.type.value}] "
                f"[{memory.status.value}] "
                f"{memory.content}"
            )


# ============================================================
# 8. EXAMPLE 3 — get_all()
# ============================================================

def example_get_all(system: MemorySystem) -> None:
    """
    get_all() returns all memories of the user,
    including non-active memories.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 3 — get_all()")
    print("=" * 80)

    memories = system.get_all(USER_ID)

    print(f"Total memories: {len(memories)}")

    for memory in memories:
        print(f"\nID: {memory.id}")
        print(f"Type: {memory.type.value}")
        print(f"Status: {memory.status.value}")
        print(f"Content: {memory.content}")


# ============================================================
# 9. EXAMPLE 4 — retrieve()
# ============================================================

def example_retrieve(system: MemorySystem) -> None:
    """
    retrieve() is the main read/search operation for Agent Core.

    Internally:
        Qwen3-Embedding
            ↓
        FAISS
            ↓
        Qwen3-Reranker
            ↓
        MemoryRetrieverV2
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 4 — retrieve()")
    print("=" * 80)

    queries = [
        "Где я живу?",
        "Что известно о моём брате?",
        "Какие у меня планы на следующую неделю?",
        "Какой у меня сейчас вес?",
    ]

    for query in queries:
        print(f"\nQUERY: {query}")

        results = system.retrieve(
            query=query,
            user_id=USER_ID,
            top_k=5,
        )

        if not results:
            print("  Nothing found.")
            continue

        for index, result in enumerate(results, start=1):
            memory = getattr(result, "memory", result)

            vector_score = getattr(
                result,
                "vector_score",
                None,
            )

            reranker_score = getattr(
                result,
                "reranker_score",
                None,
            )

            print(
                f"  {index}. "
                f"[{memory.type.value}] "
                f"{memory.content}"
            )

            if vector_score is not None:
                print(
                    f"      vector_score="
                    f"{float(vector_score):.4f}"
                )

            if reranker_score is not None:
                print(
                    f"      reranker_score="
                    f"{float(reranker_score):.4f}"
                )


# ============================================================
# 10. EXAMPLE 5 — retrieve() with custom top_k
# ============================================================

def example_retrieve_top_k(system: MemorySystem) -> None:
    """
    top_k controls the maximum number of returned memories.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 5 — retrieve() WITH top_k")
    print("=" * 80)

    query = "Что известно об Антоне?"

    for top_k in (1, 3, 5):
        results = system.retrieve(
            query=query,
            user_id=USER_ID,
            top_k=top_k,
        )

        print(f"\ntop_k={top_k}")

        for index, result in enumerate(results, start=1):
            memory = getattr(result, "memory", result)

            print(
                f"  {index}. "
                f"[{memory.type.value}] "
                f"{memory.content}"
            )


# ============================================================
# 11. EXAMPLE 6 — delete()
# ============================================================

def example_delete(system: MemorySystem) -> None:
    """
    delete() removes exactly one memory.

    MemorySystem checks:
        1. memory exists;
        2. memory belongs to user_id;
        3. repository deletes it;
        4. FAISS index is rebuilt.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 6 — delete()")
    print("=" * 80)

    memories = system.get_all(USER_ID)

    if not memories:
        print("No memories to delete.")
        return

    memory = memories[0]

    print(f"BEFORE DELETE:")
    print(f"  ID: {memory.id}")
    print(f"  Content: {memory.content}")

    system.delete(
        memory_id=memory.id,
        user_id=USER_ID,
    )

    print("\nDELETE FINISHED.")

    remaining = system.get_all(USER_ID)

    print(f"Remaining memories: {len(remaining)}")


# ============================================================
# 12. EXAMPLE 7 — clear()
# ============================================================

def example_clear() -> None:
    """
    clear() removes all memories of the specified user
    and rebuilds the FAISS index.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 7 — clear()")
    print("=" * 80)

    system = build_system()

    clear_user = f"{USER_ID}-clear"

    system.update(
        "Я живу в Москве.",
        clear_user,
    )

    system.update(
        "Я изучаю искусственный интеллект.",
        clear_user,
    )

    print_memories(
        system,
        clear_user,
        "BEFORE clear()",
    )

    system.clear(clear_user)

    print_memories(
        system,
        clear_user,
        "AFTER clear()",
    )

    system.close()


# ============================================================
# 13. EXAMPLE 8 — goal lifecycle
# ============================================================

def example_goal_lifecycle(system: MemorySystem) -> None:
    """
    Goal lifecycle:

        goal created
            ↓
        active
            ↓
        completion message
            ↓
        archived
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 8 — GOAL LIFECYCLE")
    print("=" * 80)

    goal_user = f"{USER_ID}-goal"

    system.update(
        "Я хочу сделать презентацию.",
        goal_user,
    )

    print_memories(
        system,
        goal_user,
        "AFTER GOAL CREATION",
    )

    system.update(
        "Презентацию я уже закончил.",
        goal_user,
    )

    print_memories(
        system,
        goal_user,
        "AFTER GOAL COMPLETION",
    )

    system.clear(goal_user)


# ============================================================
# 14. EXAMPLE 9 — state supersession
# ============================================================

def example_state_update(system: MemorySystem) -> None:
    """
    Changing state:

        84 kg
          ↓
        82.5 kg

    The old state becomes superseded.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 9 — STATE UPDATE")
    print("=" * 80)

    state_user = f"{USER_ID}-state"

    system.update(
        "Сейчас я вешу 84 килограмма.",
        state_user,
    )

    system.update(
        "Сегодня весы показали 82,5 килограмма.",
        state_user,
    )

    print_memories(
        system,
        state_user,
        "WEIGHT HISTORY",
        active_only=False,
    )

    system.clear(state_user)


# ============================================================
# 15. EXAMPLE 10 — router skips questions/commands
# ============================================================

def example_router_skip(system: MemorySystem) -> None:
    """
    CheapMemoryRouter can skip obvious non-memory inputs
    before the expensive Memory Pipeline.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 10 — CHEAP MEMORY ROUTER")
    print("=" * 80)

    router_user = f"{USER_ID}-router"

    messages = [
        "Какой сегодня день?",
        "Открой браузер.",
        "Спасибо!",
        "Я живу в Москве.",
    ]

    for text in messages:
        result = system.update(
            text=text,
            user_id=router_user,
        )

        print(f"\nINPUT: {text}")
        print(
            f"Memories affected: "
            f"{len(result.memories)}"
        )

    print_memories(
        system,
        router_user,
        "ROUTER USER MEMORY",
    )

    system.clear(router_user)


# ============================================================
# 16. EXAMPLE 11 — persistence / restart
# ============================================================

def example_restart() -> None:
    """
    SQLite persistence demonstration.

    Data is written in one MemorySystem instance,
    then another instance is created using the same paths.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 11 — PERSISTENCE / RESTART")
    print("=" * 80)

    clear_demo_data()

    user_id = f"{USER_ID}-restart"

    system = build_system()

    system.update(
        "Я живу в Москве.",
        user_id,
    )

    system.update(
        "Мой брат Антон живёт в Казани.",
        user_id,
    )

    print_memories(
        system,
        user_id,
        "BEFORE CLOSE",
    )

    system.close()

    print("\n--- SYSTEM CLOSED ---")

    system = build_system()

    print_memories(
        system,
        user_id,
        "AFTER REOPEN",
    )

    results = system.retrieve(
        query="Где живёт мой брат?",
        user_id=user_id,
        top_k=5,
    )

    print("\nRETRIEVAL AFTER REOPEN:")

    for index, result in enumerate(results, start=1):
        memory = getattr(result, "memory", result)

        print(
            f"  {index}. "
            f"[{memory.type.value}] "
            f"{memory.content}"
        )

    system.close()

    clear_demo_data()


# ============================================================
# 17. EXAMPLE 12 — direct API cheat sheet
# ============================================================

def example_api_cheat_sheet() -> None:
    """
    This function only prints the public API in compact form.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 12 — PUBLIC API CHEAT SHEET")
    print("=" * 80)

    print(
        """
1. Write memory:

    system.update(
        text="Я живу в Москве.",
        user_id="user-1",
    )

2. Get all memories:

    memories = system.get_all(
        user_id="user-1",
    )

3. Retrieve relevant memories:

    results = system.retrieve(
        query="Где я живу?",
        user_id="user-1",
        top_k=5,
    )

4. Delete one memory:

    system.delete(
        memory_id=memory.id,
        user_id="user-1",
    )

5. Clear all user memory:

    system.clear(
        user_id="user-1",
    )

6. Close system:

    system.close()
"""
    )


# ============================================================
# 18. MAIN
# ============================================================

def main() -> None:
    """
    Run all demonstrations.

    Heavy models are obtained through model_registry,
    so the same shared instances can be reused.
    """
    clear_demo_data()

    system = build_system()

    try:
        example_api_cheat_sheet()

        example_update(system)

        example_updates(system)

        print_memories(
            system,
            USER_ID,
            "CURRENT MEMORY",
            active_only=False,
        )

        example_get_all(system)

        example_retrieve(system)

        example_retrieve_top_k(system)

        example_delete(system)

        example_goal_lifecycle(system)

        example_state_update(system)

        example_router_skip(system)

    finally:
        system.close()

    example_clear()
    example_restart()

    clear_demo_data()

    print("\n" + "=" * 80)
    print("ALL MEMORY SYSTEM EXAMPLES FINISHED")
    print("=" * 80)


if __name__ == "__main__":
    main()
