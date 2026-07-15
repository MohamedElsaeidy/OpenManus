---
tags: [tool, planning]
type: class
source_path: app/tool/planning.py
---

# Planning Tool

`PlanningTool` (defined in `app/tool/planning.py`) is a stateful planning tool that manages task steps, completion status, and execution progress.

## Actions
- **Create**: Creates a new plan with a list of step titles and initial statuses.
- **Update**: Adjusts step descriptions while preserving the completion state of already finished steps.
- **Mark Step**: Updates the status of specific steps to completed or failed, reflecting progress in UI dashboards.

## Links
- [[Tools MOC]]
- [[Planner Agent]]
