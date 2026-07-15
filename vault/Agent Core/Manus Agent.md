---
tags: [agent-core, manus]
type: class
source_path: app/agent/manus.py
---

# Manus Agent

`Manus` (defined in `app/agent/manus.py`) is the primary general-purpose orchestrator agent in the OpenManus framework. It connects local operating system tools with remote Model Context Protocol (MCP) clients to resolve complex multi-step tasks.

## Tool Assembly
The agent comes equipped with 17 built-in tools:
1. `SkillPlaybook` — Reusable workflow scripts.
2. `PlanningTool` — Structured execution plans.
3. `CodebaseOverview` — Workspace structure mapping.
4. `GlobSearch` — File path filtering.
5. `GrepSearch` — Ripgrep pattern matching.
6. `ReadFiles` — Batch file reading.
7. `PythonExecute` — Sandbox Python execution.
8. `Bash` — Local shell commands.
9. `BrowserUseTool` — Web page automation.
10. `WebSearch` — Google/DuckDuckGo searches.
11. `LineEdit` — Primary line-number editor.
12. `ApplyPatchEditor` — Multi-file patches.
13. `MemorySave` / `MemoryRecall` — Persistent memory.
14. `AskHuman` / `WaitForUserInput` — User interaction.
15. `Terminate` — Execution finalizer.

## Key Mechanisms
- **MCP Integration**: Uses `MCPClients` to dynamically connect to Stdio and SSE servers. Discovered MCP server capabilities are automatically loaded into the agent's `available_tools` collection.
- **Multimodal State Handling**: Integrates with `BrowserContextHelper` to capture browser screenshots as base64 images and inject them into message history when web automation tools are active.
- **RL Policy Integration**: Dynamically loads reinforcement learning policy prompts to bias the model's tool selection strategy.

## Links
- [[Agent Core MOC]]
- [[Tool Call Agent]]
- [[Browser Use Tool]]
- [[Terminate Tool]]
