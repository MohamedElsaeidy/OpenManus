from typing import Literal, Optional

from pydantic import BaseModel, Field


ExecutionMode = Literal["fast", "balanced", "deep"]

# What the composer offers, and what each choice means for one run.
#
# Chat modes are the user-facing vocabulary; ExecutionMode is the internal
# budget profile. They are deliberately separate: "orchestrated" is a different
# execution *strategy* that still needs a budget, and picking it should not
# force the user to also reason about token ceilings.
CHAT_MODES = ("instant", "balanced", "thinking", "orchestrated")

ChatMode = Literal["instant", "balanced", "thinking", "orchestrated"]

_CHAT_MODE_BUDGETS: dict[str, ExecutionMode] = {
    "instant": "fast",
    "balanced": "balanced",
    "thinking": "deep",
    # Orchestration fans work out to several workers, so the lead needs the
    # widest budget to plan, review every result, and synthesise.
    "orchestrated": "deep",
}


def normalize_chat_mode(mode: Optional[str]) -> Optional[str]:
    """Return a known chat mode, or None when the caller did not pick one."""
    normalized = str(mode or "").strip().lower()
    return normalized if normalized in CHAT_MODES else None


def execution_mode_for_chat_mode(mode: Optional[str]) -> Optional[ExecutionMode]:
    normalized = normalize_chat_mode(mode)
    return _CHAT_MODE_BUDGETS.get(normalized) if normalized else None


def is_orchestrated(mode: Optional[str]) -> bool:
    return normalize_chat_mode(mode) == "orchestrated"


class ExecutionPolicy(BaseModel):
    """Layered limits for one autonomous agent run."""

    mode: ExecutionMode = "balanced"
    slice_steps: int = Field(ge=1)
    max_continuations: int = Field(ge=0)
    token_budget: int = Field(ge=1)
    enforce_token_budget: bool = True
    max_wall_time_seconds: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)
    max_no_progress_cycles: int = Field(ge=1)
    step_token_reserve: int = Field(ge=1)
    soft_limit_ratio: float = Field(default=0.8, gt=0.0, lt=1.0)

    @classmethod
    def for_mode(cls, mode: str) -> "ExecutionPolicy":
        normalized = str(mode or "balanced").strip().lower()
        profiles = {
            "fast": cls(
                mode="fast",
                slice_steps=12,
                max_continuations=1,
                token_budget=96_000,
                max_wall_time_seconds=420,
                max_tool_calls=48,
                max_no_progress_cycles=2,
                step_token_reserve=12_000,
            ),
            "balanced": cls(
                mode="balanced",
                slice_steps=24,
                max_continuations=2,
                token_budget=320_000,
                max_wall_time_seconds=1_080,
                max_tool_calls=180,
                max_no_progress_cycles=3,
                step_token_reserve=24_000,
            ),
            "deep": cls(
                mode="deep",
                slice_steps=32,
                max_continuations=4,
                token_budget=1_000_000,
                max_wall_time_seconds=1_620,
                max_tool_calls=480,
                max_no_progress_cycles=4,
                step_token_reserve=32_000,
            ),
        }
        return profiles.get(normalized, profiles["balanced"])

    @property
    def total_step_guard(self) -> int:
        return self.slice_steps * (self.max_continuations + 1)

    def without_token_limit(self) -> "ExecutionPolicy":
        """Keep token telemetry while disabling cumulative token termination."""
        return self.model_copy(update={"enforce_token_budget": False})

    def public_summary(self) -> dict:
        return {
            "mode": self.mode,
            "slice_steps": self.slice_steps,
            "max_continuations": self.max_continuations,
            "token_budget": self.token_budget,
            "token_budget_enforced": self.enforce_token_budget,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_no_progress_cycles": self.max_no_progress_cycles,
            "step_token_reserve": self.step_token_reserve,
        }
