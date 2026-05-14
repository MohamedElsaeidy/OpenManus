import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Optional

import redis as redis_lib
from sqlalchemy.exc import SQLAlchemyError

from app.agent.manus import Manus
from app.config import config
from app.runtime_settings import get_disabled_tools, get_llm_connection
from app.sandbox.conversation import ConversationSandbox
from app.skills import format_skill_context, select_skills
from app.task_context import (
    current_llm_connection,
    current_model,
    current_sandbox,
    current_task,
    current_workspace,
)
from core.task import TaskStatus
from core.task_registry import TaskRegistry
from server.celery_app import celery_app
from server.models import ConversationEventORM, TaskORM


registry = TaskRegistry()

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
DEFAULT_CONVERSATION_ID = os.getenv("OPENMANUS_DEFAULT_CONVERSATION_ID", "main")
_redis_client: Optional[redis_lib.Redis] = None


def get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def publish_event(task_id: str, event_type: str, data: dict) -> None:
    """Append an agent event to a Redis Stream so SSE can replay from the start."""
    stream_key = f"task:{task_id}:stream"
    payload = data if isinstance(data, dict) else {"value": str(data)}
    try:
        get_redis().xadd(
            stream_key,
            {"type": event_type, "data": json.dumps(payload)},
            maxlen=500,  # cap stream size
        )
    except Exception:
        pass  # Never let Redis failures crash the worker
    try:
        conversation_id = payload.get("conversation_id") or get_conversation_id(task_id)
        with registry.SessionLocal() as session:
            session.add(
                ConversationEventORM(
                    conversation_id=uuid.UUID(str(conversation_id)),
                    task_id=uuid.UUID(str(task_id)),
                    event_type=event_type,
                    payload=payload,
                )
            )
            session.commit()
    except (SQLAlchemyError, Exception):
        pass


def summarize_workspace(workspace_root: Path) -> dict:
    """Collect a small artifact summary for the completion event."""
    if not workspace_root.exists():
        return {"pdfs": [], "tex": [], "logs": [], "warning": "Workspace not found."}

    def _relative_files(pattern: str) -> list[str]:
        return sorted(
            str(path.relative_to(workspace_root))
            for path in workspace_root.rglob(pattern)
            if path.is_file()
        )

    pdfs = _relative_files("*.pdf")
    tex_files = _relative_files("*.tex")
    logs = _relative_files("*.log")
    warning = None

    if not pdfs and (tex_files or logs):
        warning = (
            "No PDF was found in the task workspace. "
            "LaTeX may have failed; check the terminal output or .log files."
        )

    return {"pdfs": pdfs, "tex": tex_files, "logs": logs, "warning": warning}


def get_task_record(task_id: str) -> Optional[TaskORM]:
    with registry.SessionLocal() as session:
        orm = session.get(TaskORM, task_id)
        if orm is None:
            return None
        session.expunge(orm)
        return orm


def get_conversation_id(task_id: str) -> str:
    orm = get_task_record(task_id)
    if orm is None:
        return DEFAULT_CONVERSATION_ID
    task_input = orm.input or {}
    if task_input.get("conversation_id"):
        return str(task_input.get("conversation_id"))
    if os.getenv("OPENMANUS_SINGLE_CONVERSATION", "true").lower() != "false":
        return DEFAULT_CONVERSATION_ID
    return str(task_input.get("conversation_id") or DEFAULT_CONVERSATION_ID)


def get_task_model(task_id: str) -> Optional[str]:
    orm = get_task_record(task_id)
    if orm is None:
        return None
    task_input = orm.input or {}
    return task_input.get("model")


def get_task_disabled_tools(task_id: str) -> set[str]:
    orm = get_task_record(task_id)
    if orm is None:
        return set()
    task_input = orm.input or {}
    return {str(name) for name in task_input.get("disabled_tools", [])}


def conversation_workspace(conversation_id: str) -> Path:
    return (
        Path(os.getenv("OPENMANUS_WORKSPACE_ROOT", "/app/workspace"))
        / "conversations"
        / conversation_id
    )


def host_conversation_workspace(conversation_id: str) -> Path:
    return (
        Path(os.getenv("OPENMANUS_HOST_WORKSPACE_ROOT", "/app/workspace"))
        / "conversations"
        / conversation_id
    )


