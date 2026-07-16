from fastapi import APIRouter, HTTPException, Request

from server.api.deps import (
    _conversation_sandbox,
    _require_conversation,
    _require_user,
    registry,
)


router = APIRouter(prefix="/api/conversations", tags=["sandbox"])


@router.post("/{conversation_id}/sandbox/start")
async def start_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    sandbox = await _conversation_sandbox(conversation_id).ensure()
    return {"conversation_id": conversation_id, "sandbox": await sandbox.status()}


@router.post("/{conversation_id}/sandbox/pause")
async def pause_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    sandbox = _conversation_sandbox(conversation_id)
    await sandbox.pause()
    return {"conversation_id": conversation_id, "sandbox": await sandbox.status()}


@router.post("/{conversation_id}/sandbox/resume")
async def resume_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    sandbox = _conversation_sandbox(conversation_id)
    await sandbox.resume()
    return {"conversation_id": conversation_id, "sandbox": await sandbox.status()}


@router.delete("/{conversation_id}/sandbox")
async def delete_conversation_sandbox(request: Request, conversation_id: str):
    user = _require_user(request)
    with registry.SessionLocal() as session:
        _require_conversation(session, user.user_id, conversation_id)
    await _conversation_sandbox(conversation_id).delete()
    return {"conversation_id": conversation_id, "deleted": True}


@router.post("/{conversation_id}/runtime/processes/{pid}/kill")
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


@router.post("/{conversation_id}/runtime/containers/{container_id}/stop")
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
