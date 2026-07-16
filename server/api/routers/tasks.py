import asyncio
import json
import uuid
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Form, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from core.task import TaskStatus
from server.api.deps import (
    REDIS_URL,
    TERMINAL_STATUSES,
    _conversation_id_for,
    _conversation_tasks,
    _ensure_default_conversation,
    _now,
    _require_conversation,
    _require_user,
    _set_app_setting,
    _task_belongs_to_user,
    registry,
)
from server.api.event_mapping import _agent_event_to_progress
from server.api.routers.models_llm import (
    _effective_llm_connection,
    _merge_conversation_llm_connection,
)
from server.celery_app import celery_app
from server.models import ConversationEventORM, ConversationORM, TaskORM
from server.tasks import run_task


router = APIRouter(prefix="", tags=["tasks"])


def _task_input(
    prompt: Optional[str],
    conversation_id: str,
    parent_task_id: Optional[str] = None,
    model: Optional[str] = None,
    disabled_tools: Optional[list[str]] = None,
    requested_context_window: Optional[int] = None,
    auto_context_compress: Optional[bool] = None,
    disabled_skills: Optional[list[str]] = None,
    enable_vendor_skills: Optional[bool] = None,
    pinned_skills: Optional[list[str]] = None,
    identity_notes: Optional[str] = None,
) -> dict:
    data = {"conversation_id": conversation_id}
    if not requested_context_window:
        try:
            with registry.SessionLocal() as session:
                connection = _effective_llm_connection(session)
            calibrated_context = int(connection.get("context_window") or 0)
            if calibrated_context > 0:
                requested_context_window = calibrated_context
        except (TypeError, ValueError):
            requested_context_window = None
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
    if disabled_skills:
        data["disabled_skills"] = [
            str(name) for name in disabled_skills if str(name).strip()
        ]
    if enable_vendor_skills is not None:
        data["enable_vendor_skills"] = bool(enable_vendor_skills)
    if pinned_skills:
        data["pinned_skills"] = [
            str(name).strip() for name in pinned_skills if str(name).strip()
        ]
    if identity_notes:
        data["identity_notes"] = str(identity_notes).strip()
    return data


@router.get("/api/tasks")
async def list_tasks(request: Request, page: int = 1, pageSize: int = 30):
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

        tasks = [
            {
                "id": str(orm.task_id),
                "task_id": str(orm.task_id),
                "status": orm.status,
                "conversation_id": _conversation_id_for(orm),
                "created_at": orm.created_at.isoformat() if orm.created_at else None,
                "request": (orm.input or {}).get("prompt", "Untitled task"),
            }
            for orm in orms
        ]

        return {"tasks": tasks, "total": len(tasks)}


