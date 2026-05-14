import asyncio
import hashlib
import json
import os
import secrets
import shutil
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, text
from sse_starlette.sse import EventSourceResponse

from app.config import config
from app.sandbox.conversation import ConversationSandbox
from app.skills import load_skills, select_skills
from core.task import TaskStatus
from core.task_registry import TaskRegistry
from server.celery_app import celery_app
from server.models import (
    AppSettingORM,
    ConversationEventORM,
    ConversationORM,
    ObsidianEdgeORM,
    ObsidianNoteORM,
    SessionORM,
    TaskORM,
    UserORM,
)
from server.tasks import run_task


REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
DEFAULT_CONVERSATION_ID = os.getenv("OPENMANUS_DEFAULT_CONVERSATION_ID", "main")
SESSION_COOKIE = "openmanus_session"
SESSION_DAYS = int(os.getenv("OPENMANUS_SESSION_DAYS", "30"))
WORKSPACE_ROOT = os.getenv("OPENMANUS_WORKSPACE_ROOT", "/app/workspace")
HOST_WORKSPACE_ROOT = os.getenv(
    "OPENMANUS_HOST_WORKSPACE_ROOT", "/app/workspace"
)


app = FastAPI(title="OpenManus Task API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = TaskRegistry()


def _ensure_schema_updates() -> None:
    from server.models import Base

    Base.metadata.create_all(bind=registry.engine)
    with registry.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS model VARCHAR")
        )
        connection.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversation_events_conversation_created "
                "ON conversation_events (conversation_id, created_at, event_id)"
            )
        )


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
    {"name": "str_replace_editor", "label": "File Editor", "scope": "code"},
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


_ensure_schema_updates()


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "INTERRUPTED"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _require_admin(request: Request) -> UserORM:
    user = _require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


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


def _conversation_to_dict(session, conversation: ConversationORM) -> dict:
    from sqlalchemy import desc

    latest_task = (
        session.query(TaskORM)
        .filter(
            TaskORM.input["conversation_id"].astext == str(conversation.conversation_id)
        )
        .order_by(desc(TaskORM.created_at))
        .first()
    )
    latest_status = latest_task.status if latest_task else None
    settings = conversation.settings or {}
    requested_context_window = settings.get("requested_context_window")
    auto_context_compress = settings.get("auto_context_compress", True)
    if requested_context_window not in (None, ""):
        try:
            requested_context_window = int(requested_context_window)
        except (TypeError, ValueError):
            requested_context_window = None
    current_input = 0
    if latest_task is not None:
        latest_token_event = (
            session.query(ConversationEventORM)
            .filter(
                ConversationEventORM.task_id == latest_task.task_id,
                ConversationEventORM.event_type == "token_count",
            )
            .order_by(desc(ConversationEventORM.created_at))
            .first()
        )
        if latest_token_event is not None:
            payload = latest_token_event.payload or {}
            current_input = payload.get("total_input") or payload.get("input") or 0
            try:
                current_input = int(current_input)
            except (TypeError, ValueError):
                current_input = 0
    ratio = (
        round(current_input / requested_context_window, 4)
        if requested_context_window and requested_context_window > 0
        else None
    )
    latest_context = {
        "requested_window": requested_context_window,
        "current_input_tokens": current_input,
        "usage_ratio": ratio,
        "is_near_limit": bool(ratio is not None and ratio >= 0.9),
        "auto_context_compress": bool(auto_context_compress),
    }
    return {
        "id": str(conversation.conversation_id),
        "conversation_id": str(conversation.conversation_id),
        "title": conversation.title,
        "model": conversation.model,
        "settings": settings,
        "context": latest_context,
        "latest_task_id": str(latest_task.task_id) if latest_task else None,
        "latest_status": latest_status,
        "state": CONVERSATION_STATES.get(str(latest_status or ""), "idle"),
        "updated_at": conversation.updated_at.isoformat()
        if conversation.updated_at
        else None,
        "created_at": conversation.created_at.isoformat()
        if conversation.created_at
        else None,
    }


def _task_to_dict(orm: TaskORM) -> dict:
    status = str(orm.status)
    return {
        "id": str(orm.task_id),
        "task_id": str(orm.task_id),
        "status": status,
        "state": CONVERSATION_STATES.get(status, "idle"),
        "result": orm.result,
        "conversation_id": _conversation_id_for(orm),
        "created_at": orm.created_at.isoformat() if orm.created_at else None,
        "request": (orm.input or {}).get("prompt", "Untitled task"),
    }


