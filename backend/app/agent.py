from __future__ import annotations

import json
from datetime import date
from typing import Any, Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from . import agent_graph, db, memory_bridge
from .prompts import build_system_prompt
from .tools import MEMORY_TOOL_NAME

# Each round of the agent loop is 2 super-steps (agent -> tools), so this
# allows roughly 10 rounds of tool calls (memory searches and/or actions)
# before the graph gives up and we fall back to a canned reply.
RECURSION_LIMIT = 20

# A local 4-bit model occasionally hits EOS on the very first generated
# token (seen right after cold start) and produces a genuinely empty reply.
# When that happens with no tool call either — i.e. nothing has been shown
# to the user yet this turn — it's safe to transparently retry once before
# falling back to a canned message.
MAX_ATTEMPTS = 2


def handle_chat_turn_stream(user_id: str, message: str) -> Iterator[dict[str, Any]]:
    """Run one chat turn through the LangGraph agent, yielding incremental
    events as it works:
    {"type": "token", "text": ...} for live reply text,
    {"type": "tool_call", "tool": ..., "arguments": ...} for each tool call
      the agent makes along the way (memory searches included),
    {"type": "done", "action": ...} once the turn is fully saved, where
      `action` is the last non-memory tool call executed (or None).

    The agent decides for itself, per turn, whether it needs to consult
    memory (via the search_memory tool) and how many times to query it
    before answering — no memory context is fetched up front.
    """
    graph = agent_graph.build_graph(user_id)
    system_prompt = build_system_prompt(today=date.today().isoformat())
    inputs = {"messages": [SystemMessage(content=system_prompt), HumanMessage(content=message)]}
    config = {"recursion_limit": RECURSION_LIMIT}

    reply_parts: list[str] = []
    last_action: dict[str, Any] | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply_parts = []
        last_action = None
        had_tool_call = False
        pending_calls: dict[str, dict[str, Any]] = {}
        recursion_hit = False

        try:
            for mode, payload in graph.stream(inputs, config=config, stream_mode=["messages", "updates"]):
                if mode == "messages":
                    chunk, meta = payload
                    if meta.get("langgraph_node") == "agent" and chunk.content:
                        reply_parts.append(chunk.content)
                        yield {"type": "token", "text": chunk.content}
                    continue

                for node_name, node_update in payload.items():
                    for msg in node_update.get("messages", []):
                        if node_name == "agent" and isinstance(msg, AIMessage) and msg.tool_calls:
                            for tc in msg.tool_calls:
                                pending_calls[tc["id"]] = {"name": tc["name"], "arguments": tc["args"]}
                        elif node_name == "tools" and isinstance(msg, ToolMessage):
                            had_tool_call = True
                            call = pending_calls.pop(msg.tool_call_id, None)
                            name = call["name"] if call else msg.name
                            arguments = call["arguments"] if call else {}
                            result = _parse_tool_result(msg.content)
                            yield {"type": "tool_call", "tool": name, "arguments": arguments}
                            if name != MEMORY_TOOL_NAME:
                                last_action = {"tool": name, "arguments": arguments, "result": result}
        except GraphRecursionError:
            recursion_hit = True

        if recursion_hit:
            fallback = "Не получилось сформировать ответ за отведённое число шагов. Попробуй переформулировать вопрос."
            reply_parts = [fallback]
            yield {"type": "token", "text": fallback}
            break

        if reply_parts or had_tool_call:
            break  # got real text and/or a tool ran — nothing to retry

        # Nothing was yielded at all this attempt, so a silent retry is
        # invisible to the frontend (still shows "thinking").
        print(f"[agent] empty reply on attempt {attempt}/{MAX_ATTEMPTS} for user_id={user_id!r}, message={message!r}")

    reply = "".join(reply_parts).strip()
    if not reply:
        reply = (
            "Готово."
            if last_action is not None
            else "Не получилось получить ответ от модели. Попробуй повторить запрос."
        )
        yield {"type": "token", "text": reply}

    _save_message(user_id, "user", message, None)
    _save_message(user_id, "assistant", reply, last_action)

    yield {"type": "done", "action": last_action}


def _parse_tool_result(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _save_message(user_id: str, role: str, content: str, action: dict | None) -> None:
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
