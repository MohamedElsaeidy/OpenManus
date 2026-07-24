from types import SimpleNamespace

import pytest

from app.tool.mcp import MCPClients, MCPToolRoutingError


class FakeSession:
    def __init__(self, *tool_names: str):
        self.tool_names = tool_names
        self.list_calls = 0

    async def initialize(self):
        return None

    async def list_tools(self):
        self.list_calls += 1
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=name,
                    description=f"{name} tool",
                    inputSchema={"type": "object", "properties": {}},
                )
                for name in self.tool_names
            ]
        )

    async def call_tool(self, name: str, arguments: dict):
        return SimpleNamespace(content=[])


@pytest.mark.asyncio
async def test_route_tool_call_selects_exposing_server_and_uses_cache():
    clients = MCPClients()
    clients.sessions = {
        "files": FakeSession("read_file"),
        "search": FakeSession("web_search"),
    }

    await clients._initialize_and_list_tools("files")
    await clients._initialize_and_list_tools("search")

    assert clients.route_tool_call("read_file") == "files"
    assert clients.route_tool_call("web_search") == "search"
    assert clients.route_tool_call("mcp_files_read_file") == "files"

    result = await clients.execute(name="read_file", tool_input={"path": "note.md"})
    assert result.error is None

    await clients.list_tools()
    assert clients.sessions["files"].list_calls == 1
    assert clients.sessions["search"].list_calls == 1


@pytest.mark.asyncio
async def test_route_tool_call_rejects_unknown_tool():
    clients = MCPClients()
    clients.sessions = {"files": FakeSession("read_file")}
    await clients._initialize_and_list_tools("files")

    with pytest.raises(
        MCPToolRoutingError,
        match="No connected MCP server exposes tool 'missing_tool'",
    ):
        clients.route_tool_call("missing_tool")
