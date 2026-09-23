from __future__ import annotations

import json
import os
import secrets
import sqlite3
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

TELEGRAM_API = "https://api.telegram.org"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


class TelegramBotError(RuntimeError):
    pass


class TelegramAuthorizationError(TelegramBotError):
    pass


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    db_path: str = "telegram_bot.db"
    timeout: int = 20


class TelegramBotClient:
    """
    Безопасный сервисный слой для Telegram Bot API.

    App user (аккаунт Web UI) привязывается к Telegram user через одноразовый
    код, а группы отдельно привязываются к app user.

    Основные методы:
        create_connect_code(app_user_id, kind)
        get_connection_status(app_user_id)
        get_connection_message(app_user_id)
        list_connected_chats(app_user_id)
        get_recent_messages(app_user_id, chat_id, limit=20)
        send_message(app_user_id, chat_id, text)
        process_message_update(message)
        poll_once(timeout=0)

    Правило безопасности:
        ни чтение, ни отправка не выполняются только по chat_id.
        Всегда проверяется app_user_id -> chat_id.
    """

    PRIVATE = "private"
    GROUP = "group"

    def __init__(
        self,
        token: str = TELEGRAM_BOT_TOKEN,
        db_path: str = "telegram_bot.db",
        timeout: int = 20,
    ) -> None:
        if not token or token == "ВСТАВЬ_НОВЫЙ_ТОКЕН_СЮДА":
            raise ValueError("TELEGRAM_BOT_TOKEN не задан — добавь его в backend/.env")

        self.config = TelegramConfig(token=token, db_path=db_path, timeout=timeout)
        self.base_url = f"{TELEGRAM_API}/bot{token}"
        self._init_db()

    # ============================================================
    # LOW LEVEL
    # ============================================================

    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/{method}",
            json=payload or {},
            timeout=self.config.timeout,
        )

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramBotError(
                f"Telegram returned non-JSON response: HTTP {response.status_code}"
            ) from exc

        if not response.ok:
            raise TelegramBotError(
                f"Telegram API HTTP {response.status_code}: "
                f"{data.get('description', response.text)}"
            )

        if not data.get("ok"):
            raise TelegramBotError(
                data.get("description", "Telegram API request failed")
            )

        return data

    # ============================================================
    # DATABASE
    # ============================================================

    def _connect_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_connections (
                    app_user_id TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    telegram_username TEXT,
                    telegram_first_name TEXT,
                    telegram_last_name TEXT,
                    connected_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_chat_access (
                    chat_id INTEGER NOT NULL,
                    app_user_id TEXT NOT NULL,
                    chat_type TEXT NOT NULL,
                    title TEXT,
                    username TEXT,
                    connected_at TEXT NOT NULL,
                    connected_by_telegram_user_id INTEGER,
                    PRIMARY KEY (chat_id, app_user_id),
                    FOREIGN KEY (app_user_id)
                        REFERENCES telegram_connections(app_user_id)
                        ON DELETE CASCADE
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_chats (
                    chat_id INTEGER PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_message_id INTEGER,
                    chat_id INTEGER NOT NULL,
                    sender_id INTEGER,
                    sender_username TEXT,
                    sender_first_name TEXT,
                    sender_last_name TEXT,
                    text TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    UNIQUE(chat_id, telegram_message_id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_connect_codes (
                    code TEXT PRIMARY KEY,
                    app_user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tg_messages_chat_time "
                "ON telegram_messages(chat_id, sent_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tg_access_user "
                "ON telegram_chat_access(app_user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tg_access_chat "
                "ON telegram_chat_access(chat_id)"
            )
            conn.commit()

    # ============================================================
    # CONNECTION CODES
    # ============================================================

    @staticmethod
    def _make_code(length: int = 8) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def create_connect_code(
        self,
        app_user_id: str,
        kind: str,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        app_user_id = str(app_user_id).strip()
        if not app_user_id:
            raise ValueError("app_user_id cannot be empty")

        if kind not in {self.PRIVATE, self.GROUP}:
            raise ValueError("kind must be 'private' or 'group'")

        ttl_seconds = max(60, min(int(ttl_seconds), 3600))
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        with self._connect_db() as conn:
            conn.execute(
                """
                DELETE FROM telegram_connect_codes
                WHERE app_user_id = ?
                  AND kind = ?
                  AND used_at IS NULL
                """,
                (app_user_id, kind),
            )

            for _ in range(10):
                code = self._make_code()
                try:
                    conn.execute(
                        """
                        INSERT INTO telegram_connect_codes (
                            code, app_user_id, kind,
                            created_at, expires_at, used_at
                        )
                        VALUES (?, ?, ?, ?, ?, NULL)
                        """,
                        (
                            code,
                            app_user_id,
                            kind,
                            now.isoformat(),
                            expires_at.isoformat(),
                        ),
                    )
                    conn.commit()
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise TelegramBotError("Failed to generate unique connect code")

        return {
            "code": code,
            "kind": kind,
            "app_user_id": app_user_id,
            "expires_at": expires_at.isoformat(),
        }

    def _get_valid_code_owner(self, code: str, kind: str) -> str:
        code = str(code).strip().upper()
        now = datetime.now(timezone.utc)

        with self._connect_db() as conn:
            row = conn.execute(
                """
                SELECT app_user_id, kind, expires_at, used_at
                FROM telegram_connect_codes
                WHERE code = ?
                """,
                (code,),
            ).fetchone()

        if row is None:
            raise TelegramAuthorizationError("Код подключения не найден")
        if row["kind"] != kind:
            raise TelegramAuthorizationError("Код предназначен для другого подключения")
        if row["used_at"] is not None:
            raise TelegramAuthorizationError("Код уже использован")

        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= now:
            raise TelegramAuthorizationError("Срок действия кода истёк")

        return str(row["app_user_id"])

    def _consume_code(self, code: str) -> None:
        with self._connect_db() as conn:
            conn.execute(
                """
                UPDATE telegram_connect_codes
                SET used_at = ?
                WHERE code = ? AND used_at IS NULL
                """,
                (datetime.now(timezone.utc).isoformat(), str(code).strip().upper()),
            )
            conn.commit()

    # ============================================================
    # IDENTITY / AUTHORIZATION
    # ============================================================

    def _get_app_user_by_telegram_user(self, telegram_user_id: int) -> str | None:
        with self._connect_db() as conn:
            row = conn.execute(
                "SELECT app_user_id FROM telegram_connections "
                "WHERE telegram_user_id = ?",
                (int(telegram_user_id),),
            ).fetchone()
        return str(row["app_user_id"]) if row else None

    def _assert_chat_access(self, app_user_id: str, chat_id: int) -> None:
        with self._connect_db() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM telegram_chat_access
                WHERE app_user_id = ? AND chat_id = ?
                LIMIT 1
                """,
                (str(app_user_id), int(chat_id)),
            ).fetchone()

        if row is None:
            raise TelegramAuthorizationError(
                "У этого аккаунта нет доступа к указанному Telegram-чату"
            )

    def _connect_private_user(
        self,
        app_user_id: str,
        sender: dict[str, Any],
    ) -> dict[str, Any]:
        telegram_user_id = int(sender["id"])

        with self._connect_db() as conn:
            existing = conn.execute(
                """
                SELECT app_user_id
                FROM telegram_connections
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()

            if existing and str(existing["app_user_id"]) != app_user_id:
                raise TelegramAuthorizationError(
                    "Этот Telegram-аккаунт уже привязан к другому аккаунту приложения"
                )

            conn.execute(
                """
                INSERT INTO telegram_connections (
                    app_user_id,
                    telegram_user_id,
                    telegram_username,
                    telegram_first_name,
                    telegram_last_name,
                    connected_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(app_user_id) DO UPDATE SET
                    telegram_user_id = excluded.telegram_user_id,
                    telegram_username = excluded.telegram_username,
                    telegram_first_name = excluded.telegram_first_name,
                    telegram_last_name = excluded.telegram_last_name
                """,
                (
                    app_user_id,
                    telegram_user_id,
                    sender.get("username"),
                    sender.get("first_name"),
                    sender.get("last_name"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

        return {
            "connected": True,
            "app_user_id": app_user_id,
            "telegram_user_id": telegram_user_id,
        }

    def _grant_group_access(
        self,
        app_user_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        sender = message["from"]
        chat = message["chat"]

        sender_telegram_user_id = int(sender["id"])
        chat_id = int(chat["id"])

        actual_app_user_id = self._get_app_user_by_telegram_user(
            sender_telegram_user_id
        )
        if actual_app_user_id != app_user_id:
            raise TelegramAuthorizationError(
                "Команду /connect может выполнять только уже привязанный Telegram-пользователь"
            )

        if chat.get("type") not in {"group", "supergroup"}:
            raise TelegramAuthorizationError(
                "/connect CODE можно использовать только внутри группы"
            )

        now = datetime.now(timezone.utc).isoformat()

        with self._connect_db() as conn:
            conn.execute(
                """
                INSERT INTO telegram_chat_access (
                    chat_id,
                    app_user_id,
                    chat_type,
                    title,
                    username,
                    connected_at,
                    connected_by_telegram_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, app_user_id) DO UPDATE SET
                    chat_type = excluded.chat_type,
                    title = excluded.title,
                    username = excluded.username,
                    connected_at = excluded.connected_at,
                    connected_by_telegram_user_id =
                        excluded.connected_by_telegram_user_id
                """,
                (
                    chat_id,
                    app_user_id,
                    chat.get("type", ""),
                    chat.get("title"),
                    chat.get("username"),
                    now,
                    sender_telegram_user_id,
                ),
            )
            conn.commit()

        return {
            "connected": True,
            "app_user_id": app_user_id,
            "chat_id": chat_id,
            "chat_type": chat.get("type"),
            "title": chat.get("title"),
        }

    # ============================================================
    # CONNECTION STATUS
    # ============================================================

    def get_bot_info(self) -> dict[str, Any]:
        return self._request("getMe")["result"]

    def get_connection_status(self, app_user_id: str) -> dict[str, Any]:
        app_user_id = str(app_user_id)
        bot = self.get_bot_info()

        with self._connect_db() as conn:
            connection = conn.execute(
                """
                SELECT telegram_user_id, telegram_username,
                       telegram_first_name, connected_at
                FROM telegram_connections
                WHERE app_user_id = ?
                """,
                (app_user_id,),
            ).fetchone()

            chats = conn.execute(
                "SELECT COUNT(*) AS count FROM telegram_chat_access "
                "WHERE app_user_id = ?",
                (app_user_id,),
            ).fetchone()

        return {
            "connected": connection is not None,
            "app_user_id": app_user_id,
            "telegram_user_id": (
                int(connection["telegram_user_id"]) if connection else None
            ),
            "telegram_username": (
                connection["telegram_username"] if connection else None
            ),
            "bot_username": bot.get("username"),
            "bot_name": bot.get("first_name"),
            "chat_count": int(chats["count"]),
        }

    def get_connection_message(self, app_user_id: str) -> str:
        status = self.get_connection_status(app_user_id)
        username = status.get("bot_username")

        if not username:
            return "У Telegram-бота отсутствует username."

        if status["connected"]:
            return (
                f"Telegram подключён. Бот: @{username}. "
                f"Подключённых чатов: {status['chat_count']}."
            )

        return (
            "Сначала подключите Telegram-бота: "
            f"откройте @{username}, нажмите Start и используйте "
            "код подключения, который выдаст Web UI."
        )

    # ============================================================
    # TOOL 1 — LIST CONNECTED CHATS
    # ============================================================

    def list_connected_chats(self, app_user_id: str) -> list[dict[str, Any]]:
        """Return only chats authorized for this app user."""
        with self._connect_db() as conn:
            rows = conn.execute(
                """
                SELECT chat_id,
                       chat_type AS type,
                       title,
                       username,
                       connected_at
                FROM telegram_chat_access
                WHERE app_user_id = ?
                ORDER BY connected_at DESC
                """,
                (str(app_user_id),),
            ).fetchall()

        return [dict(row) for row in rows]

    # ============================================================
    # TOOL 2 — READ MESSAGES
    # ============================================================

    def get_recent_messages(
        self,
        app_user_id: str,
        chat_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return messages only from an authorized chat."""
        self._assert_chat_access(app_user_id, chat_id)
        limit = max(1, min(int(limit), 100))

        with self._connect_db() as conn:
            rows = conn.execute(
                """
                SELECT id,
                       telegram_message_id,
                       chat_id,
                       sender_id,
                       sender_username,
                       sender_first_name,
                       sender_last_name,
                       text,
                       sent_at
                FROM telegram_messages
                WHERE chat_id = ?
                ORDER BY sent_at DESC, id DESC
                LIMIT ?
                """,
                (int(chat_id), limit),
            ).fetchall()

        return [dict(row) for row in reversed(rows)]

    # ============================================================
    # TOOL 3 — WRITE / SEND MESSAGE
    # ============================================================

    def send_message(
        self,
        app_user_id: str,
        chat_id: int,
        text: str,
    ) -> dict[str, Any]:
        """Send a message only to an authorized chat."""
        self._assert_chat_access(app_user_id, chat_id)

        text = str(text).strip()
        if not text:
            raise ValueError("Message text cannot be empty")

        data = self._request(
            "sendMessage",
            {"chat_id": int(chat_id), "text": text},
        )

        result = data["result"]
        return {
            "sent": True,
            "app_user_id": app_user_id,
            "chat_id": int(chat_id),
            "message_id": result["message_id"],
            "text": result.get("text", text),
        }

    # ============================================================
    # MESSAGE PROCESSING
    # ============================================================

    def _save_chat(self, chat: dict[str, Any]) -> None:
        with self._connect_db() as conn:
            conn.execute(
                """
                INSERT INTO telegram_chats (
                    chat_id,
                    type,
                    title,
                    username,
                    first_name,
                    last_name,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    type = excluded.type,
                    title = excluded.title,
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    int(chat["id"]),
                    chat.get("type", ""),
                    chat.get("title"),
                    chat.get("username"),
                    chat.get("first_name"),
                    chat.get("last_name"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def _save_message(self, message: dict[str, Any]) -> bool:
        text = str(message.get("text") or "").strip()
        if not text:
            return False

        chat = message["chat"]
        sender = message["from"]
        sent_at = datetime.fromtimestamp(
            int(message["date"]),
            tz=timezone.utc,
        ).isoformat()

        with self._connect_db() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO telegram_messages (
                    telegram_message_id,
                    chat_id,
                    sender_id,
                    sender_username,
                    sender_first_name,
                    sender_last_name,
                    text,
                    sent_at,
                    raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.get("message_id"),
                    int(chat["id"]),
                    sender.get("id"),
                    sender.get("username"),
                    sender.get("first_name"),
                    sender.get("last_name"),
                    text,
                    sent_at,
                    json.dumps(message, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def process_message_update(self, message: dict[str, Any]) -> dict[str, Any]:
        """Process one Telegram message and resolve its app owner."""
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = str(message.get("text") or "").strip()

        if not chat or not sender:
            return {"processed": False, "reason": "missing_chat_or_sender"}

        chat_id = int(chat["id"])
        sender_id = int(sender["id"])
        self._save_chat(chat)

        # --------------------------------------------------------
        # /start CODE -> private Telegram account binding
        # --------------------------------------------------------
        if text.startswith("/start") and chat.get("type") == "private":
            parts = text.split(maxsplit=1)

            if len(parts) < 2:
                self._save_message(message)
                return {
                    "processed": True,
                    "kind": "start_without_code",
                    "chat_id": chat_id,
                    "telegram_user_id": sender_id,
                }

            code = parts[1].strip()
            app_user_id = self._get_valid_code_owner(
                code, self.PRIVATE
            )
            result = self._connect_private_user(
                app_user_id, sender
            )
            self._consume_code(code)
            self._save_message(message)

            return {
                "processed": True,
                "kind": "private_connected",
                **result,
            }

        # --------------------------------------------------------
        # /connect CODE -> group binding
        # --------------------------------------------------------
        if text.startswith("/connect"):
            if chat.get("type") not in {"group", "supergroup"}:
                self._save_message(message)
                return {
                    "processed": True,
                    "kind": "connect_wrong_chat_type",
                    "chat_id": chat_id,
                }

            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                self._save_message(message)
                return {
                    "processed": True,
                    "kind": "connect_without_code",
                    "chat_id": chat_id,
                }

            code = parts[1].strip()
            app_user_id = self._get_valid_code_owner(
                code, self.GROUP
            )

            # Authorization is checked before the code is consumed.
            result = self._grant_group_access(
                app_user_id, message
            )
            self._consume_code(code)
            self._save_message(message)

            return {
                "processed": True,
                "kind": "group_connected",
                **result,
            }

        # --------------------------------------------------------
        # Ordinary message
        # --------------------------------------------------------
        app_user_id = self._resolve_message_owner(message)

        if app_user_id is None:
            # The Telegram identity/chat is known, but no application owner
            # is known yet. The message is not attached to any user's memory.
            return {
                "processed": True,
                "kind": "message_from_unconnected_chat",
                "chat_id": chat_id,
                "telegram_user_id": sender_id,
                "saved": False,
            }

        self._assert_chat_access(app_user_id, chat_id)
        saved = self._save_message(message)

        return {
            "processed": True,
            "kind": "ordinary_message",
            "app_user_id": app_user_id,
            "chat_id": chat_id,
            "telegram_user_id": sender_id,
            "saved": saved,
            "text": text,
        }

    def _resolve_message_owner(
        self,
        message: dict[str, Any],
    ) -> str | None:
        sender = message.get("from") or {}
        chat = message.get("chat") or {}

        telegram_user_id = sender.get("id")
        chat_id = chat.get("id")

        if telegram_user_id is None:
            return None

        if chat.get("type") == "private":
            return self._get_app_user_by_telegram_user(
                int(telegram_user_id)
            )

        if chat_id is None:
            return None

        # For the current prototype one group can be connected to one app
        # account. Sender ID is still saved separately in each message.
        with self._connect_db() as conn:
            row = conn.execute(
                """
                SELECT app_user_id
                FROM telegram_chat_access
                WHERE chat_id = ?
                ORDER BY connected_at DESC
                LIMIT 1
                """,
                (int(chat_id),),
            ).fetchone()

        return str(row["app_user_id"]) if row else None

    # ============================================================
    # POLLING
    # ============================================================

    def poll_once(self, timeout: int = 0) -> dict[str, Any]:
        """Receive and process pending message updates once."""
        timeout = max(0, min(int(timeout), 30))

        data = self._request(
            "getUpdates",
            {
                "timeout": timeout,
                "allowed_updates": ["message"],
            },
        )

        updates = data["result"]
        processed: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for update in updates:
            message = update.get("message")
            if not message:
                continue

            try:
                result = self.process_message_update(message)
                result["update_id"] = update.get("update_id")
                processed.append(result)
            except Exception as exc:
                errors.append(
                    {
                        "update_id": update.get("update_id"),
                        "error": str(exc),
                    }
                )

        # Confirm all updates so they are not returned again.
        if updates:
            last_update_id = int(updates[-1]["update_id"])
            self._request(
                "getUpdates",
                {
                    "offset": last_update_id + 1,
                    "timeout": 0,
                    "allowed_updates": ["message"],
                },
            )

        return {
            "updates_received": len(updates),
            "processed": processed,
            "errors": errors,
        }
