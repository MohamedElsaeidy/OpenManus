from typing import Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal["fast", "balanced", "deep"]


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
