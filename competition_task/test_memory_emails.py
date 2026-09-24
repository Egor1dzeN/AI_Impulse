"""Ingest emails_mock_teamlead.jsonl into the memory pipeline (tagged with
real source=email + the email's own date, see memory_bridge.ingest_email),
then ask a handful of control questions from data/reference_answers.md
through the REAL agent (backend/app/agent.py) and print actual vs
reference for manual comparison.

Scope note: only questions the fact-memory system is actually meant to
answer are included here (profile facts extracted from email content) —
NOT the spending/savings questions (11, 22), which the reference answers
compute by parsing the raw bank-alert emails directly (a job for the app's
own expenses table / a dedicated parser, not fuzzy LLM fact-memory), and
NOT "find email about X, draft a reply" (17), which is full-text email
search over raw messages, not something distilled facts can answer. Those
need separate tooling/tests.

Run from anywhere: `python competition_task/test_memory_emails.py`.
Needs backend/.env configured (GigaChat + cloud.ru keys) since ingestion
and the agent both make real API calls — with 160 emails this takes a
while and consumes real quota.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Same .env-loading approach used throughout this session's ad-hoc checks —
# dev.sh/backend.sh normally do this, but this script runs standalone.
env_path = BACKEND / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8")

from app import agent, db, memory_bridge  # noqa: E402

USER_ID = "competition_test_maria"
EMAILS_PATH = ROOT / "competition_task" / "train_dataset" / "emails_mock_teamlead.jsonl"

# (question, reference answer summary) — from data/reference_answers.md,
# restricted to what emails_mock_teamlead.jsonl alone should support.
CONTROL_QUESTIONS = [
    (
        "Как меня зовут и как ко мне обращаться?",
        "Мария Соколова. Реф.: 'Мария Соколова, коллеги — Мария в переписке' (reference_answers.md #2).",
    ),
    (
        "Какая у меня дата рождения?",
        "14.03.1994 (32 года). Реф.: ДМС-письмо кадровой службы, 'Дата рождения: 14.03.1994' (reference_answers.md #3).",
    ),
    (
        "Какой физической активностью я занимаюсь и с кем?",
        "Персональные тренировки в Energy Loft (м. Курская), тренер Алина Волошина, вторник/четверг 19:30 "
        "— подтверждается ~17 письмами-бронированиями май-сентябрь (reference_answers.md #9).",
    ),
]


def ensure_test_user() -> None:
    db.init_db()
    with db.get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (USER_ID,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (USER_ID, f"_{USER_ID}", "n/a", db.now_iso()),
            )


def ingest_all_emails() -> None:
    emails = [json.loads(line) for line in EMAILS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    emails.sort(key=lambda e: e.get("date", ""))
    print(f"Ingesting {len(emails)} emails for user_id={USER_ID}...")
    for i, email in enumerate(emails, 1):
        memory_bridge.ingest_email(USER_ID, email)
        if i % 20 == 0 or i == len(emails):
            print(f"  ...{i}/{len(emails)}")


def dump_memories() -> None:
    memories = memory_bridge.list_memories(USER_ID)
    print(f"\n=== {len(memories)} memories extracted ===")
    for m in memories:
        src = m["sources"][0] if m["sources"] else {}
        print(
            f"- [{m['type']}] {m['content']!r}\n"
            f"    source={src.get('type')} valid_from={m['valid_from']}"
        )


def ask(question: str) -> str:
    conv_id = db.new_id()
    reply_parts: list[str] = []
    for event in agent.handle_chat_turn_stream(USER_ID, conv_id, question, enable_thinking=True):
        if event["type"] == "token":
            reply_parts.append(event["text"])
    return "".join(reply_parts).strip()


def run_control_questions() -> None:
    print("\n=== Control questions (each asked twice, fresh session, per case's reproducibility rule) ===")
    for question, reference in CONTROL_QUESTIONS:
        print(f"\nQ: {question}")
        print(f"REFERENCE: {reference}")
        answer_1 = ask(question)
        answer_2 = ask(question)
        print(f"ANSWER (run 1): {answer_1}")
        print(f"ANSWER (run 2): {answer_2}")
        print(f"REPRODUCIBLE: {'yes' if answer_1.strip() == answer_2.strip() else 'NO — differs between runs'}")


if __name__ == "__main__":
    ensure_test_user()
    ingest_all_emails()
    dump_memories()
    run_control_questions()
