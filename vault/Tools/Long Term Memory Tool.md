---
tags: [tool, memory]
type: class
source_path: app/tool/long_term_memory.py
---

# Long Term Memory Tool

`LongTermMemory` (defined in `app/tool/long_term_memory.py`) provides the agent with capability to save and retrieve experiences across conversation sessions.

## Operations
- **Save Experience**: Automatically compiles successful workspace execution traces and plans, indexing them in the vector database.
- **Recall Experience**: Queries matching historical runs to extract relevant solutions, reducing duplication of effort on similar tasks.

## Links
- [[Tools MOC]]
- [[Tool Call Agent]]
