---
tags: [tool, human]
type: class
source_path: app/tool/user_input_tool.py
---

# User Input Tool

`UserInputTool` (defined in `app/tool/user_input_tool.py`) is a tool that allows the agent to pause execution and prompt the user for free-form text input or task instruction updates.

## Functionality
- **Prompt Execution**: Pauses the background worker thread, publishes a prompt event, and waits for user input.
- **Input Gathering**: Captures inputs and returns them as a `ToolResult`, passing details back to the agent's message queue.

## Links
- [[Tools MOC]]
- [[Ask Human Tool]]
