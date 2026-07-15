---
tags: [frontend, UI]
type: feature
source_path: frontend/src/components/features/chat
---

# Chat and Step Timeline

The **Chat and Step Timeline** is the core interactive conversation panel in the OpenManus frontend interface. It is located in `frontend/src/components/features/chat`.

## Component Responsibilities
- **Message Rendering**: Displays user queries, assistant statements, and tool execution logs in a chronological chat layout.
- **Trace Event Parsing**: Receives real-time Server-Sent Events (SSE) from the FastAPI backend and maps them to visual timelines:
  - Collapses raw LLM reasoning blocks behind interactive "Thinking" status banners.
  - Generates detailed action sub-items for active tool calls.
  - Prints stdout/stderr observations directly under command blocks.
- **Multimodal Display**: Renders page screenshot elements sent by the Browser Agent context helper.
- **Workflow Control**: Includes buttons to pause, cancel, or re-run active background worker execution flows.

## Links
- [[Frontend MOC]]
- [[ReAct Loop]]
- [[REST API Surface]]
