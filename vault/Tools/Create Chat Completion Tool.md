---
tags: [tool, llm]
type: class
source_path: app/tool/create_chat_completion.py
---

# Create Chat Completion Tool

`CreateChatCompletion` (defined in `app/tool/create_chat_completion.py`) allows agents to spawn secondary LLM requests directly.

## Usage
- **Sub-task Execution**: Spawns isolated completions for tasks like formatting data, summarizing search results, or checking implementation logic.
- **Context Separation**: Runs outside the main agent conversation memory to keep active history clean.

## Links
- [[Tools MOC]]
- [[Tool Call Agent]]
