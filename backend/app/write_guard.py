from __future__ import annotations

"""
Слой подтверждения write-действий.

В кейсе прямо написано: обращение ассистента к незнакомому платному или
изменяющему данные методу без подтверждения пользователя обнуляет баллы
по критерию "Авто-подключение к открытым API". До этого модуля
create_task/add_expense/log_meal/log_weight/log_workout (и любой новый
MCP write-тул) вызывались ToolNode-ом сразу, без остановки на
подтверждение — эта дыра закрывается здесь, один раз, на уровне сборки
списка тулов, а не в каждом тулe по отдельности.

Как это работает:
1. Для каждого тула из WRITE_TOOL_NAMES в список тулов, который видит
   модель, попадает не оригинал, а тул-заглушка с тем же именем и той
   же схемой аргументов. Вызов заглушки не выполняет реальное действие —
   он сохраняет предложенное действие (имя тула + аргументы) в
   pending_actions и возвращает текст-предложение с просьбой
   подтвердить.
2. В список тулов всегда добавляется confirm_action(confirm: bool) —
   модель обязана вызвать именно её после ответа пользователя "да"/"нет"
   (см. обновлённые ACTION_INSTRUCTIONS в prompts.py). Только тогда
   реальный оригинальный тул вызывается по-настоящему.

Расширяется добавлением имени тула в WRITE_TOOL_NAMES — ни одну функцию
самого тула трогать не нужно.
"""

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, tool

from . import db

# Существующие локальные write-тулы (tools.py) + новые MCP write-тулы
# (mcp_servers/telegram_server.py, mcp_servers/documents_server.py).
# Имена тулов из MCP-серверов приходят БЕЗ префикса сервера
# (langchain-mcp-adapters по умолчанию их не добавляет), поэтому здесь
# просто голое имя функции, объявленной через @mcp.tool().
WRITE_TOOL_NAMES = {
    "create_task",
    "add_expense",
    "log_meal",
    "log_weight",
    "log_workout",
    "send_telegram_message",
    "save_document_note",
}


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_actions (
            user_id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            tool_args TEXT NOT NULL,
            proposed_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def set_pending(user_id: str, tool_name: str, args: dict, proposed_text: str) -> None:
    with db.get_conn() as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO pending_actions "
            "(user_id, tool_name, tool_args, proposed_text, created_at) VALUES (?,?,?,?,?)",
            (user_id, tool_name, json.dumps(args, ensure_ascii=False), proposed_text, db.now_iso()),
        )


def get_pending(user_id: str) -> dict | None:
    with db.get_conn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT tool_name, tool_args, proposed_text FROM pending_actions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "tool_name": row["tool_name"],
        "tool_args": json.loads(row["tool_args"]),
        "proposed_text": row["proposed_text"],
    }


def clear_pending(user_id: str) -> None:
    with db.get_conn() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM pending_actions WHERE user_id = ?", (user_id,))


def _describe(tool_name: str, args: dict) -> str:
    pretty_args = ", ".join(f"{k}={v!r}" for k, v in args.items() if v is not None)
    return f"Предлагаю выполнить действие «{tool_name}» с параметрами: {pretty_args}."


def _make_stub(user_id: str, original: BaseTool) -> BaseTool:
    async def _stub(**kwargs: Any) -> dict:
        proposed = _describe(original.name, kwargs)
        set_pending(user_id, original.name, kwargs, proposed)
        return {
            "status": "confirmation_required",
            "proposed_action": proposed,
            "note": "Спроси у пользователя подтверждение (да/нет), прежде чем вызывать что-либо ещё.",
        }

    return StructuredTool.from_function(
        coroutine=_stub,
        name=original.name,
        description=(
            (original.description or "")
            + " ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ: этот вызов не выполняет действие сразу, "
            "а только предлагает его пользователю на подтверждение."
        ),
        args_schema=original.args_schema,
    )


def guard_write_tools(user_id: str, all_tools: list[BaseTool]) -> list[BaseTool]:
    """Заменяет в списке тулов те, что изменяют данные (WRITE_TOOL_NAMES),
    на тулы-заглушки, и добавляет общий confirm_action. Читающие тулы и
    search_memory проходят без изменений."""

    guarded: list[BaseTool] = []
    originals_by_name: dict[str, BaseTool] = {t.name: t for t in all_tools}

    for original in all_tools:
        if original.name not in WRITE_TOOL_NAMES:
            guarded.append(original)
            continue
        guarded.append(_make_stub(user_id, original))

    @tool
    async def confirm_action(confirm: bool) -> dict:
        """Подтвердить или отменить последнее предложенное действие "на
        запись" (создание задачи/траты/записи о еде/весе/тренировке,
        отправка сообщения, сохранение заметки и т.п.). Вызывай ТОЛЬКО в
        ответ на явное согласие/отказ пользователя ПОСЛЕ того, как ты сам
        предложил действие и спросил подтверждение — ни в какой другой
        ситуации, и никогда не вызывай исходный write-тул напрямую
        второй раз вместо этого."""
        pending = get_pending(user_id)
        if pending is None:
            return {"status": "no_pending_action"}

        clear_pending(user_id)

        if not confirm:
            return {"status": "cancelled", "tool_name": pending["tool_name"]}

        original = originals_by_name.get(pending["tool_name"])
        if original is None:
            return {"status": "error", "message": "Исходный инструмент недоступен, попробуйте ещё раз."}

        result = await original.ainvoke(pending["tool_args"])
        return {"status": "executed", "tool_name": pending["tool_name"], "result": result}

    guarded.append(confirm_action)
    return guarded
