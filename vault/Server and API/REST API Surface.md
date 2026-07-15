---
tags: [server, api]
type: module
source_path: server/api.py
---

# REST API Surface

The **REST API Surface** represents the central HTTP interaction interface for OpenManus, built with the FastAPI framework and defined in `server/api.py`.

## Core Responsibilities
- **Authentication & Registration**: Implements user creation, session token management, cookie validations, and admin role enforcement checks.
- **Conversations Management**: Endpoints to list, create, update, delete, and duplicate active conversation sessions.
- **Task Controls**: Triggers background Celery runs (`POST /api/conversations/{id}/tasks`), pauses runs, and retrieves current execution history.
- **Real-Time Streams**: Streams step-by-step agent lifecycle events to frontend clients using Server-Sent Events (SSE).
- **Obsidian Graph Integration**: Outputs node-edge JSON payloads (`GET /api/conversations/{id}/obsidian/graph`) to render the vault visualization map.

## Architectural Constraints
- **Monolithic Layout**: The module `server/api.py` is structured as a large monolith (exceeding 3,000 lines of code). It mixes user auth, session ORM queries, real-time message broadcasting, admin settings, model calibration logic, and static file endpoints inside a single script, creating maintainability risks.

## Links
- [[Server MOC]]
- [[Task Pipeline]]
- [[Obsidian Vault Sync]]
- [[REST API Monolith]]
