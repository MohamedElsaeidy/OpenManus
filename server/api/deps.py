import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, Response

from app.config import config
from app.sandbox.conversation import ConversationSandbox
from core.task_registry import TaskRegistry
from server.api.event_mapping import _agent_event_to_progress
from server.models import AppSettingORM, ConversationORM, SessionORM, TaskORM, UserORM


REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
DEFAULT_CONVERSATION_ID = os.getenv("OPENMANUS_DEFAULT_CONVERSATION_ID", "main")
SESSION_COOKIE = "openmanus_session"
SESSION_DAYS = int(os.getenv("OPENMANUS_SESSION_DAYS", "30"))
WORKSPACE_ROOT = os.getenv("OPENMANUS_WORKSPACE_ROOT", "/app/workspace")
HOST_WORKSPACE_ROOT = os.getenv("OPENMANUS_HOST_WORKSPACE_ROOT", "/app/workspace")

registry = TaskRegistry()

AVAILABLE_TOOLS = [
    {"name": "skill_playbook", "label": "Skill Playbook", "scope": "reasoning"},
    {"name": "planning", "label": "Planning", "scope": "reasoning"},
    {"name": "codebase_overview", "label": "Codebase Overview", "scope": "code"},
    {"name": "glob", "label": "Glob Search", "scope": "code"},
    {"name": "grep", "label": "Grep Search", "scope": "code"},
    {"name": "read_files", "label": "Read Files", "scope": "code"},
    {"name": "python_execute", "label": "Python", "scope": "terminal"},
    {"name": "bash", "label": "Bash", "scope": "terminal"},
    {"name": "browser_use", "label": "Browser", "scope": "browser"},
    {"name": "web_search", "label": "Web Search", "scope": "browser"},
    {"name": "apply_patch_editor", "label": "Patch Editor", "scope": "code"},
    {"name": "memory_save", "label": "Memory Save", "scope": "memory"},
    {"name": "memory_recall", "label": "Memory Recall", "scope": "memory"},
    {"name": "ask_human", "label": "Ask Human", "scope": "conversation"},
    {"name": "wait_for_user_input", "label": "Wait For Input", "scope": "conversation"},
    {"name": "terminate", "label": "Terminate", "scope": "control", "locked": True},
]

CONVERSATION_STATES = {
    "CREATED": "running",
    "RUNNING": "running",
    "COMPLETED": "finished",
    "FAILED": "error",
    "INTERRUPTED": "paused",
}

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "INTERRUPTED"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    iterations = 210_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
        return secrets.compare_digest(check, digest)
    except Exception:
        return False


