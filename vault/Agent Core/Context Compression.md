---
tags: [agent-core, context]
type: feature
source_path: app/agent/toolcall.py
---

# Context Compression

As conversations grow, message history can quickly approach the token window limit of the model. To prevent context window exhaustion and avoid raw truncation (which destroys critical history), `ToolCallAgent` implements a structured **Context Compression** routine.

## Implementation Details
1. **Trigger Threshold**: When the active prompt token size exceeds **90%** of the configured context window (checked via `_maybe_compress_context`), compression is triggered.
2. **Recent Window Preservation**: The agent keeps the last **24** messages (`keep_recent`) completely intact to ensure local conversational coherence.
3. **Structured Summarization**: Older messages are compressed into a structured summary that extracts:
   - Executed tool names and their outputs (truncating large success outputs but retaining full error details up to 400 characters).
   - Assistant reasoning snippets and key decisions.
4. **Pinned Artifacts**: Developers or tools can call `_pin_artifact()` to pin critical information (like git diffs, file structures, or test logs). Pinned items are stored in `pinned_context` and appended to the final summary, guaranteeing they survive compression.
5. **Event Emission**: Emits a `context_compressed` event to the task pipeline detailing the number of tokens before compression, the usage ratio, and the number of messages compressed.

## Links
- [[Agent Core MOC]]
- [[Tool Call Agent]]
- [[ReAct Loop]]
