from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request

from app.skills import load_skills, select_skills
from server.api.deps import (
    registry,
    _require_user,
    _require_conversation,
    _get_app_setting,
    AVAILABLE_TOOLS,
    WORKSPACE_ROOT,
)

router = APIRouter(prefix="/api", tags=["tools_skills"])

@router.get("/tools")
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

@router.get("/skills")
async def list_skills(
    request: Request,
    conversation_id: Optional[str] = None,
    prompt: Optional[str] = None,
):
    user = _require_user(request)
    workspace = None
    include_vendor = True
    disabled_skills: set[str] = set()
    if conversation_id:
        with registry.SessionLocal() as session:
            conversation = _require_conversation(session, user.user_id, conversation_id)
            settings = conversation.settings or {}
            include_vendor = bool(settings.get("enable_vendor_skills", True))
            disabled_skills = {
                str(name)
                for name in (settings.get("disabled_skills") or [])
                if str(name).strip()
            }
        workspace = Path(WORKSPACE_ROOT) / "conversations" / conversation_id
    skills = (
        select_skills(
            prompt or "",
            workspace,
            include_vendor=include_vendor,
            disabled_skills=disabled_skills,
        )
        if prompt
        else load_skills(
            workspace,
            include_vendor=include_vendor,
            disabled_skills=set(),
        )
    )
    output = []
    for skill in skills:
        item = skill.summary()
        item["enabled"] = skill.name not in disabled_skills
        output.append(item)
    return {"skills": output}
