from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from . import mcp_tools, tools as tools_module, write_guard
from .chat_model import ChatQwen


def build_tool_list(user_id: str) -> list[BaseTool]:
    """All tools available to the agent for this user, as one flat list:
    the memory retriever, local action tools, and whatever MCP servers are
    configured (see mcp_tools.py — empty until mcp_servers.json exists).
    LangGraph's ToolNode and the chat model treat every entry identically,
    so plugging in more MCP tools later needs no changes here.

    write_guard.guard_write_tools() swaps every write-capable tool (local
    or MCP, see WRITE_TOOL_NAMES) for a confirmation-only stub and appends
    confirm_action — see write_guard.py for why."""
    memory_tool = tools_module.build_memory_tool(user_id)
    action_tools = tools_module.build_action_tools(user_id)
    all_tools = [memory_tool, *action_tools, *mcp_tools.get_cached_mcp_tools()]
    return write_guard.guard_write_tools(user_id, all_tools)


def build_graph(user_id: str, enable_thinking: bool = True) -> CompiledStateGraph:
    all_tools = build_tool_list(user_id)
    llm_with_tools = ChatQwen(enable_thinking=enable_thinking).bind_tools(all_tools)

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
    graph.add_node("tools", ToolNode(all_tools, handle_tool_errors=True))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
