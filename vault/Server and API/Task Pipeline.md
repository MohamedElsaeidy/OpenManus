---
tags: [server, pipeline]
type: module
source_path: server/tasks.py
---

# Task Pipeline

The **Task Pipeline** (implemented in `server/tasks.py`) acts as the background execution orchestration layer for OpenManus. It coordinates task initialization, handles agent lifecycle loops, and publishes status streams.

## Flow Details
- **Orchestration**: Runs under Celery workers asynchronously to prevent blocking the web request server. It receives a `conversation_id`, retrieves the session DB models, configures workspaces, and instantiates the active agent.
- **Event Forwarding**: Intercepts step lifecycle events emitted from the agent loop (e.g. `step_start`, `agent_state`, `step_result`) and publishes them to Redis message channels. These events are subsequently streamed to clients via FastAPI Server-Sent Events (SSE).
- **Interruption Support**: Checks Redis status flags before executing each step. If a user triggers a cancel request via the UI, the pipeline raises `TaskInterrupted` to abort operations cleanly.
- **Hook Integration**: Automatically executes post-task hooks like `auto_sync_obsidian_notes` to refresh context maps upon completion.

## Links
- [[Server MOC]]
- [[REST API Surface]]
- [[Obsidian Vault Sync]]
- [[ReAct Loop]]
- [[Obsidian Edge Wipe on Auto-Sync]]