def _persist_conversation_event(
    session,
    conversation_id: str,
    event_type: str,
    payload: dict,
    task_id: Optional[str] = None,
) -> ConversationEventORM:
    try:
        cid = uuid.UUID(str(conversation_id))
        tid = uuid.UUID(str(task_id)) if task_id else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid event id") from exc
    event = ConversationEventORM(
        conversation_id=cid,
        task_id=tid,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


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


def _conversation_sandbox(conversation_id: str) -> ConversationSandbox:
    return ConversationSandbox(
        conversation_id=conversation_id,
        host_workspace=Path(HOST_WORKSPACE_ROOT) / "conversations" / conversation_id,
        config=config.sandbox,
    )


def _prune_orphan_conversation_sandboxes(session) -> int:
    """Remove sandbox containers whose conversation row no longer exists."""
    try:
        import docker

        active_ids = {
            str(row[0]) for row in session.query(ConversationORM.conversation_id).all()
        }
        client = docker.from_env()
        removed = 0
        for container in client.containers.list(
            all=True, filters={"label": "openmanus.kind=conversation-sandbox"}
        ):
            conversation_id = container.labels.get("openmanus.conversation_id")
            if conversation_id and conversation_id not in active_ids:
                container.remove(force=True)
                removed += 1
        return removed
    except Exception:
        return 0


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


def _redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(
                secret in lowered
                for secret in ("api_key", "password", "token", "secret")
            ):
                redacted[key] = "********" if item else item
            else:
                redacted[key] = _redact_config(item)
        return redacted
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _loaded_config_defaults() -> dict:
    data = config._config.model_dump(mode="json")
    data["server"] = {
        "database_url": os.getenv("DATABASE_URL", ""),
        "redis_url": REDIS_URL,
        "workspace_root": WORKSPACE_ROOT,
        "single_conversation": os.getenv("OPENMANUS_SINGLE_CONVERSATION", "false"),
        "default_conversation_id": DEFAULT_CONVERSATION_ID,
        "session_days": SESSION_DAYS,
    }
    return _redact_config(data)


def _default_llm_connection() -> dict:
    default = config.llm.get("default")
    if default is None:
        return {}
    return _redact_config(default.model_dump(mode="json"))


def _effective_llm_connection(session) -> dict:
    override = _get_app_setting(session, "llm_connection", {})
    if isinstance(override, dict) and override.get("base_url"):
        return override
    default = config.llm.get("default")
    return default.model_dump(mode="json") if default is not None else {}


def _lmstudio_native_base(base_url: str) -> Optional[str]:
    try:
        parsed = urlparse.urlparse(base_url.strip())
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    root = f"{parsed.scheme}://{parsed.netloc}"
    return f"{root}/api/v1"


def _http_json(
    method: str,
    url: str,
    payload: Optional[dict] = None,
    token: Optional[str] = None,
    timeout: int = 8,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, method=method, headers=headers, data=body)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if not data:
            return {}
        return json.loads(data.decode("utf-8"))


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


def _extract_wikilinks(content: str) -> list[str]:
    if not content:
        return []
    out: list[str] = []
    for match in WIKILINK_RE.findall(content):
        normalized = str(match).strip()
        if normalized:
            out.append(normalized)
    return out


def _obsidian_graph_payload(session, conversation: ConversationORM) -> dict:
    notes = (
        session.query(ObsidianNoteORM)
        .filter(ObsidianNoteORM.conversation_id == conversation.conversation_id)
        .all()
    )
    edges = (
        session.query(ObsidianEdgeORM)
        .filter(ObsidianEdgeORM.conversation_id == conversation.conversation_id)
        .all()
    )
    node_payload = [
        {
            "id": str(note.note_id),
            "path": note.path,
            "title": note.title,
            "tags": note.tags or [],
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }
        for note in notes
    ]
    edge_payload = [
        {
            "id": str(edge.edge_id),
            "source": str(edge.source_note_id),
            "target": str(edge.target_note_id),
            "relation": edge.relation,
        }
        for edge in edges
    ]
    return {
        "nodes": node_payload,
        "edges": edge_payload,
        "node_count": len(node_payload),
        "edge_count": len(edge_payload),
    }


def _agent_event_to_progress(event: dict) -> list[dict]:
    """
    Convert one internal agent event into one or more SSE progress messages
    that match the frontend's lifecycle type hierarchy.

    Internal types → frontend lifecycle names:
      step_start     → [agent:lifecycle:start (once)] + agent:lifecycle:step:start
                        + agent:lifecycle:step:think:start
      thought        → agent:lifecycle:step:think:tool:selected
                        + agent:lifecycle:step:think:complete
                        + agent:lifecycle:step:act:start
      tool_result    → agent:lifecycle:step:act:tool:execute:complete
                        + agent:lifecycle:step:act:complete
                        + agent:lifecycle:step:complete
      finish_signal  → agent:lifecycle:complete
      final_response  → agent:lifecycle:complete
      terminated     → agent:lifecycle:terminated
      browser_screenshot → agent:lifecycle:step:think:browser:browse:complete
      token_count    → agent:lifecycle:step:think:token:count
      context_compressed → agent:lifecycle:step:think:context:compressed
      terminal_output → agent:lifecycle:step:act:tool:terminal:output
      workspace_file_updated → agent:lifecycle:step:act:tool:file:updated
      error          → agent:lifecycle:step:error
    """
    agent_type = event.get("type", "")
    data = event.get("data", {})

    def _msg(name: str, content=None) -> dict:
        return {"type": "progress", "name": name, "content": content or data}

    if agent_type == "step_start":
        step = data.get("step", 1)
        msgs = []
        if step == 1:
            msgs.append(_msg("agent:lifecycle:start", {"step": step}))
        msgs.append(_msg("agent:lifecycle:step:start", data))
        msgs.append(_msg("agent:lifecycle:step:think:start", data))
        return msgs

    if agent_type == "thought":
        tools = data.get("tools", [])
        tool_calls = data.get("tool_calls", [])
        first_call = tool_calls[0] if tool_calls else {}
        first_function = first_call.get("function", {})
        first_id = first_call.get("id") or (tools[0] if tools else None)
        first_name = first_function.get("name") or (tools[0] if tools else None)
        first_arguments = first_function.get("arguments") or data.get("arguments")
        msgs = [
            _msg(
                "agent:lifecycle:step:think:tool:selected",
                {
                    "tool": first_name,
                    "tool_calls": tool_calls,
                    "content": data.get("content", ""),
                },
            )
        ]
        msgs.append(_msg("agent:lifecycle:step:think:complete", data))
        if tools:
            msgs.append(_msg("agent:lifecycle:step:act:start", data))
            msgs.append(
                _msg(
                    "agent:lifecycle:step:act:tool:start",
                    {
                        "id": first_id,
                        "name": first_name,
                    },
                )
            )
            msgs.append(
                _msg(
                    "agent:lifecycle:step:act:tool:execute:start",
                    {
                        "id": first_id,
                        "name": first_name,
                        "arguments": first_arguments,
                    },
                )
            )
        return msgs

    if agent_type == "tool_result":
        tool = data.get("tool", "")
        tool_call_id = data.get("tool_call_id") or tool
        msgs = [
            _msg(
                "agent:lifecycle:step:act:tool:execute:complete",
                {
                    "id": tool_call_id,
                    "name": tool,
                    "result": data.get("result", ""),
                },
            )
        ]
        msgs.append(
            _msg(
                "agent:lifecycle:step:act:tool:complete",
                {"id": tool_call_id, "name": tool},
            )
        )
        msgs.append(_msg("agent:lifecycle:step:act:complete", data))
        msgs.append(_msg("agent:lifecycle:step:complete", data))
        return msgs

    if agent_type == "step_result":
        # Secondary step completion — already handled via tool_result path; skip
        return []

    if agent_type == "finish_signal":
        # Only map the final completion from tasks.py (which contains 'workspace')
        # to avoid duplicating the final message that is already shown in the 'thought' bubble.
        if "workspace" in data:
            return [_msg("agent:lifecycle:complete", data)]
        return []

    if agent_type == "final_response":
        # Ignore since the assistant text is already in the 'thought' event,
        # and tasks.py will emit a final finish_signal anyway.
        return []

    if agent_type == "browser_screenshot":
        return [_msg("agent:lifecycle:step:think:browser:browse:complete", data)]

    if agent_type == "token_count":
        return [_msg("agent:lifecycle:step:think:token:count", data)]

    if agent_type == "context_compressed":
        return [_msg("agent:lifecycle:step:think:context:compressed", data)]

    if agent_type == "terminal_output":
        return [_msg("agent:lifecycle:step:act:tool:terminal:output", data)]

    if agent_type == "workspace_file_updated":
        return [_msg("agent:lifecycle:step:act:tool:file:updated", data)]

    if agent_type == "terminated":
        return [_msg("agent:lifecycle:terminated", data)]

    if agent_type == "agent_state":
        return [_msg("agent:lifecycle:state:change", data)]

    if agent_type == "stuck_detected":
        return [_msg("agent:lifecycle:state:change", data)]

    if agent_type == "error":
        return [
            _msg("agent:lifecycle:step:error", data),
            _msg(
                "agent:lifecycle:terminated",
                {
                    **data,
                    "reason": data.get("detail")
                    or data.get("message")
                    or "Task failed",
                    "status": "failure",
                },
            ),
        ]

    # Catch-all: pass through as a generic step event
    return [_msg(f"agent:lifecycle:{agent_type}", data)]


def _task_input(
    prompt: Optional[str],
    conversation_id: str,
    parent_task_id: Optional[str] = None,
    model: Optional[str] = None,
    disabled_tools: Optional[list[str]] = None,
    requested_context_window: Optional[int] = None,
    auto_context_compress: Optional[bool] = None,
) -> dict:
    data = {"conversation_id": conversation_id}
    if prompt:
        data["prompt"] = prompt
    if parent_task_id:
        data["parent_task_id"] = parent_task_id
    if model:
        data["model"] = model
    if disabled_tools:
        data["disabled_tools"] = disabled_tools
    if requested_context_window and requested_context_window > 0:
        data["requested_context_window"] = int(requested_context_window)
    if auto_context_compress is not None:
        data["auto_context_compress"] = bool(auto_context_compress)
    return data


def _conversation_id_for(orm: TaskORM) -> str:
    task_input = orm.input or {}
    if task_input.get("conversation_id"):
        return str(task_input.get("conversation_id"))
    if os.getenv("OPENMANUS_SINGLE_CONVERSATION", "true").lower() != "false":
        return DEFAULT_CONVERSATION_ID
    return str(task_input.get("conversation_id") or DEFAULT_CONVERSATION_ID)


# ---------------------------------------------------------------------------
# Auth and conversations
# ---------------------------------------------------------------------------


@app.post("/api/auth/signup")
async def signup(request: Request, response: Response):
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    name = str(body.get("name") or email.split("@")[0] or "User").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    with registry.SessionLocal() as session:
        existing = session.query(UserORM).filter(UserORM.email == email).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Email is already registered")
        user_count = session.query(UserORM).count()
        role = "admin" if user_count == 0 else "user"
        user = UserORM(
            email=email,
            name=name,
            password_hash=_hash_password(password),
            role=role,
        )
        session.add(user)
        session.flush()
        _ensure_default_conversation(session, user.user_id)
        session.commit()
        public = _public_user(user)
        user_id = user.user_id
    token = _create_session(response, user_id)
    return {"user": public, "token": token}


@app.post("/api/auth/login")
async def login(request: Request, response: Response):
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    with registry.SessionLocal() as session:
        user = session.query(UserORM).filter(UserORM.email == email).first()
        if user is None or not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        public = _public_user(user)
        user_id = user.user_id
    token = _create_session(response, user_id)
    return {"user": public, "token": token}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = _session_token_from_request(request)
    if token:
        with registry.SessionLocal() as session:
            session_orm = session.get(SessionORM, token)
            if session_orm is not None:
                session.delete(session_orm)
                session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    user = _require_user(request)
    return {"user": _public_user(user)}


@app.get("/api/models")
async def list_models(request: Request):
    _require_user(request)
    with registry.SessionLocal() as session:
        connection = _get_app_setting(session, "llm_connection", {})

    configured = [
        {"id": settings.model, "name": name, "api_type": settings.api_type}
        for name, settings in config.llm.items()
        if settings.model
    ]
    if isinstance(connection, dict) and connection.get("model"):
        configured.insert(
            0,
            {
                "id": connection["model"],
                "name": "admin",
                "api_type": connection.get("api_type", "openai"),
            },
        )
    models = configured
    seen = set()
    unique_models = []
    for model in models:
        if model["id"] in seen:
            continue
        seen.add(model["id"])
        unique_models.append(model)
    return {"models": unique_models}


@app.post("/api/models/eject")
async def eject_model(request: Request):
    _require_user(request)
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    requested_model = str((body or {}).get("model") or "").strip()

    with registry.SessionLocal() as session:
        connection = _effective_llm_connection(session)

    base_url = str(connection.get("base_url") or "")
    api_type = str(connection.get("api_type") or "").lower()
    api_key = str(connection.get("api_key") or "")

    native_base = _lmstudio_native_base(base_url)
    if not native_base:
        raise HTTPException(status_code=400, detail="LLM base_url is not configured")
    if api_type not in {"openai", "lmstudio", "local"} and "1234" not in base_url:
        raise HTTPException(
            status_code=400, detail="Eject is only supported for LM Studio connections"
        )

    try:
        listing = _http_json("GET", f"{native_base}/models", token=api_key)
        models = listing.get("data") if isinstance(listing, dict) else []
        if not isinstance(models, list):
            models = []

        target_instance_id = requested_model
        if not target_instance_id:
            loaded = [
                m for m in models if isinstance(m, dict) and m.get("state") == "loaded"
            ]
            if len(loaded) == 1:
                target_instance_id = str(
                    loaded[0].get("id") or loaded[0].get("instance_id") or ""
                )
            elif len(loaded) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="Multiple models are loaded. Specify model id.",
                )

        if not target_instance_id:
            raise HTTPException(
                status_code=404, detail="No loaded model found to eject"
            )

        instance_id = target_instance_id
        exact = next(
            (
                m
                for m in models
                if isinstance(m, dict) and str(m.get("id") or "") == target_instance_id
            ),
            None,
        )
        if exact:
            instance_id = str(
                exact.get("instance_id") or exact.get("id") or target_instance_id
            )

        unloaded = _http_json(
            "POST",
            f"{native_base}/models/unload",
            payload={"instance_id": instance_id},
            token=api_key,
        )
        return {
            "ok": True,
            "requested_model": requested_model or instance_id,
            "instance_id": unloaded.get("instance_id", instance_id)
            if isinstance(unloaded, dict)
            else instance_id,
        }
    except HTTPException:
        raise
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(
            status_code=exc.code, detail=detail or "LM Studio eject failed"
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LM Studio eject failed: {exc}")


@app.get("/api/tools")
async def list_tools(request: Request):
    _require_user(request)
    with registry.SessionLocal() as session:
        global_tools = _get_app_setting(session, "tools", {})
    disabled = (
        set(global_tools.get("disabled", []))
        if isinstance(global_tools, dict)
        else set()
    )
    return {
        "tools": [
            {**tool, "enabled": tool["name"] not in disabled}
            for tool in AVAILABLE_TOOLS
        ]
    }


@app.get("/api/skills")
async def list_skills(
    request: Request,
    conversation_id: Optional[str] = None,
    prompt: Optional[str] = None,
):
    user = _require_user(request)
    workspace = None
    if conversation_id:
        with registry.SessionLocal() as session:
            _require_conversation(session, user.user_id, conversation_id)
        workspace = Path(WORKSPACE_ROOT) / "conversations" / conversation_id
    skills = (
        select_skills(prompt or "", workspace) if prompt else load_skills(workspace)
    )
    return {"skills": [skill.summary() for skill in skills]}


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

            # Deduplicate agent:lifecycle:complete events
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


def _event_row_to_progress(
    row: ConversationEventORM, task: Optional[TaskORM] = None
) -> list[dict]:
    task_id = str(row.task_id) if row.task_id else ""
    prompt = (task.input or {}).get("prompt", "") if task is not None else ""
    progress_items = _agent_event_to_progress(
        {"type": row.event_type, "data": row.payload or {}}
    )
    output: list[dict] = []
    for index, progress in enumerate(progress_items):
        content = dict(progress.get("content") or {})
        if task_id:
            content.setdefault("task_id", task_id)
        content.setdefault("conversation_id", str(row.conversation_id))
        if progress.get("name") == "agent:lifecycle:start":
            content.setdefault("request", prompt)
        output.append(
            {
                **progress,
                "id": f"event:{row.event_id}:{index}:{progress.get('name')}",
                "event_id": row.event_id,
                "task_id": task_id or None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "content": content,
            }
        )
    return output


@app.get("/api/admin/settings")
async def get_admin_settings(request: Request):
    _require_admin(request)
    with registry.SessionLocal() as session:
        llm_connection = _get_app_setting(session, "llm_connection", {})
        return {
            "llm_connection": llm_connection or _default_llm_connection(),
            "llm_connection_override": llm_connection,
            "tools": _get_app_setting(session, "tools", {"disabled": []}),
            "config_defaults": _loaded_config_defaults(),
            "config_overrides": _get_app_setting(session, "config_overrides", {}),
            "available_tools": AVAILABLE_TOOLS,
            "models": (await list_models(request))["models"],
        }


@app.put("/api/admin/settings")
async def update_admin_settings(request: Request):
    _require_admin(request)
    body = await request.json()
    with registry.SessionLocal() as session:
        if "llm_connection" in body:
            allowed = {
                key: body["llm_connection"].get(key)
                for key in [
                    "model",
                    "base_url",
                    "api_key",
                    "api_type",
                    "max_tokens",
                    "temperature",
                ]
                if body["llm_connection"].get(key) not in (None, "")
            }
            _set_app_setting(session, "llm_connection", allowed)
        if "tools" in body:
            disabled = [
                str(name)
                for name in body["tools"].get("disabled", [])
                if str(name) not in {"terminate"}
            ]
            _set_app_setting(session, "tools", {"disabled": disabled})
        if "config_overrides" in body:
            overrides = body["config_overrides"]
            if not isinstance(overrides, dict):
                raise HTTPException(
                    status_code=400, detail="config_overrides must be an object"
                )
            _set_app_setting(session, "config_overrides", overrides)
        session.commit()
        llm_connection = _get_app_setting(session, "llm_connection", {})
        return {
            "llm_connection": llm_connection or _default_llm_connection(),
            "llm_connection_override": llm_connection,
            "tools": _get_app_setting(session, "tools", {"disabled": []}),
            "config_defaults": _loaded_config_defaults(),
            "config_overrides": _get_app_setting(session, "config_overrides", {}),
            "available_tools": AVAILABLE_TOOLS,
        }


@app.get("/api/conversations")
async def list_conversations(request: Request):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        from sqlalchemy import desc

        _prune_orphan_conversation_sandboxes(session)

        conversations = (
            session.query(ConversationORM)
            .filter(ConversationORM.user_id == user.user_id)
            .order_by(
                desc(ConversationORM.updated_at), desc(ConversationORM.created_at)
            )
            .all()
        )
        if not conversations:
            conversations = [_ensure_default_conversation(session, user.user_id)]
            session.commit()
        return {
            "conversations": [
                _conversation_to_dict(session, conversation)
                for conversation in conversations
            ]
        }


@app.post("/api/conversations")
async def create_conversation(request: Request):
    user = _require_user(request)
    title = "New conversation"
    model = None
    try:
        body = await request.json()
        title = str(body.get("title") or title).strip() or title
        model = str(body.get("model") or "").strip() or None
    except Exception:
        pass
    with registry.SessionLocal() as session:
        conversation = ConversationORM(
            user_id=user.user_id, title=title[:120], model=model
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return _conversation_to_dict(session, conversation)


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        return _conversation_to_dict(session, conversation)


@app.get("/api/conversations/{conversation_id}/tasks")
async def get_conversation_tasks(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        tasks = _conversation_tasks(
            session, str(conversation.conversation_id), ascending=True
        )
        return {"tasks": [_task_to_dict(task) for task in tasks]}


@app.get("/api/conversations/{conversation_id}/events/history")
async def get_conversation_event_history(
    request: Request,
    conversation_id: str,
    limit: int = 500,
    before_event_id: Optional[int] = None,
    kind: Optional[str] = None,
):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        tasks = _conversation_tasks(
            session, str(conversation.conversation_id), ascending=True
        )
        conversation_payload = _conversation_to_dict(session, conversation)
        task_payloads = [_task_to_dict(task) for task in tasks]
        task_by_id = {str(task.task_id): task for task in tasks}

        query = (
            session.query(ConversationEventORM)
            .filter(
                ConversationEventORM.conversation_id == conversation.conversation_id
            )
            .order_by(ConversationEventORM.event_id.asc())
        )
        if before_event_id is not None:
            query = query.filter(ConversationEventORM.event_id < before_event_id)
        if kind:
            query = query.filter(ConversationEventORM.event_type == kind)
        rows = query.limit(max(1, min(limit, 2000))).all()

    events: list[dict] = []
    if rows:
        has_emitted_complete_by_task: set[str] = set()
        for row in rows:
            task_key = str(row.task_id) if row.task_id else ""
            for event in _event_row_to_progress(row, task_by_id.get(task_key)):
                is_complete = event.get("name") == "agent:lifecycle:complete"
                if is_complete and task_key in has_emitted_complete_by_task:
                    continue
                if is_complete and task_key:
                    has_emitted_complete_by_task.add(task_key)
                events.append(event)
    else:
        for task in tasks:
            events.extend(await _task_stream_progress(task))

    return {
        "conversation": conversation_payload,
        "tasks": task_payloads,
        "events": events,
    }


@app.post("/api/conversations/{conversation_id}/obsidian/import")
async def import_obsidian_context(request: Request, conversation_id: str):
    """Import Obsidian notes and build a wikilink graph for this conversation."""
    user = _require_user(request)
    body = await request.json()
    notes = body.get("notes") or []
    if not isinstance(notes, list) or not notes:
        raise HTTPException(status_code=400, detail="notes[] is required")

    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        cid = conversation.conversation_id
        existing = (
            session.query(ObsidianNoteORM)
            .filter(ObsidianNoteORM.conversation_id == cid)
            .all()
        )
        by_path = {note.path: note for note in existing}
        by_title = {note.title: note for note in existing}

        upserted: list[ObsidianNoteORM] = []
        for raw in notes:
            if not isinstance(raw, dict):
                continue
            path = str(raw.get("path") or raw.get("title") or "").strip()
            title = str(raw.get("title") or Path(path).stem or "").strip()
            content = str(raw.get("content") or "")
            tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            if not path or not title:
                continue
            note = by_path.get(path)
            if note is None:
                note = ObsidianNoteORM(
                    conversation_id=cid,
                    path=path,
                    title=title,
                    content=content,
                    tags=tags,
                    meta=meta,
                )
                session.add(note)
            else:
                note.title = title
                note.content = content
                note.tags = tags
                note.meta = meta
            upserted.append(note)
            by_path[path] = note
            by_title[title] = note

        session.flush()

        session.query(ObsidianEdgeORM).filter(
            ObsidianEdgeORM.conversation_id == cid
        ).delete()

        edges_created = 0
        for note in upserted:
            for target_name in _extract_wikilinks(note.content):
                target = by_title.get(target_name) or by_path.get(target_name)
                if target is None:
                    continue
                session.add(
                    ObsidianEdgeORM(
                        conversation_id=cid,
                        source_note_id=note.note_id,
                        target_note_id=target.note_id,
                        relation="wikilink",
                    )
                )
                edges_created += 1

        conversation.updated_at = _now()
        session.commit()

        payload = _obsidian_graph_payload(session, conversation)
        return {
            "conversation_id": conversation_id,
            "imported_notes": len(upserted),
            "edges_created": edges_created,
            "graph": payload,
        }


@app.get("/api/conversations/{conversation_id}/obsidian/graph")
async def get_obsidian_graph(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        return {
            "conversation_id": conversation_id,
            **_obsidian_graph_payload(session, conversation),
        }


@app.get("/api/conversations/{conversation_id}/obsidian/context")
async def get_obsidian_context(request: Request, conversation_id: str, limit: int = 8):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        notes = (
            session.query(ObsidianNoteORM)
            .filter(ObsidianNoteORM.conversation_id == conversation.conversation_id)
            .order_by(ObsidianNoteORM.updated_at.desc())
            .limit(max(1, min(limit, 30)))
            .all()
        )
    items = []
    for note in notes:
        content = (note.content or "").strip()
        if len(content) > 900:
            content = content[:900] + "..."
        items.append(
            {
                "id": str(note.note_id),
                "path": note.path,
                "title": note.title,
                "tags": note.tags or [],
                "content": content,
            }
        )
    return {"conversation_id": conversation_id, "notes": items, "count": len(items)}


@app.get("/api/conversations/{conversation_id}/events/count")
async def get_conversation_event_count(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        counts = (
            session.query(
                ConversationEventORM.event_type,
                func.count(ConversationEventORM.event_id),
            )
            .filter(
                ConversationEventORM.conversation_id == conversation.conversation_id
            )
            .group_by(ConversationEventORM.event_type)
            .all()
        )
        return {
            "conversation_id": conversation_id,
            "total": sum(int(count) for _, count in counts),
            "by_type": {event_type: int(count) for event_type, count in counts},
        }


@app.get("/api/conversations/{conversation_id}/runtime")
async def get_conversation_runtime(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)

    sandbox = _conversation_sandbox(conversation_id)
    try:
        sandbox_status, processes, containers, urls = await asyncio.gather(
            sandbox.status(),
            sandbox.list_processes(),
            sandbox.list_docker_containers(),
            sandbox.list_exposed_urls(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    killable_processes = [
        item
        for item in processes
        if not item.get("protected") and not item.get("zombie")
    ]
    visible_containers = [item for item in containers if not item.get("protected")]
    killable_containers = visible_containers
    return {
        "conversation_id": conversation_id,
        "sandbox": sandbox_status,
        "status": "running" if killable_processes or killable_containers else "idle",
        "processes": processes,
        "containers": visible_containers,
        "urls": urls,
        "hidden_system_containers": len(containers) - len(visible_containers),
        "running_count": len(killable_processes) + len(killable_containers),
    }


@app.get("/api/conversations/{conversation_id}/state")
async def get_conversation_state(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        conversation_payload = _conversation_to_dict(session, conversation)
    sandbox_status = await _conversation_sandbox(conversation_id).status()
    return {
        "conversation_id": conversation_id,
        "state": conversation_payload.get("state", "idle"),
        "latest_status": conversation_payload.get("latest_status"),
        "sandbox": sandbox_status,
    }


@app.post("/api/conversations/{conversation_id}/sandbox/start")
async def start_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    sandbox = await _conversation_sandbox(conversation_id).ensure()
    return {"conversation_id": conversation_id, "sandbox": await sandbox.status()}


@app.post("/api/conversations/{conversation_id}/sandbox/pause")
async def pause_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    sandbox = _conversation_sandbox(conversation_id)
    await sandbox.pause()
    return {"conversation_id": conversation_id, "sandbox": await sandbox.status()}


@app.post("/api/conversations/{conversation_id}/sandbox/resume")
async def resume_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    sandbox = _conversation_sandbox(conversation_id)
    await sandbox.resume()
    return {"conversation_id": conversation_id, "sandbox": await sandbox.status()}


@app.delete("/api/conversations/{conversation_id}/sandbox")
async def delete_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    await _conversation_sandbox(conversation_id).delete()
    return {"conversation_id": conversation_id, "deleted": True}


@app.post("/api/conversations/{conversation_id}/runtime/processes/{pid}/kill")
async def kill_conversation_process(request: Request, conversation_id: str, pid: int):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    signal = str(body.get("signal") or "TERM")
    try:
        await _conversation_sandbox(conversation_id).kill_process(pid, signal=signal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"conversation_id": conversation_id, "pid": pid, "killed": True}


@app.post("/api/conversations/{conversation_id}/runtime/containers/{container_id}/stop")
async def stop_conversation_container(
    request: Request, conversation_id: str, container_id: str
):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)

    try:
        await _conversation_sandbox(conversation_id).stop_docker_container(container_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "conversation_id": conversation_id,
        "container_id": container_id,
        "stopped": True,
    }


@app.put("/api/conversations/{conversation_id}/settings")
async def update_conversation_settings(request: Request, conversation_id: str):
    user = _require_user(request)
    body = await request.json()
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        settings = dict(conversation.settings or {})
        if "model" in body:
            model = str(body.get("model") or "").strip()
            conversation.model = model or None
        if "disabled_tools" in body:
            settings["disabled_tools"] = [
                str(name)
                for name in body.get("disabled_tools", [])
                if str(name) != "terminate"
            ]
        if "requested_context_window" in body:
            raw_window = body.get("requested_context_window")
            if raw_window in (None, "", 0):
                settings.pop("requested_context_window", None)
            else:
                try:
                    value = int(raw_window)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail="requested_context_window must be a positive integer",
                    )
                if value <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail="requested_context_window must be a positive integer",
                    )
                settings["requested_context_window"] = value
        if "auto_context_compress" in body:
            settings["auto_context_compress"] = bool(body.get("auto_context_compress"))
        conversation.settings = settings
        conversation.updated_at = _now()
        session.commit()
        session.refresh(conversation)
        return _conversation_to_dict(session, conversation)


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(request: Request, conversation_id: str):
    user = _require_user(request)
    import redis as redis_lib

    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        tasks = (
            session.query(TaskORM)
            .filter(
                TaskORM.input["conversation_id"].astext
                == str(conversation.conversation_id)
            )
            .all()
        )
        task_ids = [str(task.task_id) for task in tasks]
        session.query(ConversationEventORM).filter(
            ConversationEventORM.conversation_id == conversation.conversation_id
        ).delete(synchronize_session=False)
        for task in tasks:
            session.delete(task)
        session.delete(conversation)
        session.commit()

    try:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True)
        for tid in task_ids:
            r.delete(f"task:{tid}:stream")
            r.delete(f"task:{tid}:inbox")
        r.close()
    except Exception:
        pass

    workspace_path = os.path.join(WORKSPACE_ROOT, "conversations", conversation_id)
    shutil.rmtree(workspace_path, ignore_errors=True)

    try:
        import docker

        client = docker.from_env()
        container = client.containers.get(f"openmanus_sandbox_{conversation_id}")
        container.remove(force=True)
    except Exception:
        pass

    return {"id": conversation_id, "deleted": True}


# ---------------------------------------------------------------------------
# Task list (sidebar history)
# ---------------------------------------------------------------------------


@app.get("/api/tasks")
async def list_tasks(request: Request, page: int = 1, pageSize: int = 30):
    """List tasks from the database for the sidebar."""
    user = _require_user(request)
    with registry.SessionLocal() as session:
        from sqlalchemy import desc

        conversation_ids = [
            str(row[0])
            for row in session.query(ConversationORM.conversation_id)
            .filter(ConversationORM.user_id == user.user_id)
            .all()
        ]
        offset = (page - 1) * pageSize
        orms = (
            session.query(TaskORM)
            .filter(TaskORM.input["conversation_id"].astext.in_(conversation_ids))
            .order_by(desc(TaskORM.created_at))
            .offset(offset)
            .limit(pageSize)
            .all()
        )

        # The hook does: res.data?.tasks  so we must nest it under "tasks"
        tasks = [
            {
                "id": str(orm.task_id),
                "task_id": str(orm.task_id),
                "status": orm.status,
                "conversation_id": _conversation_id_for(orm),
                "created_at": orm.created_at.isoformat() if orm.created_at else None,
                # The sidebar uses 'request' as the display label
                "request": (orm.input or {}).get("prompt", "Untitled task"),
            }
            for orm in orms
        ]

        return {"tasks": tasks, "total": len(tasks)}


# ---------------------------------------------------------------------------
# SSE event stream
# ---------------------------------------------------------------------------


@app.get("/api/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str):
    """SSE endpoint: read from Redis Stream so events are never missed."""
    user = _require_user(request)

    # Check current task status from DB before opening the stream
    with registry.SessionLocal() as session:
        orm = session.get(TaskORM, task_id)
        if orm is None or not _task_belongs_to_user(session, orm, user.user_id):
            raise HTTPException(status_code=404, detail="Task not found")
        task_is_done = orm is not None and orm.status in (
            "COMPLETED",
            "FAILED",
            "INTERRUPTED",
        )
        task_prompt = (orm.input or {}).get("prompt", "")
        task_conversation_id = _conversation_id_for(orm)

    async def event_generator():
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        stream_key = f"task:{task_id}:stream"
        last_id = "0"  # start from the very beginning of the stream
        empty_polls = 0
        has_emitted_complete = False

        try:
            while True:
                if await request.is_disconnected():
                    break

                # XREAD blocks up to 1 second waiting for new entries
                results = await redis.xread({stream_key: last_id}, count=10, block=1000)

                if not results:
                    if task_is_done:
                        # Task is completed in DB but no stream data exists
                        # (task ran before Redis Streams were added, or stream expired).
                        # Send synthetic lifecycle events so the UI can close cleanly.
                        empty_polls += 1
                        if empty_polls >= 2:  # wait 2 seconds then synthesize
                            yield {
                                "data": json.dumps(
                                    {
                                        "type": "progress",
                                        "name": "agent:lifecycle:start",
                                        "content": {},
                                    }
                                )
                            }
                            yield {
                                "data": json.dumps(
                                    {
                                        "type": "progress",
                                        "name": "agent:lifecycle:complete",
                                        "content": {
                                            "message": "Task already completed"
                                        },
                                    }
                                )
                            }
                            # Grace period: let browser receive and process before we close
                            await asyncio.sleep(3)
                            return
                    # Active task with no events yet — send keep-alive ping
                    yield {"data": json.dumps({"type": "ping"})}
                    continue

                # Reset empty poll counter whenever we get events
                empty_polls = 0

                done = False
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        event_type = fields.get("type", "")
                        try:
                            data = json.loads(fields.get("data", "{}"))
                        except Exception:
                            data = {}

                        progress_list = _agent_event_to_progress(
                            {"type": event_type, "data": data}
                        )
                        for progress_index, progress in enumerate(progress_list):
                            content = dict(progress.get("content") or {})
                            content.setdefault("task_id", task_id)
                            content.setdefault("conversation_id", task_conversation_id)
                            if progress.get("name") == "agent:lifecycle:start":
                                content.setdefault("request", task_prompt)
                            progress["content"] = content

                            is_complete = (
                                progress.get("name") == "agent:lifecycle:complete"
                            )
                            if is_complete and has_emitted_complete:
                                continue
                            if is_complete:
                                has_emitted_complete = True

                            progress[
                                "id"
                            ] = f"{task_id}:{msg_id}:{progress_index}:{progress.get('name')}"
                            progress["task_id"] = task_id
                            yield {"data": json.dumps(progress)}
                            if progress["name"] in (
                                "agent:lifecycle:complete",
                                "agent:lifecycle:terminated",
                                "agent:lifecycle:step:error",
                            ):
                                done = True
                                break
                        if done:
                            break
                    if done:
                        break

                if done:
                    # Grace period: give the browser time to receive the completion
                    # event and call eventSource.close() itself, BEFORE we close the
                    # TCP connection — this prevents the browser firing onerror and
                    # showing the "connection failed" toast.
                    await asyncio.sleep(3)
                    return

        finally:
            await redis.aclose()

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Create task — frontend sends multipart/form-data
# ---------------------------------------------------------------------------


@app.post("/api/tasks")
async def create_task(
    request: Request,
    prompt: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    conversation_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
):
    """Create a new task, or queue a follow-up run on an existing task."""
    user = _require_user(request)

    if task_id:
        # Check if this task already exists (follow-up message in a conversation)
        with registry.SessionLocal() as session:
            existing_orm = session.get(TaskORM, task_id)
            if existing_orm is not None and not _task_belongs_to_user(
                session, existing_orm, user.user_id
            ):
                raise HTTPException(status_code=404, detail="Task not found")

        if existing_orm is not None:
            conversation_id = _conversation_id_for(existing_orm)
            with registry.SessionLocal() as session:
                conversation = session.get(
                    ConversationORM, uuid.UUID(str(conversation_id))
                )
                conversation_settings = (
                    conversation.settings if conversation is not None else {}
                )
                run_model = model or (
                    conversation.model if conversation is not None else None
                )
                disabled_tools = list(
                    (conversation_settings or {}).get("disabled_tools", [])
                )
                requested_context_window = (conversation_settings or {}).get(
                    "requested_context_window"
                )
                auto_context_compress = (conversation_settings or {}).get(
                    "auto_context_compress", True
                )
                if conversation is not None:
                    conversation.updated_at = _now()
                    session.commit()
            if existing_orm.status in TERMINAL_STATUSES:
                task = registry.create_task(
                    input=_task_input(
                        prompt,
                        conversation_id,
                        parent_task_id=task_id,
                        model=run_model,
                        disabled_tools=disabled_tools,
                        requested_context_window=requested_context_window,
                        auto_context_compress=auto_context_compress,
                    )
                )
                task.status = TaskStatus.CREATED
                run_task.apply_async(args=[task.id, prompt], task_id=task.id)
                return {
                    "task_id": task.id,
                    "status": str(task.status),
                    "conversation_id": conversation_id,
                    "data": {
                        "task_id": task.id,
                        "status": str(task.status),
                        "conversation_id": conversation_id,
                    },
                }

            # Task exists but is NOT in a terminal status.
            # We must not queue another Celery task for the exact same task ID,
            # otherwise two agents will run concurrently and exhaust the server.
            raise HTTPException(
                status_code=409,
                detail="Task is currently running. Please wait for it to finish or interrupt it before sending a new prompt.",
            )

    # New task — create a DB record and queue it. When the caller did not
    # provide an id, use one generated id for both the first task and the
    # conversation so the workspace path stays easy to reason about.
    new_task_id = str(task_id or uuid.uuid4())
    with registry.SessionLocal() as session:
        if conversation_id:
            conversation = _require_conversation(session, user.user_id, conversation_id)
        else:
            conversation = _ensure_default_conversation(session, user.user_id)
        if prompt and conversation.title == "New conversation":
            conversation.title = (
                prompt.strip().splitlines()[0][:80] or conversation.title
            )
        if model:
            conversation.model = model
        run_model = model or conversation.model
        disabled_tools = list((conversation.settings or {}).get("disabled_tools", []))
        requested_context_window = (conversation.settings or {}).get(
            "requested_context_window"
        )
        auto_context_compress = (conversation.settings or {}).get(
            "auto_context_compress", True
        )
        conversation.updated_at = _now()
        session.commit()
        conversation_id = str(conversation.conversation_id)

    task = registry.create_task(
        task_id=new_task_id,
        input=_task_input(
            prompt,
            conversation_id,
            model=run_model,
            disabled_tools=disabled_tools,
            requested_context_window=requested_context_window,
            auto_context_compress=auto_context_compress,
        ),
    )
    task.status = TaskStatus.CREATED
    run_task.apply_async(args=[task.id, prompt], task_id=task.id)

    return {
        "task_id": task.id,
        "status": str(task.status),
        "conversation_id": conversation_id,
        "data": {
            "task_id": task.id,
            "status": str(task.status),
            "conversation_id": conversation_id,
        },
    }


@app.post("/api/tasks/{task_id}/message")
async def send_task_message(request: Request, task_id: str):
    """Queue a user message for a running task to consume."""
    user = _require_user(request)
    with registry.SessionLocal() as session:
        orm = session.get(TaskORM, task_id)
        if orm is None or not _task_belongs_to_user(session, orm, user.user_id):
            raise HTTPException(status_code=404, detail="Task not found")

    message = ""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            message = str(body.get("message") or body.get("prompt") or "")
        except Exception:
            message = ""
    else:
        form = await request.form()
        message = str(form.get("message") or form.get("prompt") or "")

    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.rpush(f"task:{task_id}:inbox", message)
    finally:
        await redis.aclose()

    return {"id": task_id, "queued": True}


@app.post("/api/conversations/{conversation_id}/messages")
async def send_conversation_message(request: Request, conversation_id: str):
    """OpenHands-style pending message endpoint.

    If the conversation has a running task, queue the message into that task inbox.
    Otherwise create a new task in the same conversation.
    """
    user = _require_user(request)
    body = await request.json()
    message = str(body.get("message") or body.get("prompt") or "").strip()
    requested_model = str(body.get("model") or "").strip() or None
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        active_task = next(
            (
                task
                for task in reversed(
                    _conversation_tasks(
                        session, str(conversation.conversation_id), ascending=True
                    )
                )
                if task.status not in TERMINAL_STATUSES
            ),
            None,
        )
        if active_task is not None:
            active_task_id = str(active_task.task_id)
        else:
            active_task_id = None
            run_model = requested_model or conversation.model
            if requested_model:
                conversation.model = requested_model
            disabled_tools = list(
                (conversation.settings or {}).get("disabled_tools", [])
            )
            requested_context_window = (conversation.settings or {}).get(
                "requested_context_window"
            )
            auto_context_compress = (conversation.settings or {}).get(
                "auto_context_compress", True
            )
            conversation.updated_at = _now()
            session.commit()

    if active_task_id:
        redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            await redis.rpush(f"task:{active_task_id}:inbox", message)
        finally:
            await redis.aclose()
        return {
            "conversation_id": conversation_id,
            "task_id": active_task_id,
            "queued": True,
            "created_task": False,
        }

    task = registry.create_task(
        input=_task_input(
            message,
            conversation_id,
            model=run_model,
            disabled_tools=disabled_tools,
            requested_context_window=requested_context_window,
            auto_context_compress=auto_context_compress,
        )
    )
    task.status = TaskStatus.CREATED
    run_task.apply_async(args=[task.id, message], task_id=task.id)
    return {
        "conversation_id": conversation_id,
        "task_id": task.id,
        "queued": True,
        "created_task": True,
    }


# ---------------------------------------------------------------------------
# Single task detail
# ---------------------------------------------------------------------------


@app.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        orm = session.get(TaskORM, task_id)
        if orm is None or not _task_belongs_to_user(session, orm, user.user_id):
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "id": str(orm.task_id),
            "status": orm.status,
            "result": orm.result,
            "conversation_id": _conversation_id_for(orm),
        }


# ---------------------------------------------------------------------------
# Interrupt / terminate
# ---------------------------------------------------------------------------


@app.post("/api/tasks/{task_id}/interrupt")
@app.post("/api/tasks/{task_id}/terminate")
async def interrupt_task(request: Request, task_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        orm = session.get(TaskORM, task_id)
        if orm is None or not _task_belongs_to_user(session, orm, user.user_id):
            raise HTTPException(status_code=404, detail="Task not found")
        orm.status = TaskStatus.INTERRUPTED.value
        session.commit()
    registry.interrupt_task(task_id)
    try:
        celery_app.control.revoke(task_id, terminate=True)
    except Exception:
        pass
    return {"id": task_id, "status": TaskStatus.INTERRUPTED.value}


@app.delete("/api/tasks/{task_id}")
async def delete_task(request: Request, task_id: str):
    """Delete a task and its Redis stream."""
    import redis as redis_lib

    user = _require_user(request)
    with registry.SessionLocal() as session:
        orm = session.get(TaskORM, task_id)
        if orm is None or not _task_belongs_to_user(session, orm, user.user_id):
            raise HTTPException(status_code=404, detail="Task not found")
        session.query(ConversationEventORM).filter(
            ConversationEventORM.task_id == orm.task_id
        ).delete(synchronize_session=False)
        session.delete(orm)
        session.commit()
    try:
        r = redis_lib.from_url(REDIS_URL, decode_responses=True)
        r.delete(f"task:{task_id}:stream")
        r.delete(f"task:{task_id}:inbox")
        r.close()
    except Exception:
        pass
    return {"id": task_id, "deleted": True}


@app.get("/api/workspace/{path:path}")
async def get_workspace(path: str = ""):
    """List files in /app/workspace or return file content."""
    import datetime
    import os

    from fastapi.responses import FileResponse

    base = "/app/workspace"
    target = os.path.normpath(os.path.join(base, path)) if path else base

    # Security: block path traversal
    if not target.startswith(base):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not os.path.exists(target):
        return []  # Empty workspace

    if os.path.isfile(target):
        return FileResponse(target, filename=os.path.basename(target))

    entries = []
    try:
        for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name)):
            s = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": s.st_size,
                    "modifiedTime": datetime.datetime.fromtimestamp(
                        s.st_mtime
                    ).isoformat(),
                }
            )
    except PermissionError:
        pass
    return entries


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":  # pragma: no cover
    uvicorn.run(app, host="0.0.0.0", port=8000)
