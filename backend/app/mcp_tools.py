from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

CONFIG_PATH = Path(__file__).resolve().parent.parent / "mcp_servers.json"

# Populated once at startup by refresh_mcp_tools() and merged into every
# request's tool list in agent_graph.build_tool_list(). Empty by default —
# the agent works exactly as it does today until mcp_servers.json lists at
# least one server, so adding MCP support later is just dropping a config
# file in, no code changes required.
_cached_tools: list[BaseTool] = []


def _load_server_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


async def refresh_mcp_tools() -> list[BaseTool]:
    """Connect to every MCP server in mcp_servers.json and cache their
    tools. Call once at app startup (see main.py). A missing/empty config
    is a no-op, not an error."""
    global _cached_tools

    servers = _load_server_config()
    if not servers:
        _cached_tools = []
        return _cached_tools

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(servers)
    _cached_tools = await client.get_tools()
    return _cached_tools


def get_cached_mcp_tools() -> list[BaseTool]:
    return list(_cached_tools)
