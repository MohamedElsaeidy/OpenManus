---
tags: [agent-core, swe]
type: class
source_path: app/agent/swe.py
---

# SWE Agent

`SWEAgent` (defined in `app/agent/swe.py`) is a specialized agent persona adapted for software engineering tasks inside a codebase. It inherits from `ToolCallAgent` and restricts its capabilities to tools focused on program modification and command execution.

## Tool Limitations
To minimize execution drift and prioritize coding speed, `SWEAgent` only receives four core tools:
1. `Bash` — For running test suites, compilation commands, and search scripts.
2. `LineEdit` — For line-by-line file edits.
3. `ApplyPatchEditor` — For writing multi-file unified diff blocks.
4. `Terminate` — For exiting once tests pass.

## Links
- [[Agent Core MOC]]
- [[Tool Call Agent]]
- [[Bash Tool]]
- [[Line Edit Tool]]
- [[Apply Patch Editor]]
- [[Terminate Tool]]
