"""MCP-сервер: тематика "Новости". Дайджест заголовков из бесплатных
RSS-лент (без ключа), с опциональной фильтрацией по ключевому слову —
под задачу из кейса "собрать дайджест под интересы из памяти".

Парсинг RSS через xml.etree, как и get_exchange_rate в основном проекте
(tools.py) — не тянем feedparser отдельной зависимостью.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("news")

# Бесплатные RSS-ленты без ключа и регистрации.
FEEDS = {
    "tech": "https://habr.com/ru/rss/all/all/",
    "general": "https://lenta.ru/rss/news",
}

USER_AGENT = "ImpulseAssistant/1.0 (personal project; contact via GitHub)"


def _parse_rss(url: str, limit: int) -> list[dict]:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    items = []
    for item in root.findall(".//item")[:limit]:
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
            }
        )
    return items


@mcp.tool()
def get_news_digest(topic: str | None = None, limit: int = 5) -> dict:
    """Дайджест последних новостей, опционально отфильтрованный по
    ключевому слову в заголовке.

    Args:
        topic: ключевое слово для фильтра по заголовку (например
            "искусственный интеллект"), или не указывать — без фильтра.
        limit: сколько новостей вернуть (по умолчанию 5).
    """
    all_items: list[dict] = []
    errors = []
    for feed_name, url in FEEDS.items():
        try:
            all_items.extend(_parse_rss(url, limit=20))
        except Exception as e:  # noqa: BLE001
            errors.append({"feed": feed_name, "error": str(e)})

    if topic:
        topic_lower = topic.strip().lower()
        all_items = [it for it in all_items if topic_lower in it["title"].lower()]

    return {
        "topic": topic,
        "count": len(all_items[:limit]),
        "items": all_items[:limit],
        "source": "Habr / Lenta.ru RSS",
        "errors": errors or None,
    }


if __name__ == "__main__":
    mcp.run()
