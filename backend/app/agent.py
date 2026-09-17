from __future__ import annotations

from datetime import date
from typing import Any, Iterator

from . import db, llm, memory_bridge, tools

# A plain-text reply never legitimately starts this way — only a tool_call
# block does — so this prefix is enough to decide, from the first streamed
# chunk, whether to show tokens live or hold them back until the tool runs.
_TOOL_CALL_PREFIXES = ("```", "<tool_call>")


def handle_chat_turn_stream(user_id: str, message: str) -> Iterator[dict[str, Any]]:
    """Run one chat turn, yielding incremental events as the model generates:
    {"type": "token", "text": ...} for live reply text,
    {"type": "tool_call", "tool": ..., "arguments": ...} once an action starts,
    {"type": "done", "action": ...} once the turn is fully saved.
    """
    memory_context = memory_bridge.retrieve_memory_context(message, user_id)
    system_prompt = llm.build_system_prompt(memory_context, today=date.today().isoformat())

    buffer = ""
    decided = False
    is_tool_call = False
    flushed = False

    for chunk in llm.generate_reply_stream(system_prompt, message):
        buffer += chunk
        if not decided and len(buffer) >= 4:
            decided = True
            is_tool_call = buffer.lstrip().startswith(_TOOL_CALL_PREFIXES)
        if decided and not is_tool_call:
            if not flushed:
                yield {"type": "token", "text": buffer}
                flushed = True
            else:
                yield {"type": "token", "text": chunk}

    if not decided:
        is_tool_call = buffer.lstrip().startswith(_TOOL_CALL_PREFIXES)
        if not is_tool_call:
            yield {"type": "token", "text": buffer}

    raw_reply = buffer
    tool_call = llm.parse_tool_call(raw_reply)

    action = None
    reply = raw_reply

    if tool_call is not None:
        name = tool_call["name"]
        arguments = tool_call.get("arguments") or {}
        try:
            result = tools.call_tool(name, user_id, arguments)
            yield {"type": "tool_call", "tool": name, "arguments": arguments}

            followup = ""
            for chunk in llm.generate_followup_stream(system_prompt, message, name, arguments, result):
                followup += chunk
                yield {"type": "token", "text": chunk}

            reply = followup
            action = {"tool": name, "arguments": arguments, "result": result}
        except (ValueError, TypeError) as exc:
            reply = f"Не получилось выполнить действие: {exc}"
            yield {"type": "token", "text": reply}
    elif is_tool_call:
        # Looked like a tool_call block but failed to parse — fall back to
        # showing the raw text, since it was withheld from the live stream.
        yield {"type": "token", "text": reply}

    _save_message(user_id, "user", message, None)
    _save_message(user_id, "assistant", reply, action)

    yield {"type": "done", "action": action}


def _save_message(user_id: str, role: str, content: str, action: dict | None) -> None:
    import json

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, action_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                db.new_id(),
                user_id,
                role,
                content,
                json.dumps(action, ensure_ascii=False) if action else None,
                db.now_iso(),
            ),
        )


def extract_memory_background(user_id: str, message: str) -> None:
    memory_bridge.update_memory(message, user_id)
