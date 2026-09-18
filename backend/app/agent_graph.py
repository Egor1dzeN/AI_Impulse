from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from . import mcp_tools, tools as tools_module
from .chat_model import ChatQwen


def build_tool_list(user_id: str) -> list[BaseTool]:
    """All tools available to the agent for this user, as one flat list:
    the memory retriever, local action tools, and whatever MCP servers are
    configured (see mcp_tools.py — empty until mcp_servers.json exists).
    LangGraph's ToolNode and the chat model treat every entry identically,
    so plugging in more MCP tools later needs no changes here."""
    memory_tool = tools_module.build_memory_tool(user_id)
    action_tools = tools_module.build_action_tools(user_id)
    return [memory_tool, *action_tools, *mcp_tools.get_cached_mcp_tools()]


def build_graph(user_id: str) -> CompiledStateGraph:
    all_tools = build_tool_list(user_id)
    llm_with_tools = ChatQwen().bind_tools(all_tools)

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
    graph.add_node("tools", ToolNode(all_tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
