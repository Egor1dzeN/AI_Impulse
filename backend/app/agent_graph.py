from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from . import mcp_tools, tools as tools_module
from .chat_gigachat import GIGACHAT_MODEL, ChatGigaChat, gigachat_is_configured
from .chat_model import ChatQwen
from .data_tools import build_data_tools
from .masking import mask_obj, mask_sensitive

logger = logging.getLogger(__name__)


def build_tool_list(user_id: str) -> list[BaseTool]:
    """All tools available to the agent for this user, as one flat list:
    the memory retriever, local action tools, and whatever MCP servers are
    configured (see mcp_tools.py — empty until mcp_servers.json exists).
    LangGraph's ToolNode and the chat model treat every entry identically,
    so plugging in more MCP tools later needs no changes here."""
    memory_tools = tools_module.build_memory_tools(user_id)
    data_tools = build_data_tools(user_id)
    action_tools = tools_module.build_action_tools(user_id)
    return [*memory_tools, *data_tools, *action_tools, *mcp_tools.get_cached_mcp_tools()]


def _build_llm(enable_thinking: bool):
    # GigaChat is the intended backend going forward; ChatQwen (local
    # Qwen3-4B) stays available as a fallback for offline dev whenever
    # GIGA_CHAT_IDE isn't set, without needing a separate code path — both
    # expose the same bind_tools()/invoke() surface to the graph below.
    if gigachat_is_configured():
        logger.info("llm backend: GigaChat (model=%s)", GIGACHAT_MODEL)
        return ChatGigaChat(enable_thinking=enable_thinking)
    logger.warning("llm backend: GIGA_CHAT_IDE not set, falling back to local Qwen3-4B")
    return ChatQwen(enable_thinking=enable_thinking)


# Tools that write straight into the user's records (no confirmation card).
_DIRECT_WRITE_TOOLS = {"log_meal", "log_weight", "add_expense", "add_income", "create_task", "update_transaction"}
_EXPLICIT_WRITE_RE = re.compile(
    r"(?i)\b(запиш|записа|добав|внес|внёс|зафиксир|отметь|отметить|сохрани|сохранить|создай|создать|заведи|залогир|"
    r"учти|поправ|исправ|измени|удали|поставь задач|log|add|record|save)\w*"
)


def _block_unrequested_writes(state: MessagesState) -> tuple[list[ToolMessage], MessagesState]:
    """"Я съела пасту на 650 ккал, сколько осталось?" is a question, not an
    order to log a meal — yet the model logged it, and the second session
    then subtracted the same 650 twice. A direct-write tool runs only when
    the user's current message actually asks to record something."""
    last = state["messages"][-1]
    if not isinstance(last, AIMessage) or not last.tool_calls:
        return [], state
    human = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
    text = (human.content if human and isinstance(human.content, str) else "").split("\n\nНе забудь посмотреть в память")[0]
    if _EXPLICIT_WRITE_RE.search(text):
        return [], state
    blocked = [tc for tc in last.tool_calls if tc["name"] in _DIRECT_WRITE_TOOLS]
    if not blocked:
        return [], state
    notes = [
        ToolMessage(
            content="НЕ ВЫПОЛНЕНО: пользователь не просил ничего записывать — это был вопрос. Ничего не записано; "
                    "ответь на вопрос без записи (можешь предложить записать, если он захочет).",
            tool_call_id=tc["id"], name=tc["name"],
        )
        for tc in blocked
    ]
    allowed = [tc for tc in last.tool_calls if tc["name"] not in _DIRECT_WRITE_TOOLS]
    trimmed = last.model_copy(update={"tool_calls": allowed})
    return notes, {**state, "messages": [*state["messages"][:-1], trimmed]}


def build_graph(user_id: str, enable_thinking: bool = True) -> CompiledStateGraph:
    all_tools = build_tool_list(user_id)
    llm_with_tools = _build_llm(enable_thinking).bind_tools(all_tools)

    def agent_node(state: MessagesState) -> dict:
        result = llm_with_tools.invoke(state["messages"])
        return {"messages": [result]}

    def route(state: MessagesState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    # handle_tool_errors=True: this build's langgraph only auto-recovers
    # from its own internal ToolInvocationError by default — any other
    # exception a tool raises (a bad id producing an HTTPError, a timeout,
    # ...) is re-raised and crashes the whole graph run, killing the SSE
    # stream mid-turn instead of just failing that one tool call. This
    # turns any such exception into a ToolMessage the model can see and
    # react to (retry, apologize, ask again) so one flaky/misused tool
    # call can't take down the entire turn.
    tool_node = ToolNode(all_tools, handle_tool_errors=True)

    # Every tool result passes through masking before it can reach the
    # model's prompt or the UI — no tool has to remember to do it itself.
    def masked_tools_node(state: MessagesState, config) -> dict:
        blocked, state = _block_unrequested_writes(state)
        result = tool_node.invoke(state, config) if state["messages"][-1].tool_calls else {"messages": []}
        result = {**result, "messages": [*blocked, *result.get("messages", [])]}
        for msg in result.get("messages", []):
            if isinstance(msg.content, str):
                msg.content = mask_sensitive(msg.content)
            if getattr(msg, "artifact", None) is not None:
                msg.artifact = mask_obj(msg.artifact)
        return result

    graph.add_node("tools", masked_tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
