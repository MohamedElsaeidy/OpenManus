---
tags: [tool, mcp]
type: class
source_path: app/tool/mcp.py
---

# MCP Tool

`MCPClientTool` (defined in `app/tool/mcp.py`) is the integration wrapper that exposes remote tools from Model Context Protocol (MCP) servers directly to the agent.

## Properties
- **Dynamic Definition**: Connects to stdio or SSE endpoints, reads server schemas, and exposes each remote function as a structured local tool.
- **Payload Translation**: Translates local agent inputs into valid MCP payloads, transmits them to the server, and parses outputs back into standard observations.

## Links
- [[Tools MOC]]
- [[Manus Agent]]
