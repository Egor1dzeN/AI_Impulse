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
    gmail_refresh_token TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;

-- MCP servers a user added in Settings (remote HTTPS only — see mcp_tools.py).
-- auth_header is never sent back to the browser.
CREATE TABLE IF NOT EXISTS user_mcp_servers (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    auth_header TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

-- Guards against re-sending the same draft twice (e.g. a page refresh
-- re-rendering an already-sent draft card with a live "Send" button) —
-- POST /api/gmail/send checks this before calling Gmail's API at all.
CREATE TABLE IF NOT EXISTS sent_email_drafts (
    draft_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

-- Same idempotency guard as sent_email_drafts above, for the calendar
-- create/update/delete confirmation cards (POST /api/calendar/confirm) —
-- see google_calendar.py's action_already_done()/mark_action_done().
CREATE TABLE IF NOT EXISTS calendar_action_drafts (
    draft_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    done_at TEXT NOT NULL
);

-- Maps an imported calendar record's own `id` (from the uploaded
-- .jsonl/.json export) to the real event it created on the user's Google
-- Calendar, so re-running the same Data Import (POST /api/import,
-- source=calendar, sync_calendar=true) is a no-op instead of creating
-- duplicates — see main.py's import_calendar_to_google().
CREATE TABLE IF NOT EXISTS imported_calendar_events (
    user_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    google_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, external_id)
);

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
    type TEXT NOT NULL DEFAULT 'expense',
    category TEXT NOT NULL,
    note TEXT,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT,
    external_id TEXT,
    merchant TEXT,
    balance REAL
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

-- Imported source records kept verbatim (after masking) so exact questions
-- (free slots, spend per month, unanswered mail, ...) are answered by
-- deterministic queries instead of the fuzzy fact memory. See
-- personal_data.py / personal_analytics.py.
CREATE TABLE IF NOT EXISTS raw_emails (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    thread_id TEXT,
    folder TEXT,
    direction TEXT NOT NULL DEFAULT 'in',
    date TEXT,
    from_name TEXT,
    from_email TEXT,
    to_json TEXT,
    cc_json TEXT,
    subject TEXT,
    body TEXT,
    attachments_json TEXT,
    flags_json TEXT,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS idx_raw_emails_user_date ON raw_emails(user_id, date);

CREATE TABLE IF NOT EXISTS raw_calendar_events (
    user_id TEXT NOT NULL,
    id TEXT NOT NULL,
    series_id TEXT,
    summary TEXT,
    description TEXT,
    location TEXT,
    start TEXT,
    end TEXT,
    all_day INTEGER NOT NULL DEFAULT 0,
    timezone TEXT,
    status TEXT,
    transparency TEXT,
    event_type TEXT,
    importance TEXT,
    organizer_email TEXT,
    organizer_name TEXT,
    attendees_json TEXT,
    recurrence_json TEXT,
    original_start TEXT,
    flags_json TEXT,
    PRIMARY KEY (user_id, id)
);
CREATE INDEX IF NOT EXISTS idx_raw_calendar_user_start ON raw_calendar_events(user_id, start);

CREATE TABLE IF NOT EXISTS raw_messages (
    user_id TEXT NOT NULL,
    chat TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    date TEXT,
    sender TEXT,
    is_out INTEGER NOT NULL DEFAULT 0,
    text TEXT,
    reply_to TEXT,
    flags_json TEXT,
    PRIMARY KEY (user_id, chat, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_messages_user_date ON raw_messages(user_id, date);

CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_external
    ON expenses(user_id, source, external_id) WHERE external_id IS NOT NULL;
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

    if user_columns and "gmail_refresh_token" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN gmail_refresh_token TEXT")

    # Rows written before income tracking existed have no "type" — they
    # were all spends, so backfill 'expense' rather than leaving them NULL.
    expense_columns = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)").fetchall()}
    if expense_columns and "type" not in expense_columns:
        conn.execute("ALTER TABLE expenses ADD COLUMN type TEXT NOT NULL DEFAULT 'expense'")
    for column, ddl in (
        ("source", "TEXT"),
        ("external_id", "TEXT"),
        ("merchant", "TEXT"),
        ("balance", "REAL"),
    ):
        if expense_columns and column not in expense_columns:
            conn.execute(f"ALTER TABLE expenses ADD COLUMN {column} {ddl}")

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


# The app-DB half of the account-wide "forget me" reset — see
# memory_bridge.delete_all_memories() for the other half (the fact-memory
# system, which lives in its own memory.db, not here).
def delete_all_user_data(user_id: str) -> None:
    """Wipe every per-user row this app itself logged, in every table that
    has one — everything except `users` itself, so the account's
    credentials (password hash, Google/Gmail/Intervals.icu tokens) survive
    the reset and the user doesn't get logged out or have to reconnect
    integrations."""
    with get_conn() as conn:
        for table in (
            "tasks",
            "expenses",
            "meals",
            "weights",
            "imported_calendar_events",
            "raw_emails",
            "raw_calendar_events",
            "raw_messages",
            "sent_email_drafts",
            "calendar_action_drafts",
            "chat_messages",
            "conversations",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))


def now_iso() -> str:
    return datetime.utcnow().isoformat()
