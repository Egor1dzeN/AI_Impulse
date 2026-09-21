from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

# Run this file from backend/app in the project.
# The memory modules live in the sibling top-level memory/ directory.
MEMORY_DIR = Path(__file__).resolve().parents[2] / "memory"
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))

from memory_input_router import MemoryInputRouter
from models import MemoryCandidate, MemoryType, Source
from unified_memory_analyzer import UnifiedMemoryAnalyzer


class FakeExtractor:
    """
    Minimal extractor stub for testing router behavior.
    """

    @staticmethod
    def _extract_rule_based(text, user_id, source):
        text = text.strip().lower().replace("ё", "е")

        if "я выбираю между omoda c5 и jac js6" in text:
            return [
                MemoryCandidate(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    type=MemoryType.GOAL,
                    content="Пользователь выбирает между Omoda C5 и JAC JS6.",
                    confidence=0.9,
                    source=source,
                    created_at=None,
                    source_text=text,
                    metadata={},
                )
            ]

        if "мне нравится уроки вождения" in text:
            return [
                MemoryCandidate(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    type=MemoryType.PREFERENCE,
                    content="Пользователь любит уроки вождения.",
                    confidence=0.9,
                    source=source,
                    created_at=None,
                    source_text=text,
                    metadata={},
                )
            ]

        return []

    @staticmethod
    def _extract_context_rule_based(
        text,
        user_id,
        source,
        context,
    ):
        return []


def test_mixed_question_is_not_dropped():
    router = MemoryInputRouter(FakeExtractor())

    source = Source(type="text", id="test-source")

    route = router.route(
        text=(
            "Я думаю над покупкой автомобиля. "
            "Я выбираю между Omoda C5 и JAC JS6. "
            "Что лучше выбрать?"
        ),
        user_id="test-user",
        context=None,
        source=source,
    )

    assert route.kind == "UNIFIED", route
    print("PASS: mixed statement + question is routed to UNIFIED")


def test_pure_question_is_dropped():
    router = MemoryInputRouter(FakeExtractor())

    source = Source(type="text", id="test-source")

    route = router.route(
        text="Что лучше выбрать: Omoda C5 или JAC JS6?",
        user_id="test-user",
        context=None,
        source=source,
    )

    assert route.kind == "DROP", route
    print("PASS: pure question is still dropped")


def test_purchase_intent_is_not_completion():
    now = None

    candidate = MemoryCandidate(
        id=str(uuid.uuid4()),
        user_id="test-user",
        type=MemoryType.GOAL,
        content="Пользователь купил автомобиль Omoda.",
        confidence=0.98,
        source=Source(type="text", id="test-source"),
        created_at=now,
        source_text="нет, все же покупаю омоду!",
        metadata={},
    )

    candidate = UnifiedMemoryAnalyzer._normalize_goal_modality(candidate)

    assert candidate.type == MemoryType.GOAL
    assert "решил приобрести" in candidate.content.lower()
    assert "купил" not in candidate.content.lower()

    print(
        "PASS: purchase intent remains a goal and is not rewritten "
        "as completed purchase"
    )


if __name__ == "__main__":
    test_mixed_question_is_not_dropped()
    test_pure_question_is_dropped()
    test_purchase_intent_is_not_completion()
    print("\nALL MEMORY INGESTION REGRESSION TESTS PASSED.")
