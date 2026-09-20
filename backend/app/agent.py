from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from . import agent_graph, conversations, db, memory_bridge
from .prompts import build_system_prompt
from .tools import MEMORY_TOOL_NAME

# Each round of the agent loop is 2 super-steps (agent -> tools), so this
# allows roughly 10 rounds of tool calls (memory searches and/or actions)
# before the graph gives up and we fall back to a canned reply.
RECURSION_LIMIT = 20

logger = logging.getLogger(__name__)

# A local 4-bit model occasionally hits EOS on the very first generated
# token (seen right after cold start) and produces a genuinely empty reply.
# When that happens with no tool call either — i.e. nothing has been shown
# to the user yet this turn — it's safe to transparently retry once before
# falling back to a canned message.
MAX_ATTEMPTS = 2

# How many of the most recent saved messages (both roles combined) to feed
# back in as short-term conversational context, on top of whatever the
# agent pulls from long-term memory via search_memory. Kept small since
# this is a local model — every extra message lengthens the prompt and
# slows generation.
HISTORY_MESSAGE_LIMIT = 10

# Tools whose replies are cheap to re-fetch and easy for a small model to
# latch onto: seeing its own "Сегодня в Сиднее +18°C..." sitting right in
# context strongly biases it to repeat/rehash that instead of reasoning
# about the new message (confirmed in practice — tightening the prompt
# alone didn't stop it). Replies driven by these tools are swapped out for
# a neutral placeholder in short-term history so there's nothing tempting
# to copy; the real content is untouched in the DB and still shown to the
# user, this only affects what the model sees on the *next* turn.
_VOLATILE_INFO_TOOLS = {"get_weather", "get_exchange_rate", "show_map"}

# Tool arguments/results can carry a big memory-context blob (search_memory)
# or a long geometry payload — cap what lands in the log so one call can't
# blow up the file, while still keeping enough to see what happened.
_LOG_VALUE_LIMIT = 800


