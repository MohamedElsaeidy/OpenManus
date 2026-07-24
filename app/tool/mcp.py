from collections import defaultdict
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import ListToolsResult, TextContent

from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


@runtime_checkable
class MCPClientSession(Protocol):
    async def initialize(self) -> Any:
        ...

    async def list_tools(self) -> Any:
        ...

    async def call_tool(self, name: str, arguments: dict) -> Any:
        ...


class MCPClientTool(BaseTool):
    """Represents a tool proxy that can be called on the MCP server from the client side."""

    session: Optional[MCPClientSession] = None
    server_id: str = ""  # Add server identifier
    original_name: str = ""

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool by making a remote call to the MCP server."""
        if not self.session:
            return ToolResult(error="Not connected to MCP server")

        try:
            logger.info(f"Executing tool: {self.original_name}")
            result = await self.session.call_tool(self.original_name, kwargs)
            content_str = ", ".join(
                item.text for item in result.content if isinstance(item, TextContent)
            )
            return ToolResult(output=content_str or "No output returned.")
        except Exception as e:
            return ToolResult(error=f"Error executing tool: {str(e)}")


class MCPToolRoutingError(LookupError):
    """Raised when no unambiguous connected MCP server exposes a tool."""


class MCPClients(ToolCollection):
    """
    A collection of tools that connects to multiple MCP servers and manages available tools through the Model Context Protocol.
    """

    description: str = "MCP client tools for server interaction"

    def __init__(self):
        super().__init__()  # Initialize with empty tools list
        self.name = "mcp"  # Keep name for backward compatibility
        self.sessions: Dict[str, MCPClientSession] = {}
        self.exit_stacks: Dict[str, AsyncExitStack] = {}
        self.server_tools: Dict[str, Dict[str, str]] = {}
        self.tool_routes: Dict[str, str] = {}
        self._tool_definitions: Dict[str, list] = {}

    async def connect_sse(self, server_url: str, server_id: str = "") -> None:
        """Connect to an MCP server using SSE transport."""
        if not server_url:
            raise ValueError("Server URL is required.")

        server_id = server_id or server_url

        # Always ensure clean disconnection before new connection
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        self.exit_stacks[server_id] = exit_stack

        streams_context = sse_client(url=server_url)
        streams = await exit_stack.enter_async_context(streams_context)
        session = await exit_stack.enter_async_context(ClientSession(*streams))
        self.sessions[server_id] = session

        await self._initialize_and_list_tools(server_id)

    async def connect_stdio(
        self, command: str, args: List[str], server_id: str = ""
    ) -> None:
        """Connect to an MCP server using stdio transport."""
        if not command:
            raise ValueError("Server command is required.")

        server_id = server_id or command

        # Always ensure clean disconnection before new connection
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        self.exit_stacks[server_id] = exit_stack

        server_params = StdioServerParameters(command=command, args=args)
        stdio_transport = await exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read, write = stdio_transport
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        self.sessions[server_id] = session

        await self._initialize_and_list_tools(server_id)

    async def _initialize_and_list_tools(
        self, server_id: str, *, initialize: bool = True
    ) -> None:
        """Initialize session and populate tool map."""
        session = self.sessions.get(server_id)
        if not session:
            raise RuntimeError(f"Session not initialized for server {server_id}")

        if initialize:
            await session.initialize()
        response = await session.list_tools()

        self.tool_map = {
            name: tool
            for name, tool in self.tool_map.items()
            if not isinstance(tool, MCPClientTool) or tool.server_id != server_id
        }
        exposed_tools: Dict[str, str] = {}

        # Create proper tool objects for each server tool
        for tool in response.tools:
            original_name = tool.name
            tool_name = f"mcp_{server_id}_{original_name}"
            tool_name = self._sanitize_tool_name(tool_name)
            if tool_name in self.tool_map:
                raise MCPToolRoutingError(
                    f"MCP tool name collision for '{tool_name}' on server '{server_id}'"
                )

            server_tool = MCPClientTool(
                name=tool_name,
                description=tool.description,
                parameters=tool.inputSchema,
                session=session,
                server_id=server_id,
                original_name=original_name,
            )
            self.tool_map[tool_name] = server_tool
            exposed_tools[original_name] = tool_name

        # Update tools tuple
        self.server_tools[server_id] = exposed_tools
        self._tool_definitions[server_id] = list(response.tools)
        self._rebuild_routes()
        self.tools = tuple(self.tool_map.values())
        logger.info(
            f"Connected to server {server_id} with tools: {[tool.name for tool in response.tools]}"
        )

    def _sanitize_tool_name(self, name: str) -> str:
        """Sanitize tool name to match MCPClientTool requirements."""
        import re

        # Replace invalid characters with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

        # Remove consecutive underscores
        sanitized = re.sub(r"_+", "_", sanitized)

        # Remove leading/trailing underscores
        sanitized = sanitized.strip("_")

        # Truncate to 64 characters if needed
        if len(sanitized) > 64:
            sanitized = sanitized[:64]

        return sanitized

    def _rebuild_routes(self) -> None:
        routes: Dict[str, str] = {}
        original_routes: Dict[str, set[str]] = defaultdict(set)
        for server_id, tools in self.server_tools.items():
            for original_name, exposed_name in tools.items():
                routes[exposed_name] = server_id
                original_routes[original_name].add(server_id)
        for original_name, server_ids in original_routes.items():
            if len(server_ids) == 1:
                routes[original_name] = next(iter(server_ids))
        self.tool_routes = routes

    def route_tool_call(self, tool_name: str) -> str:
        server_id = self.tool_routes.get(tool_name)
        if server_id is not None and server_id in self.sessions:
            return server_id

        candidates = [
            server_id
            for server_id, tools in self.server_tools.items()
            if tool_name in tools and server_id in self.sessions
        ]
        if len(candidates) > 1:
            qualified = sorted(
                self.server_tools[server_id][tool_name] for server_id in candidates
            )
            raise MCPToolRoutingError(
                f"MCP tool '{tool_name}' is exposed by multiple servers; use one of: "
                + ", ".join(qualified)
            )
        available = sorted(self.tool_routes)
        suffix = f" Available tools: {', '.join(available)}" if available else ""
        raise MCPToolRoutingError(
            f"No connected MCP server exposes tool '{tool_name}'.{suffix}"
        )

    async def execute(
        self, *, name: str, tool_input: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        server_id = self.route_tool_call(name)
        exposed_name = name
        if exposed_name not in self.tool_map:
            exposed_name = self.server_tools[server_id].get(name, "")
        tool = self.tool_map.get(exposed_name)
        if not isinstance(tool, MCPClientTool):
            return ToolResult(error=f"MCP route for tool '{name}' is unavailable")
        return await tool.execute(**(tool_input or {}))

    async def refresh_tools(self) -> ListToolsResult:
        for server_id in list(self.sessions):
            await self._initialize_and_list_tools(server_id, initialize=False)
        return await self.list_tools()

    async def list_tools(self) -> ListToolsResult:
        """Return cached tool definitions collected when each server connected."""
        tools_result = ListToolsResult(tools=[])
        for server_id in self.sessions:
            tools_result.tools += self._tool_definitions.get(server_id, [])
        return tools_result

    def health(self) -> dict[str, dict]:
        return {
            server_id: {
                "connected": server_id in self.sessions,
                "tool_count": len(tools),
                "tools": sorted(tools),
            }
            for server_id, tools in self.server_tools.items()
        }

    async def disconnect(self, server_id: str = "") -> None:
        """Disconnect from a specific MCP server or all servers if no server_id provided."""
        if server_id:
            if server_id in self.sessions:
                try:
                    exit_stack = self.exit_stacks.get(server_id)

                    # Close the exit stack which will handle session cleanup
                    if exit_stack:
                        try:
                            await exit_stack.aclose()
                        except RuntimeError as e:
                            if "cancel scope" in str(e).lower():
                                logger.warning(
                                    f"Cancel scope error during disconnect from {server_id}, continuing with cleanup: {e}"
                                )
                            else:
                                raise

                    # Clean up references
                    self.sessions.pop(server_id, None)
                    self.exit_stacks.pop(server_id, None)
                    self.server_tools.pop(server_id, None)
                    self._tool_definitions.pop(server_id, None)

                    # Remove tools associated with this server
                    self.tool_map = {
                        k: v
                        for k, v in self.tool_map.items()
                        if v.server_id != server_id
                    }
                    self._rebuild_routes()
                    self.tools = tuple(self.tool_map.values())
                    logger.info(f"Disconnected from MCP server {server_id}")
                except Exception as e:
                    logger.error(f"Error disconnecting from server {server_id}: {e}")
        else:
            # Disconnect from all servers in a deterministic order
            for sid in sorted(list(self.sessions.keys())):
                await self.disconnect(sid)
            self.tool_map = {}
            self.server_tools = {}
            self.tool_routes = {}
            self._tool_definitions = {}
            self.tools = tuple()
            logger.info("Disconnected from all MCP servers")
