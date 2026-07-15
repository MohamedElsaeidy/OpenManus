---
tags: [agent-core, flow]
type: feature
source_path: app/exceptions.py
---

# Verification Gate

The **Verification Gate** represents the final validation phase in the agent's life cycle. Before the agent can transition its state to `FINISHED` (via the `terminate` tool), the system validates that all implementation goals are met and code changes pass checks.

## Key Concepts
- **Verification Exception**: If the validation steps or verification commands fail, the system raises a `VerificationFailed` exception (defined in `app/exceptions.py`).
- **Feedback Loop**: The `VerificationFailed` exception captures the detailed output of the failed checks (e.g. failing test suite results or compilation errors) and feeds it back into the agent's message history. This informs the model of the exact failure reasons, allowing the ReAct loop to iterate, debug, and self-correct on subsequent steps.
- **Verification Tooling**: The agent uses specialised verification tools such as `SkillPlaybook` (which provides test execution rules) and `CodebaseTool` (which identifies workspace test commands) to perform these validation runs.
- **Phase Transition**: Maps to `AgentPhase.VERIFY` within the core agent state machine, sitting between the `OBSERVE` and `DONE` phases.

## Links
- [[Agent Core MOC]]
- [[ReAct Loop]]
- [[Tool Call Agent]]
- [[Terminate Tool]]
