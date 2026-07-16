import json
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import Field, PrivateAttr

from app.exceptions import ToolError
from app.task_context import emit_current_task, get_current_workspace
from app.tool.base import BaseTool, ToolResult


_PLANNING_TOOL_DESCRIPTION = """
A planning tool that allows the agent to create and manage plans for solving complex tasks.
The tool provides functionality for creating plans, updating plan steps, and tracking progress.
Use command=mark_step with step_index and step_status to change progress. The update
command only changes the plan title or replaces its steps.
"""


class PlanningTool(BaseTool):
    """
    A planning tool that allows the agent to create and manage plans for solving complex tasks.
    The tool provides functionality for creating plans, updating plan steps, and tracking progress.
    """

    name: str = "planning"
    description: str = _PLANNING_TOOL_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "The command to execute. Available commands: create, update, list, get, set_active, mark_step, delete.",
                "enum": [
                    "create",
                    "update",
                    "list",
                    "get",
                    "set_active",
                    "mark_step",
                    "delete",
                ],
                "type": "string",
            },
            "plan_id": {
                "description": "Unique identifier for the plan. Required for create, update, set_active, and delete commands. Optional for get and mark_step (uses active plan if not specified).",
                "type": "string",
            },
            "title": {
                "description": "Title for the plan. Required for create command, optional for update command.",
                "type": "string",
            },
            "steps": {
                "description": "List of plan steps. Required for create command, optional for update command.",
                "type": "array",
                "items": {"type": "string"},
            },
            "step_index": {
                "description": "Index of the step to update (0-based). Required for mark_step command.",
                "type": "integer",
            },
            "step_status": {
                "description": "Status to set for a step. Used with mark_step command.",
                "enum": ["not_started", "in_progress", "completed", "blocked"],
                "type": "string",
            },
            "step_notes": {
                "description": "Additional notes for a step. Optional for mark_step command.",
                "type": "string",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    plans: dict = Field(default_factory=dict)
    _current_plan_id: Optional[str] = PrivateAttr(default=None)
    _loaded_workspace: Optional[str] = PrivateAttr(default=None)

    def _state_path(self) -> Optional[Path]:
        workspace = get_current_workspace()
        if not workspace:
            return None
        return Path(workspace).resolve() / ".openmanus" / "plans.json"

    def _load_persisted_state(self) -> None:
        state_path = self._state_path()
        workspace_key = str(state_path.parent.parent) if state_path else None
        if workspace_key == self._loaded_workspace:
            return

        self.plans = {}
        self._current_plan_id = None
        self._loaded_workspace = workspace_key
        if state_path is None or not state_path.exists():
            return

        try:
            state = json.loads(state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolError(f"Unable to load persisted planning state: {exc}") from exc

        plans = state.get("plans", {}) if isinstance(state, dict) else {}
        if not isinstance(plans, dict):
            raise ToolError("Persisted planning state has an invalid plans object")
        self.plans = plans
        active = state.get("active_plan_id") if isinstance(state, dict) else None
        self._current_plan_id = active if active in self.plans else None

    def _persist_state(self) -> None:
        state_path = self._state_path()
        if state_path is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = state_path.with_suffix(".tmp")
        payload = {
            "version": 1,
            "active_plan_id": self._current_plan_id,
            "plans": self.plans,
        }
        try:
            temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            temporary_path.replace(state_path)
        except OSError as exc:
            raise ToolError(f"Unable to persist planning state: {exc}") from exc

    def active_plan_context(self) -> str:
        """Return a compact checkpoint for injection into a new agent instance."""
        self._load_persisted_state()
        if not self._current_plan_id or self._current_plan_id not in self.plans:
            return ""
        plan = self.plans[self._current_plan_id]
        statuses = plan.get("step_statuses", [])
        steps = plan.get("steps", [])
        next_index = next(
            (
                index
                for index, status in enumerate(statuses)
                if status not in {"completed", "blocked"}
            ),
            None,
        )
        next_step = (
            steps[next_index] if next_index is not None else "All steps terminal"
        )
        return (
            f"Persisted active plan: {plan.get('title', self._current_plan_id)} "
            f"(id={self._current_plan_id}). Progress: "
            f"{sum(status == 'completed' for status in statuses)}/{len(steps)}. "
            f"Next sequential step: {next_index}: {next_step}. Call planning get before "
            "continuing, execute only this earliest unfinished step, verify it, then "
            "mark it completed before advancing."
        )

    async def execute(
        self,
        *,
        command: Literal[
            "create", "update", "list", "get", "set_active", "mark_step", "delete"
        ],
        plan_id: Optional[str] = None,
        title: Optional[str] = None,
        steps: Optional[List[str]] = None,
        step_index: Optional[int] = None,
        step_status: Optional[
            Literal["not_started", "in_progress", "completed", "blocked"]
        ] = None,
        step_notes: Optional[str] = None,
        **kwargs,
    ):
        """
        Execute the planning tool with the given command and parameters.

        Parameters:
        - command: The operation to perform
        - plan_id: Unique identifier for the plan
        - title: Title for the plan (used with create command)
        - steps: List of steps for the plan (used with create command)
        - step_index: Index of the step to update (used with mark_step command)
        - step_status: Status to set for a step (used with mark_step command)
        - step_notes: Additional notes for a step (used with mark_step command)
        """

        self._load_persisted_state()
        if command == "create":
            result = self._create_plan(plan_id, title, steps)
        elif command == "update":
            if (
                step_index is not None
                or step_status is not None
                or step_notes is not None
            ):
                raise ToolError(
                    "command='update' cannot change step progress. Use "
                    "command='mark_step' with step_index and step_status."
                )
            result = self._update_plan(plan_id, title, steps)
        elif command == "list":
            result = self._list_plans()
        elif command == "get":
            result = self._get_plan(plan_id)
        elif command == "set_active":
            result = self._set_active_plan(plan_id)
        elif command == "mark_step":
            result = self._mark_step(plan_id, step_index, step_status, step_notes)
        elif command == "delete":
            result = self._delete_plan(plan_id)
        else:
            raise ToolError(
                f"Unrecognized command: {command}. Allowed commands are: create, update, list, get, set_active, mark_step, delete"
            )
        if command in {"create", "update", "set_active", "mark_step", "delete"}:
            self._persist_state()
        return result

    def _create_plan(
        self, plan_id: Optional[str], title: Optional[str], steps: Optional[List[str]]
    ) -> ToolResult:
        """Create a new plan with the given ID, title, and steps."""
        if not plan_id:
            raise ToolError("Parameter `plan_id` is required for command: create")

        if plan_id in self.plans:
            raise ToolError(
                f"A plan with ID '{plan_id}' already exists. Use 'update' to modify existing plans."
            )

        if not title:
            raise ToolError("Parameter `title` is required for command: create")

        if (
            not steps
            or not isinstance(steps, list)
            or not all(isinstance(step, str) for step in steps)
        ):
            raise ToolError(
                "Parameter `steps` must be a non-empty list of strings for command: create"
            )

        # Create a new plan with initialized step statuses
        plan = {
            "plan_id": plan_id,
            "title": title,
            "steps": steps,
            "step_statuses": ["not_started"] * len(steps),
            "step_notes": [""] * len(steps),
        }

        self.plans[plan_id] = plan
        self._current_plan_id = plan_id  # Set as active plan

        result = ToolResult(
            output=f"Plan created successfully with ID: {plan_id}\n\n{self._format_plan(plan)}"
        )
        self._emit_plan_event(plan, command="create")
        return result

    def _update_plan(
        self, plan_id: Optional[str], title: Optional[str], steps: Optional[List[str]]
    ) -> ToolResult:
        """Update an existing plan with new title or steps."""
        if not plan_id:
            raise ToolError("Parameter `plan_id` is required for command: update")

        if plan_id not in self.plans:
            raise ToolError(f"No plan found with ID: {plan_id}")

        if title is None and steps is None:
            raise ToolError(
                "command='update' requires title or steps. Use command='mark_step' "
                "to change a step's status."
            )

        plan = self.plans[plan_id]

        if title:
            plan["title"] = title

        if steps:
            if not isinstance(steps, list) or not all(
                isinstance(step, str) for step in steps
            ):
                raise ToolError(
                    "Parameter `steps` must be a list of strings for command: update"
                )

            # Preserve existing step statuses for unchanged steps
            old_steps = plan["steps"]
            old_statuses = plan["step_statuses"]
            old_notes = plan["step_notes"]

            # Create new step statuses and notes
            new_statuses = []
            new_notes = []

            for i, step in enumerate(steps):
                # If the step exists at the same position in old steps, preserve status and notes
                if i < len(old_steps) and step == old_steps[i]:
                    new_statuses.append(old_statuses[i])
                    new_notes.append(old_notes[i])
                else:
                    new_statuses.append("not_started")
                    new_notes.append("")

            plan["steps"] = steps
            plan["step_statuses"] = new_statuses
            plan["step_notes"] = new_notes

        result = ToolResult(
            output=f"Plan updated successfully: {plan_id}\n\n{self._format_plan(plan)}"
        )
        self._emit_plan_event(plan, command="update")
        return result

    def _list_plans(self) -> ToolResult:
        """List all available plans."""
        if not self.plans:
            return ToolResult(
                output="No plans available. Create a plan with the 'create' command."
            )

        output = "Available plans:\n"
        for plan_id, plan in self.plans.items():
            current_marker = " (active)" if plan_id == self._current_plan_id else ""
            completed = sum(
                1 for status in plan["step_statuses"] if status == "completed"
            )
            total = len(plan["steps"])
            progress = f"{completed}/{total} steps completed"
            output += f"• {plan_id}{current_marker}: {plan['title']} - {progress}\n"

        return ToolResult(output=output)

    def _get_plan(self, plan_id: Optional[str]) -> ToolResult:
        """Get details of a specific plan."""
        if not plan_id:
            # If no plan_id is provided, use the current active plan
            if not self._current_plan_id:
                raise ToolError(
                    "No active plan. Please specify a plan_id or set an active plan."
                )
            plan_id = self._current_plan_id

        if plan_id not in self.plans:
            raise ToolError(f"No plan found with ID: {plan_id}")

        plan = self.plans[plan_id]
        return ToolResult(output=self._format_plan(plan))

    def _set_active_plan(self, plan_id: Optional[str]) -> ToolResult:
        """Set a plan as the active plan."""
        if not plan_id:
            raise ToolError("Parameter `plan_id` is required for command: set_active")

        if plan_id not in self.plans:
            raise ToolError(f"No plan found with ID: {plan_id}")

        self._current_plan_id = plan_id
        return ToolResult(
            output=f"Plan '{plan_id}' is now the active plan.\n\n{self._format_plan(self.plans[plan_id])}"
        )

    def _mark_step(
        self,
        plan_id: Optional[str],
        step_index: Optional[int],
        step_status: Optional[str],
        step_notes: Optional[str],
    ) -> ToolResult:
        """Mark a step with a specific status and optional notes."""
        if not plan_id:
            # If no plan_id is provided, use the current active plan
            if not self._current_plan_id:
                raise ToolError(
                    "No active plan. Please specify a plan_id or set an active plan."
                )
            plan_id = self._current_plan_id

        if plan_id not in self.plans:
            raise ToolError(f"No plan found with ID: {plan_id}")

        if step_index is None:
            raise ToolError("Parameter `step_index` is required for command: mark_step")

        plan = self.plans[plan_id]

        if step_index < 0 or step_index >= len(plan["steps"]):
            raise ToolError(
                f"Invalid step_index: {step_index}. Valid indices range from 0 to {len(plan['steps'])-1}."
            )

        if step_status in {"in_progress", "completed", "blocked"}:
            unfinished_prior = [
                index
                for index, status in enumerate(plan["step_statuses"][:step_index])
                if status not in {"completed", "blocked"}
            ]
            if unfinished_prior:
                raise ToolError(
                    f"Plan steps execute sequentially. Finish or block step "
                    f"{unfinished_prior[0]} before updating step {step_index}."
                )
            other_active = next(
                (
                    index
                    for index, status in enumerate(plan["step_statuses"])
                    if status == "in_progress" and index != step_index
                ),
                None,
            )
            if other_active is not None:
                raise ToolError(
                    f"Step {other_active} is already in progress. Complete or block it "
                    f"before starting step {step_index}."
                )

        if step_status and step_status not in [
            "not_started",
            "in_progress",
            "completed",
            "blocked",
        ]:
            raise ToolError(
                f"Invalid step_status: {step_status}. Valid statuses are: not_started, in_progress, completed, blocked"
            )

        if step_status:
            plan["step_statuses"][step_index] = step_status

        if step_notes:
            plan["step_notes"][step_index] = step_notes

        result = ToolResult(
            output=f"Step {step_index} updated in plan '{plan_id}'.\n\n{self._format_plan(plan)}"
        )
        self._emit_plan_event(plan, command="mark_step", step_index=step_index)
        return result

    def _delete_plan(self, plan_id: Optional[str]) -> ToolResult:
        """Delete a plan."""
        if not plan_id:
            raise ToolError("Parameter `plan_id` is required for command: delete")

        if plan_id not in self.plans:
            raise ToolError(f"No plan found with ID: {plan_id}")

        del self.plans[plan_id]

        # If the deleted plan was the active plan, clear the active plan
        if self._current_plan_id == plan_id:
            self._current_plan_id = None

        result = ToolResult(output=f"Plan '{plan_id}' has been deleted.")
        emit_current_task(
            "agent:plan:updated",
            {"command": "delete", "plan_id": plan_id, "deleted": True},
        )
        return result

    def _format_plan(self, plan: Dict) -> str:
        """Format a plan for display."""
        output = f"Plan: {plan['title']} (ID: {plan['plan_id']})\n"
        output += "=" * len(output) + "\n\n"

        # Calculate progress statistics
        total_steps = len(plan["steps"])
        completed = sum(1 for status in plan["step_statuses"] if status == "completed")
        in_progress = sum(
            1 for status in plan["step_statuses"] if status == "in_progress"
        )
        blocked = sum(1 for status in plan["step_statuses"] if status == "blocked")
        not_started = sum(
            1 for status in plan["step_statuses"] if status == "not_started"
        )

        output += f"Progress: {completed}/{total_steps} steps completed "
        if total_steps > 0:
            percentage = (completed / total_steps) * 100
            output += f"({percentage:.1f}%)\n"
        else:
            output += "(0%)\n"

        output += f"Status: {completed} completed, {in_progress} in progress, {blocked} blocked, {not_started} not started\n\n"
        output += "Steps:\n"

        # Add each step with its status and notes
        for i, (step, status, notes) in enumerate(
            zip(plan["steps"], plan["step_statuses"], plan["step_notes"])
        ):
            status_symbol = {
                "not_started": "[ ]",
                "in_progress": "[→]",
                "completed": "[✓]",
                "blocked": "[!]",
            }.get(status, "[ ]")

            output += f"{i}. {status_symbol} {step}\n"
            if notes:
                output += f"   Notes: {notes}\n"

        return output

    def _emit_plan_event(
        self, plan: Dict, command: str, step_index: Optional[int] = None
    ) -> None:
        """Emit a structured plan state event for the UI to render."""
        total = len(plan["steps"])
        completed = sum(1 for s in plan["step_statuses"] if s == "completed")
        emit_current_task(
            "agent:plan:updated",
            {
                "command": command,
                "plan_id": plan["plan_id"],
                "title": plan["title"],
                "steps": [
                    {
                        "index": i,
                        "text": step,
                        "status": plan["step_statuses"][i],
                        "notes": plan["step_notes"][i],
                        "active": step_index == i,
                    }
                    for i, step in enumerate(plan["steps"])
                ],
                "progress": {
                    "completed": completed,
                    "total": total,
                    "pct": round(completed / total * 100, 1) if total else 0,
                },
            },
        )
