---
tags: [agent-core, planner]
type: class
source_path: app/agent/planner.py
---

# Planner Agent

`PlannerAgent` (defined in `app/agent/planner.py`) is a lightweight planning agent designed to map out tasks without executing tools. It reads task requests and outputs structured steps as a JSON array.

## Core Characteristics
- **Zero-Tool Model**: Unlike `ToolCallAgent`, the Planner Agent does not execute tools. It communicates directly with the LLM to get plan structures.
- **Structured Schema**: Normalizes output steps into a defined dictionary structure:
  - `id` — Step identifier (e.g. `step-1`).
  - `title` — Short label describing the step.
  - `action` — Concrete implementation instruction.
  - `expected_result` — Post-conditions for validation.
- **Workflow Phase**: Used in the early stages of a task execution flow to create a plan that is subsequently stored in the database.

## Links
- [[Agent Core MOC]]
- [[Tool Call Agent]]
- [[ReAct Loop]]
