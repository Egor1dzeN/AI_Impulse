"""MCP-сервер: тематика "Почта и мессенджеры". Отправка сообщения в
Telegram через Bot API (бесплатно, нужен только токен бота).

Это ЗАПИСЬ-действие — на стороне агента оно обязано проходить через
confirm_action (см. backend/app/write_guard.py), а не вызываться сразу.
"""

from __future__ import annotations

import os
import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("telegram")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


@mcp.tool()
def send_telegram_message(text: str) -> dict:
    """ЗАПИСЬ: отправить сообщение пользователю в Telegram.

    Args:
        text: текст сообщения.
    """
    if not BOT_TOKEN or not CHAT_ID:
        return {
            "status": "not_configured",
            "message": "Не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в backend/.env",
        }

    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    return {"status": "sent" if result.get("ok") else "error", "raw": result}


if __name__ == "__main__":
    mcp.run()
