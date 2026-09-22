"""MCP-сервер: тематика "Документы". Читает и ищет по документам в
песочнице (папка docs_sandbox рядом с этим файлом) и умеет сохранять
заметки — свой источник данных, без внешних API.

Закрывает из кейса: "ответить по загруженному договору с цитатой пункта".
"""

from __future__ import annotations

import os
import datetime as dt
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("documents")

# Папка с документами — переменная окружения DOCS_SANDBOX_DIR, иначе
# docs_sandbox рядом с этим файлом.
SANDBOX_DIR = Path(os.environ.get("DOCS_SANDBOX_DIR", Path(__file__).resolve().parent / "docs_sandbox"))
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".txt", ".md"}


def _safe_path(filename: str) -> Path:
    """Не даёт выйти за пределы песочницы через '../' и абсолютные пути."""
    candidate = (SANDBOX_DIR / filename).resolve()
    sandbox_resolved = SANDBOX_DIR.resolve()
    if sandbox_resolved != candidate and sandbox_resolved not in candidate.parents:
        raise ValueError("Доступ за пределы песочницы запрещён")
    return candidate


@mcp.tool()
def list_documents() -> dict:
    """Список документов, доступных ассистенту, в песочнице."""
    files = sorted(p.name for p in SANDBOX_DIR.iterdir() if p.is_file() and p.suffix in ALLOWED_EXTENSIONS)
    return {"documents": files, "folder": str(SANDBOX_DIR)}


@mcp.tool()
def read_document(filename: str) -> dict:
    """Прочитать документ целиком по имени файла (см. list_documents).

    Args:
        filename: имя файла, например "contract.txt".
    """
    path = _safe_path(filename)
    if not path.exists() or path.suffix not in ALLOWED_EXTENSIONS:
        return {"found": False, "filename": filename}
    text = path.read_text(encoding="utf-8", errors="ignore")
    return {"found": True, "filename": filename, "content": text, "chars": len(text)}


@mcp.tool()
def search_documents(query: str) -> dict:
    """Найти абзацы/пункты во всех документах песочницы, где встречается
    запрос — чтобы ответить со ссылкой на конкретный пункт документа.

    Args:
        query: искомая фраза или ключевое слово.
    """
    query_lower = query.strip().lower()
    matches = []
    for path in sorted(SANDBOX_DIR.iterdir()):
        if not (path.is_file() and path.suffix in ALLOWED_EXTENSIONS):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, paragraph in enumerate(text.split("\n\n"), start=1):
            if query_lower in paragraph.lower():
                matches.append(
                    {
                        "filename": path.name,
                        "paragraph_number": i,
                        "excerpt": paragraph.strip()[:500],
                    }
                )
    return {"query": query, "matches": matches[:10]}


@mcp.tool()
def save_document_note(note: str) -> dict:
    """ЗАПИСЬ: сохранить заметку в notes.md песочницы (исходные
    документы не трогает — только дополняет отдельный файл заметок).

    Args:
        note: текст заметки.
    """
    notes_path = SANDBOX_DIR / "notes.md"
    timestamp = dt.datetime.utcnow().isoformat()
    with notes_path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n---\n[{timestamp}]\n{note}\n")
    return {"status": "saved", "file": str(notes_path)}


if __name__ == "__main__":
    mcp.run()
