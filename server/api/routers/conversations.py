import os
import uuid
import shutil
import logging
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import func

from app.config import config
from app.memory.agentmemory import agentmemory
from server.models import (
    ConversationORM,
    ConversationEventORM,
    ObsidianNoteORM,
    ObsidianEdgeORM,
    TaskORM,
)
from server.api.deps import (
    registry,
    _require_user,
    _require_conversation,
    _get_app_setting,
    _set_app_setting,
    _ensure_default_conversation,
    _conversation_id_for,
    _conversation_sandbox,
    _conversation_tasks,
    _task_stream_progress,
    _now,
    CONVERSATION_STATES,
    TERMINAL_STATUSES,
    WORKSPACE_ROOT,
    REDIS_URL,
)
from server.api.routers.models_llm import (
    _effective_llm_connection,
    _merge_conversation_llm_connection,
    _llm_connection_health,
)
from server.api.event_mapping import _agent_event_to_progress

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conversations", tags=["conversations"])

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

    received_context_window = None
    try:
        persisted_received = settings.get("received_context_window")
        if persisted_received not in (None, ""):
            received_context_window = int(persisted_received)

        selected_model = str(conversation.model or "").strip() or None
        connection = _get_app_setting(session, "llm_connection", {})
        if isinstance(connection, dict):
            conn_model = str(connection.get("model") or "").strip() or None
            conn_window = connection.get("max_input_tokens")
            if conn_window not in (None, "") and (
                selected_model is None or selected_model == conn_model
            ):
                received_context_window = int(conn_window)

        if (
            received_context_window in (None, 0)
            and selected_model
            and isinstance(config.llm, dict)
        ):
            for llm_settings in config.llm.values():
                if getattr(llm_settings, "model", None) == selected_model:
                    value = getattr(llm_settings, "max_input_tokens", None)
                    if value not in (None, 0):
                        received_context_window = int(value)
                        break

        if received_context_window in (None, 0):
            default_llm = (
                config.llm.get("default") if isinstance(config.llm, dict) else None
            )
            value = (
                getattr(default_llm, "max_input_tokens", None) if default_llm else None
            )
            if value not in (None, 0):
                received_context_window = int(value)
    except Exception:
        received_context_window = None

    latest_context = {
        "requested_window": requested_context_window,
        "received_window": received_context_window,
        "received_window_source": settings.get("received_context_window_source")
        or ("fallback_inferred" if received_context_window else None),
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

def _truncate_text(value: str, limit: int = 8000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"

def _compact_history_value(value, *, text_limit: int = 8000):
    if isinstance(value, str):
        return _truncate_text(value, text_limit)
    if isinstance(value, list):
        return [
            _compact_history_value(item, text_limit=text_limit) for item in value[:200]
        ]
    if isinstance(value, dict):
        compacted = {}
        for key, item in value.items():
            key_s = str(key)
            if key_s in {"screenshot", "base64_image", "image"} and isinstance(
                item, str
            ):
                compacted[key_s] = f"[omitted base64 payload: {len(item)} chars]"
                continue
            compacted[key_s] = _compact_history_value(item, text_limit=text_limit)
        return compacted
    return value

def _compact_history_event(event: dict) -> dict:
    compacted = dict(event)
    compacted["content"] = _compact_history_value(event.get("content") or {})
    return compacted

def _obsidian_health(session, conversation_id: Optional[str] = None) -> dict:
    payload = {
        "enabled": True,
        "available": True,
        "live": False,
        "reason": "No notes found yet",
        "note_count": 0,
    }
    try:
        query = session.query(func.count(ObsidianNoteORM.note_id))
        if conversation_id:
            query = query.filter(
                ObsidianNoteORM.conversation_id == uuid.UUID(str(conversation_id))
            )
        count = int(query.scalar() or 0)
        payload["note_count"] = count
        if count > 0:
            payload["live"] = True
            payload["reason"] = "Notes indexed"
    except Exception as exc:
        payload["available"] = False
        payload["live"] = False
        payload["reason"] = f"DB error: {exc}"
    return payload

def _agentmemory_health(conversation_id: Optional[str] = None) -> dict:
    vec_health = agentmemory.get_vector_health()
    payload = {
        "enabled": bool(config.agentmemory.enabled),
        "available": False,
        "live": False,
        "reason": "Disabled",
        "base_url": "local_sqlite",
        "project": config.agentmemory.project,
        **vec_health,
    }
    if not config.agentmemory.enabled:
        return payload

    try:
        with agentmemory._get_conn() as conn:
            conn.execute("SELECT 1 FROM memories LIMIT 1")
        payload["available"] = True
        payload["live"] = True
        payload["reason"] = "Live (Local SQLite FTS5)"
    except Exception as exc:
        payload["available"] = False
        payload["live"] = False
        payload["reason"] = f"SQLite failed: {exc}"

    if payload["live"] and conversation_id:
        try:
            hits = agentmemory.search(
                conversation_id=conversation_id, query="summary", limit=1
            )
            payload["conversation_hits"] = len(hits)
        except Exception:
            payload["conversation_hits"] = 0

    payload.update(agentmemory.get_vector_health())
    return payload

def _prune_orphan_conversation_sandboxes(session) -> int:
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

@router.get("")
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

@router.post("")
async def create_conversation(request: Request):
    user = _require_user(request)
    title = "New conversation"
    model = None
    llm_connection = None
    try:
        body = await request.json()
        title = str(body.get("title") or title).strip() or title
        model = str(body.get("model") or "").strip() or None
        if isinstance(body.get("llm_connection"), dict):
            llm_connection = body.get("llm_connection")
    except Exception:
        pass
    with registry.SessionLocal() as session:
        settings = {"llm_connection": llm_connection} if llm_connection else {}
        conversation = ConversationORM(
            user_id=user.user_id, title=title[:120], model=model, settings=settings
        )
        session.add(conversation)
        if llm_connection:
            _set_app_setting(session, "llm_connection", llm_connection)
        session.commit()
        session.refresh(conversation)
        return _conversation_to_dict(session, conversation)

@router.get("/{conversation_id}")
async def get_conversation(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        return _conversation_to_dict(session, conversation)

@router.get("/{conversation_id}/tasks")
async def get_conversation_tasks(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        tasks = _conversation_tasks(
            session, str(conversation.conversation_id), ascending=True
        )
        return {"tasks": [_task_to_dict(task) for task in tasks]}

@router.get("/{conversation_id}/events/history")
async def get_conversation_event_history(
    request: Request,
    conversation_id: str,
    limit: int = 160,
    before_event_id: Optional[int] = None,
    kind: Optional[str] = None,
):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        tasks = _conversation_tasks(
            session, str(conversation.conversation_id), ascending=True
        )
        now = _now()
        stale_seconds = 300
        for task in tasks:
            if str(task.status) != "CREATED":
                continue
            created_at = task.created_at or now
            age = (now - created_at).total_seconds()
            if age <= stale_seconds:
                continue
            task.status = "FAILED"
            task.result = {
                "error": "Stale task recovered while loading conversation history."
            }
            session.commit()
        tasks = _conversation_tasks(
            session, str(conversation.conversation_id), ascending=True
        )
        conversation_payload = _conversation_to_dict(session, conversation)
        task_payloads = [_task_to_dict(task) for task in tasks]
        task_by_id = {str(task.task_id): task for task in tasks}

        query = session.query(ConversationEventORM).filter(
            ConversationEventORM.conversation_id == conversation.conversation_id
        )
        if before_event_id is not None:
            query = query.filter(ConversationEventORM.event_id < before_event_id)
        if kind:
            query = query.filter(ConversationEventORM.event_type == kind)
        page_size = max(1, min(limit, 2000))
        rows = (
            query.order_by(ConversationEventORM.event_id.desc()).limit(page_size).all()
        )
        rows = list(reversed(rows))

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
                events.append(_compact_history_event(event))
    else:
        for task in tasks:
            events.extend(
                [
                    _compact_history_event(item)
                    for item in await _task_stream_progress(task)
                ]
            )

    next_before_event_id = rows[0].event_id if rows else None
    return {
        "conversation": conversation_payload,
        "tasks": task_payloads,
        "events": events,
        "pagination": {
            "limit": page_size,
            "next_before_event_id": next_before_event_id,
            "has_more": len(rows) == page_size and next_before_event_id is not None,
        },
    }

@router.get("/{conversation_id}/events/count")
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

@router.get("/{conversation_id}/runtime")
async def get_conversation_runtime(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)

    sandbox = _conversation_sandbox(conversation_id)
    try:
        sandbox_status = await sandbox.status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    status_value = str((sandbox_status or {}).get("status") or "").lower()
    if status_value in {"paused", "exited", "dead", "missing", ""}:
        return {
            "conversation_id": conversation_id,
            "sandbox": sandbox_status,
            "status": "paused" if status_value == "paused" else "idle",
            "processes": [],
            "containers": [],
            "urls": [],
            "hidden_system_containers": 0,
            "running_count": 0,
        }

    try:
        processes, containers, urls = await asyncio.gather(
            sandbox.list_processes(),
            sandbox.list_docker_containers(),
            sandbox.list_exposed_urls(),
        )
    except Exception:
        processes, containers, urls = [], [], []

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
        "agentmemory": _agentmemory_health(conversation_id),
    }

@router.get("/{conversation_id}/state")
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

@router.get("/{conversation_id}/integrations/health")
async def get_conversation_integrations_health(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
        return {
            "conversation_id": conversation_id,
            "agentmemory": _agentmemory_health(conversation_id),
            "obsidian": _obsidian_health(session, conversation_id),
            "llm_connection": _llm_connection_health(session),
        }

@router.put("/{conversation_id}/settings")
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
        if "disabled_skills" in body:
            settings["disabled_skills"] = [
                str(name).strip()
                for name in body.get("disabled_skills", [])
                if str(name).strip()
            ]
        if "enable_vendor_skills" in body:
            settings["enable_vendor_skills"] = bool(body.get("enable_vendor_skills"))
        if "pinned_skills" in body:
            settings["pinned_skills"] = [
                str(name).strip()
                for name in body.get("pinned_skills", [])
                if str(name).strip()
            ]
        if "identity_notes" in body:
            settings["identity_notes"] = str(body.get("identity_notes") or "").strip()
        if "auto_skill_curator" in body:
            settings["auto_skill_curator"] = bool(body.get("auto_skill_curator"))
        if "performance_mode" in body:
            settings["performance_mode"] = bool(body.get("performance_mode"))
        if isinstance(body.get("llm_connection"), dict):
            settings["llm_connection"] = body.get("llm_connection")
            _set_app_setting(session, "llm_connection", body.get("llm_connection"))
        conversation.settings = settings
        conversation.updated_at = _now()
        session.commit()
        session.refresh(conversation)
        return _conversation_to_dict(session, conversation)

@router.delete("/{conversation_id}")
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
        session.query(ObsidianEdgeORM).filter(
            ObsidianEdgeORM.conversation_id == conversation.conversation_id
        ).delete(synchronize_session=False)
        session.query(ObsidianNoteORM).filter(
            ObsidianNoteORM.conversation_id == conversation.conversation_id
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
