from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import db

PLACEHOLDER_TITLE = "Новый чат"
TITLE_MAX_LEN = 40


def create_conversation(user_id: str) -> dict[str, Any]:
    conv_id = db.new_id()
    ts = db.now_iso()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, user_id, PLACEHOLDER_TITLE, ts, ts),
        )
    return {"id": conv_id, "title": PLACEHOLDER_TITLE, "created_at": ts, "updated_at": ts}


def list_conversations(user_id: str) -> list[dict[str, Any]]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def assert_owned(conversation_id: str, user_id: str) -> None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")


def delete_conversation(conversation_id: str, user_id: str) -> None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        conn.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def touch_and_maybe_title(conversation_id: str, first_user_message: str) -> None:
    """Bump updated_at on every turn, and — the first time only, while the
    title is still the placeholder — derive a real title from the user's
    message so the chat list shows something meaningful."""
    ts = db.now_iso()
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            return

        if row["title"] == PLACEHOLDER_TITLE:
            title = first_user_message.strip()
            if len(title) > TITLE_MAX_LEN:
                title = title[:TITLE_MAX_LEN].rstrip() + "…"
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title or PLACEHOLDER_TITLE, ts, conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (ts, conversation_id),
            )
