import math
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.execution_policy import ExecutionPolicy
from app.config import config
from app.llm import LLM
from app.sandbox.client import SANDBOX_CLIENT
from app.schema import ROLE_TYPE, AgentState, Memory, Message
from app.task_context import get_current_token_usage
from core.task import Task


class TaskInterrupted(Exception):
    """Raised when a task is interrupted."""


class BaseAgent(BaseModel, ABC):
    """Abstract base class for managing agent state and execution.

    Provides foundational functionality for state transitions, memory management,
    and a budgeted execution loop. Subclasses must implement the `step` method.
    """

    # Core attributes
    name: str = Field(..., description="Unique name of the agent")
    description: Optional[str] = Field(None, description="Optional agent description")

    # Prompts
    system_prompt: Optional[str] = Field(
        None, description="System-level instruction prompt"
    )
    next_step_prompt: Optional[str] = Field(
        None, description="Prompt for determining next action"
    )

    # Dependencies
    llm: LLM = Field(default_factory=LLM, description="Language model instance")
    memory: Memory = Field(default_factory=Memory, description="Agent's memory store")
    state: AgentState = Field(
        default=AgentState.IDLE, description="Current agent state"
    )

    # Execution control
    max_steps: int = Field(
        default=config.agent.max_steps,
        description="Compatibility alias for steps in one resumable execution slice",
    )
    current_step: int = Field(default=0, description="Current step in execution")
    total_steps: int = Field(default=0, description="Steps used across all slices")
    current_slice: int = Field(default=1, description="Current execution slice")
    tool_calls_used: int = Field(default=0, description="Tool calls used in this run")
    no_progress_cycles: int = Field(
        default=0, description="Consecutive repeated-action detections"
    )
    execution_policy: ExecutionPolicy = Field(
        default_factory=lambda: ExecutionPolicy.for_mode(config.agent.execution_mode)
    )

    final_response: Optional[str] = Field(
        default=None,
        description="User-facing response produced when the current run finishes.",
        exclude=True,
    )
    final_status: Optional[str] = Field(
        default=None,
        description="Structured outcome for the current run.",
        exclude=True,
    )
    final_reason: Optional[str] = Field(
        default=None,
        description="Optional blocker or termination reason for the current run.",
        exclude=True,
    )

    duplicate_threshold: int = 2

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        """Initialize agent with default settings if not provided."""
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """Context manager for safe agent state transitions.

        Args:
            new_state: The state to transition to during the context.

        Yields:
            None: Allows execution within the new state.

        Raises:
            ValueError: If the new_state is invalid.
        """
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid state: {new_state}")

        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR  # Transition to ERROR on failure
            raise e
        finally:
            self.state = previous_state  # Revert to previous state

    def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Add a message to the agent's memory.

        Args:
            role: The role of the message sender (user, system, assistant, tool).
            content: The message content.
            base64_image: Optional base64 encoded image.
            **kwargs: Additional arguments (e.g., tool_call_id for tool messages).

        Raises:
            ValueError: If the role is unsupported.
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")

        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    async def run(self, task: Task, input: Any) -> str:
        """Execute until semantic completion or a layered circuit breaker fires."""
        if task.is_interrupted():
            raise TaskInterrupted()

        if self.state != AgentState.IDLE:
            raise RuntimeError(f"Cannot run agent from state: {self.state}")

        if input is not None:
            self.update_memory("user", str(input))

        self.final_response = None
        self.final_status = None
        self.final_reason = None
        self.current_step = 0
        self.total_steps = 0
        self.current_slice = 1
        self.tool_calls_used = 0
        self.no_progress_cycles = 0

        results: List[str] = []
        started_at = time.monotonic()
        guidance_emitted_for_slice = False
        try:
            async with self.state_context(AgentState.RUNNING):
                task.emit(
                    "agent_state",
                    {"state": "running", "agent": self.name},
                )
                task.emit(
                    "execution_policy",
                    self.execution_policy.public_summary(),
                )
                while self.state != AgentState.FINISHED:
                    if task.is_interrupted():
                        raise TaskInterrupted()

                    budget_reason = self._hard_budget_reason(started_at)
                    if budget_reason:
                        await self._finish_after_budget(task, budget_reason, results)
                        break

                    if self.current_step >= self.max_steps:
                        if (
                            self.current_slice
                            <= self.execution_policy.max_continuations
                        ):
                            task.emit(
                                "execution_slice",
                                {
                                    "state": "continuing",
                                    "completed_slice": self.current_slice,
                                    "next_slice": self.current_slice + 1,
                                    "total_steps": self.total_steps,
                                    "mode": self.execution_policy.mode,
                                },
                            )
                            self.update_memory(
                                "user",
                                (
                                    "Continue the same task from the preserved state. "
                                    "Re-check the plan and completed tool results, skip work "
                                    "already done, prioritize the remaining deliverable, and "
                                    "finish naturally as soon as it is complete and verified."
                                ),
                            )
                            self.current_slice += 1
                            self.current_step = 0
                            guidance_emitted_for_slice = False
                            continue

                        budget_reason = (
                            f"Execution used all {self.current_slice} permitted work slices "
                            f"({self.total_steps} model turns)."
                        )
                        await self._finish_after_budget(task, budget_reason, results)
                        break

                    self.current_step += 1
                    self.total_steps += 1
                    soft_step = max(
                        1,
                        math.ceil(
                            self.max_steps * self.execution_policy.soft_limit_ratio
                        ),
                    )
                    if (
                        not guidance_emitted_for_slice
                        and self.current_step >= soft_step
                    ):
                        guidance_emitted_for_slice = True
                        remaining = max(0, self.max_steps - self.current_step)
                        usage = get_current_token_usage()
                        task.emit(
                            "execution_budget",
                            {
                                "state": "guidance",
                                "mode": self.execution_policy.mode,
                                "slice": self.current_slice,
                                "remaining_slice_steps": remaining,
                                "tokens_used": usage["total"],
                                "token_budget": self.execution_policy.token_budget,
                            },
                        )
                        self.update_memory(
                            "user",
                            (
                                "Execution budget guidance: stop optional exploration. "
                                "Prioritize creating and verifying the requested deliverable. "
                                "Finish naturally when complete; otherwise preserve a precise "
                                "remaining-work state for the next execution slice."
                            ),
                        )
                    task.emit(
                        "step_start",
                        {
                            "step": self.current_step,
                            "total_step": self.total_steps,
                            "slice": self.current_slice,
                            "mode": self.execution_policy.mode,
                        },
                    )
                    step_result = await self.step(task)

                    if self.is_stuck():
                        self.no_progress_cycles += 1
                        self.handle_stuck_state(task)
                    else:
                        self.no_progress_cycles = 0

                    if self.state == AgentState.FINISHED and step_result:
                        results.append(str(step_result))
                    else:
                        results.append(f"Step {self.current_step}: {step_result}")
                    task.emit(
                        "step_result",
                        {"step": self.current_step, "result": step_result},
                    )

            if self.final_response:
                return self.final_response
            return "\n".join(results) if results else "No steps executed"
        finally:
            task.emit(
                "agent_state",
                {
                    "state": str(
                        self.state.value if hasattr(self.state, "value") else self.state
                    ),
                    "agent": self.name,
                },
            )
            await SANDBOX_CLIENT.cleanup()

    def _hard_budget_reason(self, started_at: float) -> Optional[str]:
        usage = get_current_token_usage()
        elapsed = time.monotonic() - started_at
        if usage["total"] >= self.execution_policy.token_budget:
            return (
                f"Execution reached its {self.execution_policy.token_budget:,}-token "
                "task budget."
            )
        if self.tool_calls_used >= self.execution_policy.max_tool_calls:
            return (
                f"Execution reached its {self.execution_policy.max_tool_calls} "
                "tool-call safety limit."
            )
        if elapsed >= self.execution_policy.max_wall_time_seconds:
            return (
                f"Execution reached its {self.execution_policy.max_wall_time_seconds}-second "
                "wall-time budget."
            )
        if self.no_progress_cycles >= self.execution_policy.max_no_progress_cycles:
            return (
                "Execution repeatedly selected the same ineffective action without "
                "making measurable progress."
            )
        return None

    async def _finish_after_budget(
        self, task: Task, reason: str, results: List[str]
    ) -> None:
        task.emit(
            "execution_budget",
            {
                "state": "finalizing",
                "mode": self.execution_policy.mode,
                "reason": reason,
                "total_steps": self.total_steps,
                "tool_calls": self.tool_calls_used,
                "tokens": get_current_token_usage()["total"],
            },
        )
        finalization_result = await self._finalize_after_budget(task, reason)
        if self.state == AgentState.FINISHED:
            if finalization_result:
                results.append(str(finalization_result))
            return

        self.final_status = "failure"
        self.final_reason = reason
        self.final_response = (
            "I couldn't complete the task within the configured execution budget. "
            "Completed work and tool results remain available in this conversation."
        )
        self.state = AgentState.FINISHED
        task.emit(
            "terminated",
            {
                "reason": reason,
                "status": "budget_exhausted",
                "message": self.final_response,
            },
        )

    async def _finalize_after_budget(self, task: Task, reason: str) -> Optional[str]:
        """Give specialized agents a non-work pass to report their final state."""
        return await self._finalize_after_step_limit(task)

    async def _finalize_after_step_limit(self, task: Task) -> Optional[str]:
        """Compatibility hook for subclasses using the previous loop API."""
        return None

    @abstractmethod
    async def step(self, task: Task) -> str:
        """Execute a single step in the agent's workflow.

        Must be implemented by subclasses to define specific behavior.
        """

    def handle_stuck_state(self, task: Task):
        """Handle stuck state by adding a prompt to change strategy"""
        stuck_prompt = "\
        Observed duplicate responses. Consider new strategies and avoid repeating ineffective paths already attempted."
        self.next_step_prompt = f"{stuck_prompt}\n{self.next_step_prompt}"
        task.emit(
            "stuck_detected",
            {
                "state": "stuck",
                "message": "Agent detected repeated responses and injected a strategy-change prompt.",
            },
        )

    def is_stuck(self) -> bool:
        """Detect stuck loops via two complementary signals.

        1. Semantic tool-loop: the same tool is called with identical arguments
           three or more times in the last 12 assistant messages — even if the
           surrounding text is different each time.
        2. Content-hash fallback: assistant content that hashes identically
           (after whitespace normalization) appears `duplicate_threshold` times.
           This catches trivial wording variation that exact string match misses.
        """
        import hashlib

        messages = self.memory.messages
        if len(messages) < 2:
            return False

        # --- Signal 1: repeated tool-call signatures ---
        recent_with_tools = [
            m for m in messages[-12:] if getattr(m, "tool_calls", None)
        ]
        if len(recent_with_tools) >= self.duplicate_threshold:
            call_signatures: list[str] = []
            for msg in recent_with_tools:
                for tc in msg.tool_calls or []:
                    sig = f"{tc.function.name}:{tc.function.arguments}"
                    call_signatures.append(sig)
            from collections import Counter

            counts = Counter(call_signatures)
            if any(count >= self.duplicate_threshold for count in counts.values()):
                return True

        # --- Signal 2: content-hash duplicate (improved from exact match) ---
        last_message = messages[-1]
        if not last_message.content:
            return False

        def _content_hash(text: str) -> str:
            """Normalize whitespace and hash for near-duplicate detection."""
            normalized = " ".join(text.split()).lower().strip()
            return hashlib.md5(normalized.encode()).hexdigest()

        last_hash = _content_hash(last_message.content)
        duplicate_count = sum(
            1
            for msg in reversed(messages[:-1])
            if msg.role == "assistant"
            and msg.content
            and _content_hash(msg.content) == last_hash
        )
        return duplicate_count >= self.duplicate_threshold

    @property
    def messages(self) -> List[Message]:
        """Retrieve a list of messages from the agent's memory."""
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        """Set the list of messages in the agent's memory."""
        self.memory.messages = value