@router.get("/api/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str):
    user = _require_user(request)

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
        last_id = "0"
        empty_polls = 0
        has_emitted_complete = False

        try:
            while True:
                if await request.is_disconnected():
                    break

                results = await redis.xread({stream_key: last_id}, count=10, block=1000)

                if not results:
                    if task_is_done:
                        empty_polls += 1
                        if empty_polls >= 2:
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
                            await asyncio.sleep(3)
                            return
                    yield {"data": json.dumps({"type": "ping"})}
                    continue

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
                            try:
                                stream_timestamp_ms = int(str(msg_id).split("-", 1)[0])
                                progress["created_at"] = datetime.fromtimestamp(
                                    stream_timestamp_ms / 1000,
                                    tz=timezone.utc,
                                ).isoformat()
                            except (TypeError, ValueError, OSError):
                                progress["created_at"] = None
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
                    await asyncio.sleep(3)
                    return

        finally:
            await redis.aclose()

    return EventSourceResponse(event_generator())


@router.post("/api/tasks")
async def create_task(
    request: Request,
    prompt: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    conversation_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    llm_connection: Optional[str] = Form(None),
):
    user = _require_user(request)
    parsed_connection = None
    if llm_connection:
        try:
            parsed_connection = (
                json.loads(llm_connection)
                if isinstance(llm_connection, str)
                else llm_connection
            )
            if not isinstance(parsed_connection, dict):
                parsed_connection = None
        except Exception:
            parsed_connection = None

    if task_id:
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
                if parsed_connection and conversation is not None:
                    if not isinstance(conversation.settings, dict):
                        conversation.settings = {}
                    effective_connection = _merge_conversation_llm_connection(
                        session, conversation.settings, parsed_connection
                    )
                    conversation.settings = {
                        **conversation.settings,
                        "llm_connection": effective_connection,
                    }
                    _set_app_setting(session, "llm_connection", effective_connection)
                    session.commit()
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
                disabled_skills = list(
                    (conversation_settings or {}).get("disabled_skills", [])
                )
                enable_vendor_skills = bool(
                    (conversation_settings or {}).get("enable_vendor_skills", True)
                )
                pinned_skills = list(
                    (conversation_settings or {}).get("pinned_skills", [])
                )
                identity_notes = str(
                    (conversation_settings or {}).get("identity_notes", "")
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
                        disabled_skills=disabled_skills,
                        enable_vendor_skills=enable_vendor_skills,
                        pinned_skills=pinned_skills,
                        identity_notes=identity_notes,
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

            age_seconds = (_now() - (existing_orm.updated_at or _now())).total_seconds()
            if existing_orm.status == "CREATED" and age_seconds > 300:
                with registry.SessionLocal() as session:
                    stale = session.get(TaskORM, existing_orm.task_id)
                    if stale is not None and stale.status == "CREATED":
                        stale.status = "FAILED"
                        stale.result = {
                            "error": "Stale task recovered after worker interruption."
                        }
                        session.commit()
                with registry.SessionLocal() as session:
                    refreshed = session.get(TaskORM, existing_orm.task_id)
                    if refreshed is not None and refreshed.status in TERMINAL_STATUSES:
                        existing_orm = refreshed
                        task = registry.create_task(
                            input=_task_input(
                                prompt,
                                conversation_id,
                                parent_task_id=task_id,
                                model=run_model,
                                disabled_tools=disabled_tools,
                                requested_context_window=requested_context_window,
                                auto_context_compress=auto_context_compress,
                                disabled_skills=disabled_skills,
                                enable_vendor_skills=enable_vendor_skills,
                                pinned_skills=pinned_skills,
                                identity_notes=identity_notes,
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

            raise HTTPException(
                status_code=409,
                detail="Task is currently running. Please wait for it to finish or interrupt it before sending a new prompt.",
            )

    new_task_id = str(task_id or uuid.uuid4())
    with registry.SessionLocal() as session:
        if conversation_id:
            conversation = _require_conversation(session, user.user_id, conversation_id)
        else:
            conversation = _ensure_default_conversation(session, user.user_id)
        if parsed_connection and conversation is not None:
            if not isinstance(conversation.settings, dict):
                conversation.settings = {}
            effective_connection = _merge_conversation_llm_connection(
                session, conversation.settings, parsed_connection
            )
            conversation.settings = {
                **conversation.settings,
                "llm_connection": effective_connection,
            }
            _set_app_setting(session, "llm_connection", effective_connection)
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
        disabled_skills = list((conversation.settings or {}).get("disabled_skills", []))
        enable_vendor_skills = bool(
            (conversation.settings or {}).get("enable_vendor_skills", True)
        )
        pinned_skills = list((conversation.settings or {}).get("pinned_skills", []))
        identity_notes = str((conversation.settings or {}).get("identity_notes", ""))
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
            disabled_skills=disabled_skills,
            enable_vendor_skills=enable_vendor_skills,
            pinned_skills=pinned_skills,
            identity_notes=identity_notes,
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


@router.post("/api/tasks/{task_id}/message")
async def send_task_message(request: Request, task_id: str):
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


@router.post("/api/conversations/{conversation_id}/messages")
async def send_conversation_message(request: Request, conversation_id: str):
    user = _require_user(request)
    body = await request.json()
    message = str(body.get("message") or body.get("prompt") or "").strip()
    requested_model = str(body.get("model") or "").strip() or None
    llm_connection = (
        body.get("llm_connection")
        if isinstance(body.get("llm_connection"), dict)
        else None
    )
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    with registry.SessionLocal() as session:
        conversation = _require_conversation(session, user.user_id, conversation_id)
        if llm_connection:
            if not isinstance(conversation.settings, dict):
                conversation.settings = {}
            effective_connection = _merge_conversation_llm_connection(
                session, conversation.settings, llm_connection
            )
            conversation.settings = {
                **conversation.settings,
                "llm_connection": effective_connection,
            }
            _set_app_setting(session, "llm_connection", effective_connection)
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
            age_seconds = (_now() - (active_task.updated_at or _now())).total_seconds()
            if active_task.status == "CREATED" and age_seconds > 300:
                active_task.status = "FAILED"
                active_task.result = {
                    "error": "Stale task recovered after worker interruption."
                }
                session.commit()
                active_task = None

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
            disabled_skills = list(
                (conversation.settings or {}).get("disabled_skills", [])
            )
            enable_vendor_skills = bool(
                (conversation.settings or {}).get("enable_vendor_skills", True)
            )
            pinned_skills = list((conversation.settings or {}).get("pinned_skills", []))
            identity_notes = str(
                (conversation.settings or {}).get("identity_notes", "")
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
            disabled_skills=disabled_skills,
            enable_vendor_skills=enable_vendor_skills,
            pinned_skills=pinned_skills,
            identity_notes=identity_notes,
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


@router.get("/api/tasks/{task_id}")
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
            "created_at": orm.created_at.isoformat() if orm.created_at else None,
            "updated_at": orm.updated_at.isoformat() if orm.updated_at else None,
        }


@router.post("/api/tasks/{task_id}/interrupt")
@router.post("/api/tasks/{task_id}/terminate")
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


@router.delete("/api/tasks/{task_id}")
async def delete_task(request: Request, task_id: str):
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
