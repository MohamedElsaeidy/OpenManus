---
tags: [known-issue, server]
type: known-issue
source_path: server/api.py
---

# REST API Monolith

The **REST API Monolith** refers to the file structure and architectural design of the application's backend web server.

## Description
- **The Issue**: Almost all backend endpoints are defined inside `server/api.py`, which has grown into a large single file (containing over 3,000 lines of code).
- **Consequences**:
  - Blends distinct backend responsibilities: user authentication, session cookies, task running wrappers, real-time message stream handlers, database models queries, system defaults settings, and static frontend file rendering are all contained in a single module.
  - Increases the risk of code conflicts, slows down testing, and makes codebase refactoring difficult.
- **Remediation**: Split the monolithic API file into modular routing folders (e.g. `routers/auth.py`, `routers/conversations.py`, and `routers/obsidian.py`) using FastAPI's APIRouter structure.

## Links
- [[Known Issues MOC]]
- [[REST API Surface]]