def _session_token_from_request(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def _public_user(user: UserORM) -> dict:
    return {
        "id": str(user.user_id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
    }


def _require_user(request: Request) -> UserORM:
    token = _session_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not signed in")
    with registry.SessionLocal() as session:
        session_orm = session.get(SessionORM, token)
        if session_orm is None or session_orm.expires_at <= _now():
            raise HTTPException(status_code=401, detail="Session expired")
        user = session.get(UserORM, session_orm.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        session.expunge(user)
        return user


def _require_admin(request: Request) -> UserORM:
    user = _require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _create_session(response: Response, user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(days=SESSION_DAYS)
    with registry.SessionLocal() as session:
        session.add(SessionORM(token=token, user_id=user_id, expires_at=expires_at))
        session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return token


def _require_conversation(
    session, user_id: uuid.UUID, conversation_id: str
) -> ConversationORM:
    try:
        cid = uuid.UUID(str(conversation_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation id")
    conversation = session.get(ConversationORM, cid)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _task_belongs_to_user(session, orm: TaskORM, user_id: uuid.UUID) -> bool:
    conversation_id = (orm.input or {}).get("conversation_id")
    if not conversation_id:
        return False
    try:
        conversation = session.get(ConversationORM, uuid.UUID(str(conversation_id)))
    except ValueError:
        return False
    return conversation is not None and conversation.user_id == user_id


def _get_app_setting(session, key: str, default: Any) -> Any:
    setting = session.get(AppSettingORM, key)
    if setting is None:
        return default
    return setting.value


def _set_app_setting(session, key: str, value: Any) -> None:
    setting = session.get(AppSettingORM, key)
    if setting is None:
        session.add(AppSettingORM(key=key, value=value))
    else:
        setting.value = value


def _ensure_default_conversation(session, user_id: uuid.UUID) -> ConversationORM:
    from sqlalchemy import asc

    conversation = (
        session.query(ConversationORM)
        .filter(ConversationORM.user_id == user_id)
        .order_by(asc(ConversationORM.created_at))
        .first()
    )
    if conversation is not None:
        return conversation
    conversation = ConversationORM(user_id=user_id, title="New conversation")
    session.add(conversation)
    session.flush()
    return conversation


def _conversation_id_for(orm: TaskORM) -> str:
    task_input = orm.input or {}
    if task_input.get("conversation_id"):
        return str(task_input.get("conversation_id"))
    if os.getenv("OPENMANUS_SINGLE_CONVERSATION", "true").lower() != "false":
        return DEFAULT_CONVERSATION_ID
    return str(task_input.get("conversation_id") or DEFAULT_CONVERSATION_ID)


def _conversation_sandbox(conversation_id: str) -> ConversationSandbox:
    return ConversationSandbox(
        conversation_id=conversation_id,
        host_workspace=Path(HOST_WORKSPACE_ROOT) / "conversations" / conversation_id,
        config=config.sandbox,
    )


def _conversation_tasks(
    session, conversation_id: str, ascending: bool = True
) -> list[TaskORM]:
    from sqlalchemy import asc, desc

    order = asc(TaskORM.created_at) if ascending else desc(TaskORM.created_at)
    return (
        session.query(TaskORM)
        .filter(TaskORM.input["conversation_id"].astext == str(conversation_id))
        .order_by(order)
        .all()
    )


async def _task_stream_progress(task: TaskORM) -> list[dict]:
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    stream_key = f"task:{task.task_id}:stream"
    progress_events: list[dict] = []
    try:
        entries = await redis.xrange(stream_key, min="-", max="+")
    finally:
        await redis.aclose()

    prompt = (task.input or {}).get("prompt", "")
    has_emitted_complete = False
    for msg_id, fields in entries:
        event_type = fields.get("type", "")
        try:
            data = json.loads(fields.get("data", "{}"))
        except Exception:
            data = {}

        progress_list = _agent_event_to_progress({"type": event_type, "data": data})
        for progress_index, progress in enumerate(progress_list):
            if progress.get("name") == "agent:lifecycle:start":
                content = dict(progress.get("content") or {})
                content.setdefault("request", prompt)
                content.setdefault("task_id", str(task.task_id))
                content.setdefault("conversation_id", _conversation_id_for(task))
                progress["content"] = content
            else:
                content = dict(progress.get("content") or {})
                content.setdefault("task_id", str(task.task_id))
                content.setdefault("conversation_id", _conversation_id_for(task))
                progress["content"] = content

            is_complete_event = progress.get("name") == "agent:lifecycle:complete"
            if is_complete_event and has_emitted_complete:
                continue
            if is_complete_event:
                has_emitted_complete = True

            progress_events.append(
                {
                    **progress,
                    "id": f"{task.task_id}:{msg_id}:{progress_index}:{progress.get('name')}",
                    "task_id": str(task.task_id),
                    "created_at": task.created_at.isoformat()
                    if task.created_at
                    else None,
                }
            )

    if not progress_events and task.status in TERMINAL_STATUSES:
        progress_events = [
            {
                "id": f"{task.task_id}:synthetic:start",
                "type": "progress",
                "name": "agent:lifecycle:start",
                "task_id": str(task.task_id),
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "content": {
                    "request": prompt,
                    "task_id": str(task.task_id),
                    "conversation_id": _conversation_id_for(task),
                },
            },
            {
                "id": f"{task.task_id}:synthetic:complete",
                "type": "progress",
                "name": "agent:lifecycle:complete",
                "task_id": str(task.task_id),
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "content": {
                    "message": "Task already completed",
                    "task_id": str(task.task_id),
                    "conversation_id": _conversation_id_for(task),
                },
            },
        ]

    return progress_events
