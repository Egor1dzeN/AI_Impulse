from __future__ import annotations

from datetime import datetime, timedelta
import json
import uuid

from dreaming_engine import DreamingEngine
from memory_store import MemoryStore
from models import Memory, MemoryStatus, MemoryType, Source


USER_ID = "dreaming-test-user"


class FakeQwen:
    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_new_tokens: int = 1024,
    ) -> str:
        del system_prompt
        del max_new_tokens

        self.calls += 1
        payload = json.loads(user_prompt)
        memories = payload["active_memories"]

        first_id = memories[0]["id"]
        second_id = memories[1]["id"]

        return json.dumps(
            {
                "operations": [
                    {
                        "type": "merge",
                        "keep_id": first_id,
                        "archive_ids": [second_id],
                        "content": "Пользователь предпочитает кофе утром.",
                        "reason": "Два воспоминания относятся к одной теме.",
                    }
                ],
                "summary": "Объединены близкие воспоминания.",
            },
            ensure_ascii=False,
        )


class NullOperationsQwen:
    """Mimics a real, observed Qwen3-4B response to "nothing needs to
    change": explicit JSON nulls instead of an empty list/string. That must
    be treated as a valid no-op, not a parse failure (see
    DreamingEngine._validate_operations)."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_new_tokens: int = 1024,
    ) -> str:
        del system_prompt
        del user_prompt
        del max_new_tokens

        self.calls += 1
        return json.dumps({"operations": None, "summary": None})


def make_memory(content: str, minutes_ago: int = 0) -> Memory:
    now = datetime.utcnow() - timedelta(minutes=minutes_ago)

    return Memory(
        id=str(uuid.uuid4()),
        user_id=USER_ID,
        type=MemoryType.PREFERENCE,
        content=content,
        confidence=0.9,
        importance=0.5,
        freshness=1.0,
        status=MemoryStatus.ACTIVE,
        sources=[
            Source(
                type="text",
                id=str(uuid.uuid4()),
            )
        ],
        created_at=now,
        updated_at=now,
        valid_from=now,
        metadata={},
    )


def test_force_run() -> None:
    store = MemoryStore()
    store.add(make_memory("Пользователь любит кофе.", 2))
    store.add(make_memory("Пользователь пьёт кофе утром.", 1))

    qwen = FakeQwen()
    engine = DreamingEngine(
        store=store,
        qwen=qwen,
        every_n_memories=10,
    )

    result = engine.force_run(USER_ID)

    assert qwen.calls == 1
    assert result.applied
    assert len(result.merge_operations) == 1

    active = store.find_by_user(USER_ID, active_only=True)
    archived = [
        memory
        for memory in store.find_by_user(USER_ID, active_only=False)
        if memory.status == MemoryStatus.ARCHIVED
    ]

    assert len(active) == 1
    assert active[0].content == "Пользователь предпочитает кофе утром."
    assert len(archived) == 1


def test_threshold() -> None:
    store = MemoryStore()
    store.add(make_memory("Пользователь любит кофе.", 2))
    store.add(make_memory("Пользователь пьёт кофе утром.", 1))

    qwen = FakeQwen()
    engine = DreamingEngine(
        store=store,
        qwen=qwen,
        every_n_memories=3,
    )

    assert engine.notify_memory_changes(USER_ID, 1) is None
    assert engine.pending_count(USER_ID) == 1

    assert engine.notify_memory_changes(USER_ID, 1) is None
    assert engine.pending_count(USER_ID) == 2

    result = engine.notify_memory_changes(USER_ID, 1)

    assert result is not None
    assert result.applied
    assert engine.pending_count(USER_ID) == 0
    assert qwen.calls == 1


def test_disabled_threshold() -> None:
    store = MemoryStore()
    store.add(make_memory("Пользователь любит кофе."))

    qwen = FakeQwen()
    engine = DreamingEngine(
        store=store,
        qwen=qwen,
        every_n_memories=0,
    )

    assert engine.notify_memory_changes(USER_ID, 100) is None
    assert engine.pending_count(USER_ID) == 0
    assert qwen.calls == 0


def test_null_operations_is_not_an_error() -> None:
    store = MemoryStore()
    store.add(make_memory("Пользователь любит кофе.", 2))
    store.add(make_memory("Пользователь пьёт чай вечером.", 1))

    qwen = NullOperationsQwen()
    engine = DreamingEngine(
        store=store,
        qwen=qwen,
        every_n_memories=10,
    )

    result = engine.force_run(USER_ID)

    assert qwen.calls == 1
    assert result.merge_operations == []
    assert result.archive_operations == []
    assert not result.applied


def test_no_enough_memories() -> None:
    store = MemoryStore()
    store.add(make_memory("Пользователь любит кофе."))

    qwen = FakeQwen()
    engine = DreamingEngine(
        store=store,
        qwen=qwen,
        every_n_memories=1,
    )

    result = engine.force_run(USER_ID)

    assert result.triggered
    assert not result.applied
    assert "Not enough" in result.summary
    assert qwen.calls == 0


def main() -> None:
    print("=" * 90)
    print("DREAMING ENGINE TEST")
    print("=" * 90)

    print("\n[1] FORCE RUN")
    test_force_run()
    print("PASS")

    print("\n[2] AUTO RUN EVERY N CHANGES")
    test_threshold()
    print("PASS")

    print("\n[3] DISABLE AUTO DREAMING")
    test_disabled_threshold()
    print("PASS")

    print("\n[4] NULL OPERATIONS IS NOT AN ERROR")
    test_null_operations_is_not_an_error()
    print("PASS")

    print("\n[5] NOT ENOUGH MEMORIES")
    test_no_enough_memories()
    print("PASS")

    print("\n" + "=" * 90)
    print("ALL DREAMING TESTS PASSED")
    print("=" * 90)


if __name__ == "__main__":
    main()