def build_conversation_context(
    task_id: str, conversation_id: str, workspace_root: Path
) -> str:
    """Build compact continuity context for follow-up tasks in the same conversation."""
    with registry.SessionLocal() as session:
        rows = session.query(TaskORM).order_by(TaskORM.created_at.asc()).all()

    conversation_rows = [
        row
        for row in rows
        if (
            DEFAULT_CONVERSATION_ID
            if os.getenv("OPENMANUS_SINGLE_CONVERSATION", "true").lower() != "false"
            else str(
                (row.input or {}).get("conversation_id") or DEFAULT_CONVERSATION_ID
            )
        )
        == conversation_id
        and str(row.task_id) != task_id
    ][-6:]

    if not conversation_rows and not workspace_root.exists():
        return ""

    lines = [
        "Conversation continuity context:",
        f"- Shared workspace: {workspace_root}",
        "- Treat this as the same ongoing project. Reuse existing files and state.",
        "- Do not restart prior work unless the user explicitly asks; continue from the latest result.",
    ]

    if conversation_rows:
        lines.append("- Previous tasks in this conversation:")
        for row in conversation_rows:
            task_input = row.input or {}
            result = row.result or {}
            output = str(result.get("output") or result.get("error") or "")
            output = output.replace("\n", " ")
            if len(output) > 500:
                output = output[:500] + "..."
            lines.append(
                f"  - {row.status}: {task_input.get('prompt', '(no prompt)')} | {output}"
            )

    if workspace_root.exists():
        files = sorted(
            str(path.relative_to(workspace_root))
            for path in workspace_root.rglob("*")
            if path.is_file()
        )[:80]
        if files:
            lines.append("- Current workspace files:")
            lines.extend(f"  - {file}" for file in files)

    return "\n".join(lines)


class RedisEmittingTask:
    """
    Wraps a Task so that every emit() is also written to a Redis Stream.
    This allows the web server (different process) to stream events to the browser.
    """

    def __init__(self, task_id: str, inner_task):
        self.id = task_id
        self._inner = inner_task

    def emit(self, type: str, data) -> None:
        self._inner.emit(type, data)
        publish_event(
            self.id, type, data if isinstance(data, dict) else {"value": str(data)}
        )

    def is_interrupted(self) -> bool:
        return self._inner.is_interrupted()

    def interrupt(self) -> None:
        self._inner.interrupt()

    def __getattr__(self, name):
        return getattr(self._inner, name)


@celery_app.task(name="run_task")
def run_task(task_id: str, prompt: Optional[str] = None):
    """Celery task: run Manus agent and write events to Redis Stream."""
    task = registry.get_task(task_id)
    if task is None:
        return {"error": "task not found"}

    wrapped = RedisEmittingTask(task_id, task)
    conversation_id = get_conversation_id(task_id)
    model = get_task_model(task_id)
    disabled_tools = get_disabled_tools() | get_task_disabled_tools(task_id)
    llm_connection = get_llm_connection()
    workspace_root = conversation_workspace(conversation_id)
    host_workspace_root = host_conversation_workspace(conversation_id)

    async def _run():
        workspace_root.mkdir(parents=True, exist_ok=True)
        sandbox = None
        sandbox_token = None
        workspace_token = current_workspace.set(str(workspace_root))
        model_token = current_model.set(model)
        llm_connection_token = current_llm_connection.set(llm_connection)
        if config.sandbox.use_sandbox:
            sandbox = await ConversationSandbox(
                conversation_id=conversation_id,
                host_workspace=host_workspace_root,
                config=config.sandbox,
            ).ensure()
            sandbox_token = current_sandbox.set(sandbox)

        previous_cwd = os.getcwd()
        os.chdir(workspace_root)
        token = current_task.set(wrapped)
        try:
            continuity = build_conversation_context(
                task_id, conversation_id, workspace_root
            )
            skill_context = format_skill_context(
                select_skills(prompt or "", workspace_root)
            )
            if sandbox is not None:
                continuity = continuity.replace(
                    str(workspace_root), config.sandbox.work_dir
                )
                skill_context = skill_context.replace(
                    str(workspace_root), config.sandbox.work_dir
                )
            context_parts = [part for part in (continuity, skill_context) if part]
            combined_context = "\n\n".join(context_parts)
            run_prompt = (
                f"{combined_context}\n\nCurrent user request:\n{prompt}"
                if context_parts and prompt
                else prompt
            )
            agent_workspace = (
                config.sandbox.work_dir if sandbox is not None else str(workspace_root)
            )
            agent = await Manus.create(
                workspace_root=agent_workspace,
                disabled_tools=disabled_tools,
            )
            result = await agent.run(wrapped, run_prompt)
            return result
        finally:
            current_task.reset(token)
            if sandbox_token is not None:
                current_sandbox.reset(sandbox_token)
            current_workspace.reset(workspace_token)
            current_model.reset(model_token)
            current_llm_connection.reset(llm_connection_token)
            os.chdir(previous_cwd)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_run())
        task.status = "COMPLETED"
        workspace_summary = summarize_workspace(workspace_root)
        result_text = str(result or "").strip()
        completion_message = result_text if result_text else "Task completed."
        registry.update_task(
            task,
            result={
                "output": result,
                "workspace": workspace_summary,
                "conversation_id": conversation_id,
            },
        )
        publish_event(
            task_id,
            "finish_signal",
            {
                "message": completion_message,
                "workspace": workspace_summary,
                "conversation_id": conversation_id,
            },
        )
        return {"status": "COMPLETED", "result": result}
    except Exception as exc:
        task.status = TaskStatus.FAILED
        registry.update_task(task, result={"error": str(exc)})
        publish_event(
            task_id,
            "error",
            {
                "message": "Task failed",
                "detail": str(exc),
                "reason": str(exc),
                "conversation_id": conversation_id,
            },
        )
        return {"status": "FAILED", "error": str(exc)}
    finally:
        try:
            loop.close()
        except Exception:
            pass
