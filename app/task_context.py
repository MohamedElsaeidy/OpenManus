from contextvars import ContextVar
from typing import Any, Optional


current_task: ContextVar[Optional[Any]] = ContextVar("current_task", default=None)
current_tool_call: ContextVar[Optional[dict]] = ContextVar(
    "current_tool_call", default=None
)
current_sandbox: ContextVar[Optional[Any]] = ContextVar("current_sandbox", default=None)
current_workspace: ContextVar[Optional[str]] = ContextVar(
    "current_workspace", default=None
)
current_model: ContextVar[Optional[str]] = ContextVar("current_model", default=None)
current_llm_connection: ContextVar[Optional[dict]] = ContextVar(
    "current_llm_connection", default=None
)


def get_current_task() -> Optional[Any]:
    return current_task.get()


def emit_current_task(event_type: str, data: dict) -> None:
    task = get_current_task()
    if task is not None:
        task.emit(event_type, data)


def get_current_tool_call() -> Optional[dict]:
    return current_tool_call.get()


def get_current_sandbox() -> Optional[Any]:
    return current_sandbox.get()


def get_current_workspace() -> Optional[str]:
    return current_workspace.get()


def get_current_model() -> Optional[str]:
    return current_model.get()


def get_current_llm_connection() -> Optional[dict]:
    return current_llm_connection.get()
