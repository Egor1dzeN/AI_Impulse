"""Telegram bot ↔ Impulse.

- Linking: the Web UI issues a one-time code; the user opens
  t.me/<bot>?start=CODE (private chat) or sends /connect CODE in a group.
- The bot is a second chat interface: a private message from a linked user
  runs the same agent turn as the web chat (conversation "Telegram", visible
  in the web chat list — confirmation cards for drafts live there) and the
  answer goes back to Telegram.
- Group messages are only stored, for the agent's telegram_messages tool.

Receiving uses getUpdates long polling in one background thread and runs only
where TELEGRAM_POLLING=1: Telegram hands each update to a single poller, so a
local dev backend and the stand must not both poll with the same token.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import conversations, db
from .telegram_bot_client import TelegramAuthorizationError, TelegramBotClient, TelegramBotError

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "telegram_bot.db"
CONVERSATION_TITLE = "Telegram"

_client: TelegramBotClient | None = None
_client_lock = threading.Lock()
_bot_username: str | None = None
_stop = threading.Event()
_thread: threading.Thread | None = None
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tg-agent")


def get_client() -> TelegramBotClient | None:
    """The shared client, or None when no bot token is configured."""
    global _client
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    if _client is None:
        with _client_lock:
            if _client is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                _client = TelegramBotClient(token=token, db_path=str(DB_PATH), timeout=30)
                with sqlite3.connect(str(DB_PATH)) as conn:
                    conn.execute("CREATE TABLE IF NOT EXISTS tg_sent_drafts (draft_id TEXT PRIMARY KEY, app_user_id TEXT, sent_at TEXT)")
                    conn.execute("CREATE TABLE IF NOT EXISTS tg_conversations (app_user_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL)")
    return _client


def bot_username() -> str | None:
    global _bot_username
    client = get_client()
    if client and _bot_username is None:
        try:
            _bot_username = client.get_bot_info().get("username")
        except TelegramBotError as exc:
            logger.warning("telegram: getMe failed: %s", exc)
    # The host may be unable to reach Telegram (see TELEGRAM_PROXY): the link
    # t.me/<bot>?start=CODE is still buildable from the configured name.
    return _bot_username or (os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or None)


def polling_enabled() -> bool:
    return os.environ.get("TELEGRAM_POLLING", "").strip().lower() in ("1", "true", "yes", "on")


def is_connected(user_id: str) -> bool:
    client = get_client()
    if not client:
        return False
    with client._connect_db() as conn:
        return conn.execute("SELECT 1 FROM telegram_connections WHERE app_user_id = ?", (user_id,)).fetchone() is not None


def status(user_id: str) -> dict[str, Any]:
    client = get_client()
    if not client:
        return {"configured": False, "connected": False}
    with client._connect_db() as conn:
        row = conn.execute(
            "SELECT telegram_username, telegram_first_name FROM telegram_connections WHERE app_user_id = ?", (user_id,)
        ).fetchone()
    return {
        "configured": True,
        "receiving": polling_enabled(),
        "bot_username": bot_username(),
        "connected": row is not None,
        "telegram_username": row["telegram_username"] if row else None,
        "telegram_name": row["telegram_first_name"] if row else None,
        "chats": client.list_connected_chats(user_id) if row else [],
    }


# ============================================================
# Drafts: sent exactly once, only from the confirmation button
# ============================================================


def draft_already_sent(draft_id: str) -> bool:
    with sqlite3.connect(str(DB_PATH)) as conn:
        return conn.execute("SELECT 1 FROM tg_sent_drafts WHERE draft_id = ?", (draft_id,)).fetchone() is not None


def mark_draft_sent(draft_id: str, user_id: str) -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("INSERT OR IGNORE INTO tg_sent_drafts (draft_id, app_user_id, sent_at) VALUES (?, ?, ?)",
                     (draft_id, user_id, db.now_iso()))


# ============================================================
# The bot as a chat interface
# ============================================================


def _telegram_conversation(user_id: str) -> str:
    with sqlite3.connect(str(DB_PATH)) as conn:
        row = conn.execute("SELECT conversation_id FROM tg_conversations WHERE app_user_id = ?", (user_id,)).fetchone()
    if row:
        try:
            conversations.assert_owned(row[0], user_id)
            return row[0]
        except Exception:  # noqa: BLE001 — deleted from the web UI: start a new one
            pass
    conv_id = conversations.create_conversation(user_id)["id"]
    with db.get_conn() as conn:
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (CONVERSATION_TITLE, conv_id))
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("INSERT OR REPLACE INTO tg_conversations (app_user_id, conversation_id) VALUES (?, ?)", (user_id, conv_id))
    return conv_id


def _answer_with_agent(user_id: str, chat_id: int, text: str) -> None:
    from . import agent  # late import: agent pulls in the whole model stack

    client = get_client()
    if not client:
        return
    client.send_typing(chat_id)
    reply, action = "", None
    try:
        conv_id = _telegram_conversation(user_id)
        for event in agent.handle_chat_turn_stream(user_id, conv_id, text):
            if event["type"] == "done":
                reply, action = event.get("reply") or "", event.get("action")
        if action and action.get("tool") in agent.ACTION_TOOL_NAMES:
            reply += "\n\n(Черновик действия ждёт подтверждения в чате «Telegram» на сайте Impulse.)"
        client.send_raw(chat_id, reply or "Не получилось сформировать ответ.")
    except Exception:  # noqa: BLE001 — never let one message kill the poller
        logger.exception("telegram: agent turn failed for user_id=%s", user_id)
        client.send_raw(chat_id, "Не получилось ответить — попробуйте ещё раз чуть позже.")
        return
    try:
        agent.extract_memory_background(user_id, text)
    except Exception:  # noqa: BLE001
        logger.exception("telegram: memory extraction failed for user_id=%s", user_id)


_HINTS = {
    "start_without_code": "Привет! Я бот ассистента Impulse. Чтобы подключить меня, откройте на сайте «Настройки → Telegram → "
                          "Подключить» и перейдите по ссылке оттуда.",
    "connect_wrong_chat_type": "Команда /connect работает только в группе. В личном чате подключение идёт по ссылке из настроек.",
    "connect_without_code": "Нужен код: /connect КОД. Возьмите его на сайте в «Настройках → Telegram → Подключить группу».",
}


def _handle(result: dict[str, Any]) -> None:
    client = get_client()
    kind = result.get("kind")
    chat_id = result.get("chat_id") or result.get("telegram_user_id")
    if kind == "private_connected":
        client.send_raw(result["telegram_user_id"], "✅ Telegram подключён к вашему аккаунту Impulse. Пишите сюда вопросы — "
                                                   "отвечу так же, как в чате на сайте.")
    elif kind == "group_connected":
        client.send_raw(chat_id, "✅ Группа подключена к Impulse. Ассистент сможет читать сообщения этой группы "
                                 "и отправлять сюда сообщения — только после вашего подтверждения на сайте.")
    elif kind in _HINTS:
        client.send_raw(chat_id, _HINTS[kind])
    elif kind == "message_from_unconnected_chat":
        if chat_id and int(chat_id) > 0:  # private chat; unconnected groups stay silent
            client.send_raw(chat_id, "Этот Telegram ещё не подключён к Impulse. Откройте на сайте «Настройки → Telegram → "
                                     "Подключить» и перейдите по ссылке.")
    elif kind == "ordinary_message" and int(chat_id) > 0 and result.get("text"):
        _workers.submit(_answer_with_agent, result["app_user_id"], int(chat_id), result["text"])


def _poll_loop() -> None:
    logger.info("telegram: polling started (bot @%s)", bot_username())
    while not _stop.is_set():
        client = get_client()
        try:
            batch = client.poll_once(timeout=20)
        except Exception:  # noqa: BLE001 — network hiccup / 409 from a second poller
            logger.exception("telegram: getUpdates failed")
            _stop.wait(5)
            continue
        for result in batch["processed"]:
            try:
                _handle(result)
            except Exception:  # noqa: BLE001
                logger.exception("telegram: handling %s failed", result.get("kind"))
        for err in batch["errors"]:
            logger.warning("telegram: update %s failed: %s", err.get("update_id"), err.get("error"))
            if err.get("chat_id") and "код" in (err.get("error") or "").lower():
                try:
                    client.send_raw(err["chat_id"], f"Не получилось подключить: {err['error']}. Возьмите новый код в настройках.")
                except TelegramBotError:
                    pass


def start_polling() -> None:
    global _thread
    if _thread or not get_client() or not polling_enabled():
        return
    _stop.clear()
    _thread = threading.Thread(target=_poll_loop, name="telegram-poll", daemon=True)
    _thread.start()


def stop_polling() -> None:
    _stop.set()


# Re-exported for main.py / tools.py error handling.
__all__ = ["TelegramAuthorizationError", "TelegramBotError", "get_client", "status", "start_polling", "stop_polling",
           "is_connected", "bot_username", "draft_already_sent", "mark_draft_sent", "polling_enabled"]
