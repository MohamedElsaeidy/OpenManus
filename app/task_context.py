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
current_requested_context_window: ContextVar[Optional[int]] = ContextVar(
    "current_requested_context_window", default=None
)
current_auto_context_compress: ContextVar[bool] = ContextVar(
    "current_auto_context_compress", default=True
)
current_execution_usage: ContextVar[Optional[dict[str, int]]] = ContextVar(
    "current_execution_usage", default=None
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
    conn = current_llm_connection.get()
    if conn and isinstance(conn, dict) and conn.get("base_url"):
        return conn
    try:
        from app.runtime_settings import get_llm_connection as get_runtime_llm_conn

        runtime_conn = get_runtime_llm_conn()
        if (
            runtime_conn
            and isinstance(runtime_conn, dict)
            and runtime_conn.get("base_url")
        ):
            return runtime_conn
    except Exception:
        pass
    return conn


def get_current_requested_context_window() -> Optional[int]:
    return current_requested_context_window.get()


def get_current_auto_context_compress() -> bool:
    return current_auto_context_compress.get()


def add_current_token_usage(
    input_tokens: int, completion_tokens: int
) -> dict[str, int]:
    usage = current_execution_usage.get()
    if usage is None:
        return {"input": 0, "completion": 0, "total": 0}
    usage["input"] = usage.get("input", 0) + max(0, int(input_tokens))
    usage["completion"] = usage.get("completion", 0) + max(0, int(completion_tokens))
    usage["total"] = usage["input"] + usage["completion"]
    return dict(usage)


def get_current_token_usage() -> dict[str, int]:
    usage = current_execution_usage.get() or {}
    return {
        "input": int(usage.get("input", 0)),
        "completion": int(usage.get("completion", 0)),
        "total": int(usage.get("total", 0)),
    }


def has_current_execution_usage() -> bool:
    return current_execution_usage.get() is not None
