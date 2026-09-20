from __future__ import annotations

from . import db


def get_intervals_icu_key(user_id: str) -> str | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT intervals_icu_api_key FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row["intervals_icu_api_key"] if row else None


def set_intervals_icu_key(user_id: str, api_key: str | None) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE users SET intervals_icu_api_key = ? WHERE id = ?", (api_key, user_id)
        )


def get_settings(user_id: str) -> dict:
    return {"intervals_icu_connected": bool(get_intervals_icu_key(user_id))}
