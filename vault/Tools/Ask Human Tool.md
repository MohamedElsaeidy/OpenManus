---
tags: [tool, human]
type: class
source_path: app/tool/ask_human.py
---

# Ask Human Tool

`AskHuman` (defined in `app/tool/ask_human.py`) is a tool that allows the agent to request clarifications, input tokens, or validation decisions from the user.

## Operations
- **Interactive Prompts**: Pauses agent execution and prompts the user with questions or confirmation choices via the terminal or UI.
- **Feedback Collection**: Captures the user's responses and passes them back into the reasoning loop, allowing the model to correct path drift.

## Links
- [[Tools MOC]]
- [[Tool Call Agent]]
