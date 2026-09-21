from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

APP_DB_PATH = DATA_DIR / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    google_id TEXT,
    intervals_icu_api_key TEXT,
    google_calendar_refresh_token TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    conversation_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    action_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_conversation ON chat_messages(conversation_id);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    due_date TEXT,
    notes TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, created_at);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    note TEXT,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expenses_user ON expenses(user_id, date);

CREATE TABLE IF NOT EXISTS meals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kcal REAL NOT NULL,
    protein REAL,
    fat REAL,
    carbs REAL,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meals_user ON meals(user_id, date);

CREATE TABLE IF NOT EXISTS weights (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    value_kg REAL NOT NULL,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weights_user ON weights(user_id, date);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(APP_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns to tables created before they existed.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so new
    columns need an explicit ALTER TABLE before the schema script (which
    references them, e.g. in the google_id/conversation_id indexes) can
    run.
    """
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if user_columns and "google_id" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")

    if user_columns and "intervals_icu_api_key" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN intervals_icu_api_key TEXT")

        # This column replaces the old single global INTERVALS_ICU_API_KEY
        # env var, which every user effectively shared before per-user keys
        # (set via Settings) existed. Seed existing users with it once, so
        # this migration doesn't silently break workout logging for whoever
        # already had it working — new users start disconnected and add
        # their own key through Settings.
        env_key = os.environ.get("INTERVALS_ICU_API_KEY")
        if env_key:
            conn.execute("UPDATE users SET intervals_icu_api_key = ?", (env_key,))

    if user_columns and "google_calendar_refresh_token" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN google_calendar_refresh_token TEXT")

    # Ensure conversations exists before the backfill below can insert into
    # it — the schema script itself only runs after this function returns.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "    id TEXT PRIMARY KEY,"
        "    user_id TEXT NOT NULL,"
        "    title TEXT NOT NULL,"
        "    created_at TEXT NOT NULL,"
        "    updated_at TEXT NOT NULL"
        ")"
    )

    msg_columns = {row["name"] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if msg_columns and "conversation_id" not in msg_columns:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN conversation_id TEXT")

        # Messages saved before conversations existed have no conversation
        # of their own — fold each user's orphaned messages into one
        # "Чат" conversation instead of hiding them from the new UI.
        orphan_user_ids = [
            row["user_id"]
            for row in conn.execute(
                "SELECT DISTINCT user_id FROM chat_messages WHERE conversation_id IS NULL"
            ).fetchall()
        ]
        for user_id in orphan_user_ids:
            conv_id = new_id()
            ts = now_iso()
            conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_id, "Чат", ts, ts),
            )
            conn.execute(
                "UPDATE chat_messages SET conversation_id = ? WHERE user_id = ? AND conversation_id IS NULL",
                (conv_id, user_id),
            )


def init_db() -> None:
    with _connect() as conn:
        _migrate(conn)
        conn.executescript(_SCHEMA)


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.utcnow().isoformat()