def _log_repr(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > _LOG_VALUE_LIMIT:
        text = text[:_LOG_VALUE_LIMIT] + f"...<{len(text) - _LOG_VALUE_LIMIT} more chars>"
    return text


def _load_recent_history(user_id: str, conversation_id: str) -> list[HumanMessage | AIMessage]:
    # Order by rowid, not created_at: two messages saved in quick succession
    # (e.g. the user's message and the assistant's reply right after it) can
    # land on the same millisecond-resolution timestamp on Windows, which
    # made created_at-based ordering nondeterministic. rowid is SQLite's
    # own monotonically-increasing insertion order, so it's always correct.
    # Scoped to conversation_id too — otherwise switching chats would leak
    # unrelated context from a different conversation into the model.
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, action_json FROM ("
            "  SELECT role, content, action_json, rowid FROM chat_messages "
            "  WHERE user_id = ? AND conversation_id = ? ORDER BY rowid DESC LIMIT ?"
            ") ORDER BY rowid ASC",
            (user_id, conversation_id, HISTORY_MESSAGE_LIMIT),
        ).fetchall()

    history: list[HumanMessage | AIMessage] = []
    for row in rows:
        if row["role"] == "user":
            history.append(HumanMessage(content=row["content"]))
            continue

        tool_name = None
        if row["action_json"]:
            try:
                tool_name = json.loads(row["action_json"]).get("tool")
            except json.JSONDecodeError:
                pass

        if tool_name in _VOLATILE_INFO_TOOLS:
            content = f"[Ответ был получен через вызов {tool_name} — не повторяй эти данные, если не спросят снова]"
        else:
            content = row["content"]
        history.append(AIMessage(content=content))

    return history


def handle_chat_turn_stream(
    user_id: str, conversation_id: str, message: str, enable_thinking: bool = True
) -> Iterator[dict[str, Any]]:
    """Run one chat turn through the LangGraph agent, yielding incremental
    events as it works:
    {"type": "token", "text": ...} for live reply text,
    {"type": "tool_call", "tool": ..., "arguments": ...} for each tool call
      the agent makes along the way (memory searches included),
    {"type": "done", "action": ...} once the turn is fully saved, where
      `action` is the last non-memory tool call executed (or None).

    The agent decides for itself, per turn, whether it needs to consult
    long-term memory (via the search_memory tool) and how many times to
    query it before answering — no long-term memory context is fetched up
    front. Short-term context is different: the last HISTORY_MESSAGE_LIMIT
    messages saved under this conversation_id are always prepended, so the
    model sees recent back-and-forth directly rather than only through
    extracted facts — long-term memory is user-wide (spans conversations),
    this short-term history is scoped to just this one conversation.
    """
    logger.info(
        "chat turn start: user_id=%s conversation_id=%s message=%s",
        user_id, conversation_id, _log_repr(message),
    )

    graph = agent_graph.build_graph(user_id, enable_thinking=enable_thinking)
    system_prompt = build_system_prompt(today=date.today().isoformat())
    history = _load_recent_history(user_id, conversation_id)
    logger.info("chat turn: loaded %d short-term history message(s) for conversation_id=%s", len(history), conversation_id)
    inputs = {
        "messages": [SystemMessage(content=system_prompt), *history, HumanMessage(content=message)],
    }
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
                                logger.info(
                                    "tool call requested: user_id=%s %s(%s)",
                                    user_id, tc["name"], _log_repr(tc["args"]),
                                )
                        elif node_name == "tools" and isinstance(msg, ToolMessage):
                            had_tool_call = True
                            call = pending_calls.pop(msg.tool_call_id, None)
                            name = call["name"] if call else msg.name
                            arguments = call["arguments"] if call else {}
                            result = _parse_tool_result(msg.content)
                            logger.info(
                                "tool call result: user_id=%s %s(%s) -> %s",
                                user_id, name, _log_repr(arguments), _log_repr(result),
                            )
                            yield {"type": "tool_call", "tool": name, "arguments": arguments}
                            if name != MEMORY_TOOL_NAME:
                                last_action = {"tool": name, "arguments": arguments, "result": result}
        except GraphRecursionError:
            recursion_hit = True
            logger.warning(
                "recursion limit (%d) hit for user_id=%s conversation_id=%s message=%r",
                RECURSION_LIMIT, user_id, conversation_id, message,
            )

        if recursion_hit:
            fallback = "Не получилось сформировать ответ за отведённое число шагов. Попробуй переформулировать вопрос."
            reply_parts = [fallback]
            yield {"type": "token", "text": fallback}
            break

        if reply_parts or had_tool_call:
            break  # got real text and/or a tool ran — nothing to retry

        # Nothing was yielded at all this attempt, so a silent retry is
        # invisible to the frontend (still shows "thinking").
        logger.warning(
            "empty reply on attempt %d/%d for user_id=%r, message=%r",
            attempt, MAX_ATTEMPTS, user_id, message,
        )

    reply = "".join(reply_parts).strip()
    if not reply:
        reply = (
            "Готово."
            if last_action is not None
            else "Не получилось получить ответ от модели. Попробуй повторить запрос."
        )
        yield {"type": "token", "text": reply}

    _save_message(user_id, conversation_id, "user", message, None)
    _save_message(user_id, conversation_id, "assistant", reply, last_action)
    conversations.touch_and_maybe_title(conversation_id, message)

    logger.info(
        "chat turn done: user_id=%s conversation_id=%s reply=%s last_action_tool=%s",
        user_id, conversation_id, _log_repr(reply), last_action["tool"] if last_action else None,
    )

    yield {"type": "done", "action": last_action}


def _parse_tool_result(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _save_message(
    user_id: str, conversation_id: str, role: str, content: str, action: dict | None
) -> None:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages "
            "(id, user_id, conversation_id, role, content, action_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                db.new_id(),
                user_id,
                conversation_id,
                role,
                content,
                json.dumps(action, ensure_ascii=False) if action else None,
                db.now_iso(),
            ),
        )


def extract_memory_background(user_id: str, message: str) -> None:
    logger.info("background memory extraction start: user_id=%s message=%s", user_id, _log_repr(message))
    memory_bridge.update_memory(message, user_id)
    logger.info("background memory extraction done: user_id=%s", user_id)
