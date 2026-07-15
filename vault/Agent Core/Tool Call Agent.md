---
tags: [agent-core, tool]
type: class
source_path: app/agent/toolcall.py
---

# Tool Call Agent

`ToolCallAgent` (defined in `app/agent/toolcall.py`) is the primary subclass of `ReActAgent` that implements tool-augmented execution. It interacts with the model via structured tool calls, manages context tokens, processes results, and oversees safe exits.

## Key Features
- **Think / Act Phases**:
  - `think(task)`: Queries the LLM with formatting prompts and user message history. Inspects model output for tool call invocations. If tool calls exist, it returns `True` to trigger the `act` phase.
  - `act(task)`: Dispatches the chosen tool calls concurrently or sequentially, gathers execution outputs into `ToolResult` messages, and formats them back into agent memory.
- **Strict Exit Checking**: The only valid way to enter a finished state is by invoking the `terminate` tool. Prose descriptions or raw finish flags are ignored, forcing the model to explicitly call `terminate` with a status (`success` or `failure`) and task summary.
- **Auto-Nudging**: If the model provides a message without choosing any tools, the agent injects a nudge instruction informing the model that it must act or terminate. If the model fails to use tools repeatedly, the agent auto-terminates with a failure status to prevent spinning.
- **Context Handling**: Calls context checking routines before every step to ensure the conversation does not exceed the LLM's context limits.

## Links
- [[Agent Core MOC]]
- [[ReAct Loop]]
- [[Context Compression]]
- [[Terminate Tool]]
