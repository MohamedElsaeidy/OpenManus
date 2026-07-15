---
tags: [tool, terminal]
type: class
source_path: app/tool/bash.py
---

# Bash Tool

`Bash` (defined in `app/tool/bash.py`) is the primary terminal command execution tool. It allows agents to run terminal commands, execute compilers, launch test suites, and manage workspace processes.

## Features
- **Sandbox Execution**: Commands are routed and executed inside isolated container sandboxes to protect the host system.
- **State Preservation**: Reuses persistent terminals to preserve environment variables, paths, and directory positions between steps.
- **Process Management**: Captures exit codes, stdout, and stderr. Supports long-running background processes (like dev servers) with explicit tracking and manual termination.

## Links
- [[Tools MOC]]
- [[Python Execute Tool]]
- [[Docker Compose Stack]]
