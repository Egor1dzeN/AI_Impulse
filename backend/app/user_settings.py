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
        cur = conn.execute(
            "UPDATE users SET intervals_icu_api_key = ? WHERE id = ?", (api_key, user_id)
        )
        if cur.rowcount == 0:
            # Silently no-oping here (e.g. a stale JWT for a user_id that
            # no longer exists, after a DB reset) previously looked like a
            # successful save that just never "took" — the key vanished
            # into a WHERE clause matching no row, with no error anywhere.
            raise ValueError(f"no user with id={user_id!r} — log out and log back in")


def get_google_calendar_refresh_token(user_id: str) -> str | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT google_calendar_refresh_token FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row["google_calendar_refresh_token"] if row else None


def set_google_calendar_refresh_token(user_id: str, refresh_token: str | None) -> None:
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET google_calendar_refresh_token = ? WHERE id = ?", (refresh_token, user_id)
        )
        if cur.rowcount == 0:
            raise ValueError(f"no user with id={user_id!r} — log out and log back in")


def get_gmail_refresh_token(user_id: str) -> str | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT gmail_refresh_token FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["gmail_refresh_token"] if row else None


def set_gmail_refresh_token(user_id: str, refresh_token: str | None) -> None:
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE users SET gmail_refresh_token = ? WHERE id = ?", (refresh_token, user_id))
        if cur.rowcount == 0:
            raise ValueError(f"no user with id={user_id!r} — log out and log back in")


def get_settings(user_id: str) -> dict:
    return {
        "intervals_icu_connected": bool(get_intervals_icu_key(user_id)),
        "google_calendar_connected": bool(get_google_calendar_refresh_token(user_id)),
        "gmail_connected": bool(get_gmail_refresh_token(user_id)),
    }
