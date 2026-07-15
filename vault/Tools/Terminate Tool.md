---
tags: [tool]
type: class
source_path: app/tool/terminate.py
---

# Terminate Tool

`Terminate` (defined in `app/tool/terminate.py`) is the primary execution control tool. It is the only structural path for the agent loop to transition into a completed state.

## Functionality
- **Parameters**:
  - `status` — Must be either `"success"` or `"failure"`.
  - `summary` — Detailed textual explanation of the final outcome, completed steps, and achievements.
  - `reason` — If status is failure, describes the blockages or system errors encountered.
- **Execution Outcomes**: Returns a formatted string confirming the final status. When received by `ToolCallAgent`, the agent catches the termination call, marks the task state as `FINISHED` or `ERROR`, and exits the step loop.
- **Safety Role**: Ensures the agent cannot get stuck in an observe loop indefinitely or exit without leaving an audit trail.

## Links
- [[Tools MOC]]
- [[Tool Call Agent]]
- [[Verification Gate]]
