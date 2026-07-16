import os
import threading
import asyncio
from typing import Any, Optional
from fastapi import APIRouter, Request, HTTPException

from app.config import config
from server.api.deps import (
    registry,
    _require_admin,
    _get_app_setting,
    _set_app_setting,
    AVAILABLE_TOOLS,
    REDIS_URL,
    WORKSPACE_ROOT,
    SESSION_DAYS,
    DEFAULT_CONVERSATION_ID,
)
from server.api.routers.models_llm import (
    list_models,
    _redact_config,
    _default_llm_connection,
    _effective_llm_connection,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_calibration_status: dict = {}

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

def _calibrate_model_sync(
    base_url: str,
    model_id: str,
    api_key: str | None,
    embedding_model: str | None,
    gpu_target_percent: float = 97.0,
    ram_target_percent: float = 85.0,
    max_context: int | None = None,
) -> dict:
    from server.model_calibration import LMStudioCalibrationRunner

    global _calibration_status

    def status(phase: str, message: str, progress: int = -1, **extra) -> None:
        _calibration_status.update(
            phase=phase,
            message=message,
            progress=progress,
            running=True,
            **extra,
        )

    status("init", "Starting resource-aware calibration", 0)
    runner = LMStudioCalibrationRunner(
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
        embedding_model=embedding_model,
        gpu_target_percent=gpu_target_percent,
        ram_target_percent=ram_target_percent,
        max_context=max_context,
        status_callback=status,
    )
    result = runner.run()
    active = result["profiles"][result["active_mode"]]
    _calibration_status.update(
        phase="done",
        running=False,
        progress=100,
        result=result,
        message=(
            f"Calibration complete. {result['active_mode'].title()} mode is active at "
            f"{active['context_length']:,} tokens."
        ),
    )
    return result

@router.get("/settings")
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

@router.put("/settings")
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
                    "thinking_budget",
                    "execution_mode",
                    "max_steps",
                    "context_window",
                    "calibration_mode",
                    "fallback_chain",
                ]
                if body["llm_connection"].get(key) not in (None, "")
            }
            if "fallback_chain" in allowed and not isinstance(
                allowed["fallback_chain"], list
            ):
                raise HTTPException(
                    status_code=400,
                    detail="llm_connection.fallback_chain must be a list of connection objects",
                )
            if "max_steps" in allowed:
                try:
                    max_steps = int(allowed["max_steps"])
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail="llm_connection.max_steps must be an integer",
                    )
                if not 1 <= max_steps <= 200:
                    raise HTTPException(
                        status_code=400,
                        detail="llm_connection.max_steps must be between 1 and 200",
                    )
                allowed["max_steps"] = max_steps
            if "execution_mode" in allowed:
                execution_mode = str(allowed["execution_mode"]).strip().lower()
                if execution_mode not in {"fast", "balanced", "deep"}:
                    raise HTTPException(
                        status_code=400,
                        detail="llm_connection.execution_mode must be fast, balanced, or deep",
                    )
                allowed["execution_mode"] = execution_mode
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

@router.post("/calibrate")
async def start_calibration(request: Request):
    """Launch model auto-calibration in a background thread."""
    _require_admin(request)

    global _calibration_status
    if _calibration_status.get("running"):
        raise HTTPException(status_code=409, detail="Calibration already in progress")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    with registry.SessionLocal() as session:
        connection = _effective_llm_connection(session)

    base_url = body.get("base_url") or connection.get("base_url", "")
    model_id = body.get("model") or connection.get("model", "")
    api_key = body.get("api_key") or connection.get("api_key") or ""
    embedding_model = body.get("embedding_model") or ""
    try:
        gpu_target_percent = float(body.get("gpu_target_percent", 97))
        ram_target_percent = float(body.get("ram_target_percent", 85))
        max_context_raw = body.get("max_context")
        max_context = (
            int(max_context_raw) if max_context_raw not in (None, "") else None
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail="Calibration limits must be numeric"
        )

    if not 50 <= gpu_target_percent <= 99.5:
        raise HTTPException(
            status_code=400,
            detail="GPU target must be between 50 and 99.5 percent",
        )
    if not 50 <= ram_target_percent <= 95:
        raise HTTPException(
            status_code=400, detail="RAM target must be between 50 and 95 percent"
        )
    if max_context is not None and max_context < 8192:
        raise HTTPException(
            status_code=400, detail="Maximum context must be at least 8192"
        )

    if not base_url:
        raise HTTPException(status_code=400, detail="No base_url configured")

    _calibration_status = {
        "phase": "init",
        "message": "Starting...",
        "running": True,
        "progress": 0,
    }

    def _run():
        try:
            result = _calibrate_model_sync(
                base_url,
                model_id,
                api_key or None,
                embedding_model or None,
                gpu_target_percent,
                ram_target_percent,
                max_context,
            )
            if "error" not in result:
                with registry.SessionLocal() as session:
                    existing = _get_app_setting(session, "llm_connection", {})
                    if isinstance(existing, dict):
                        existing["model"] = result["model_id"]
                        existing["calibration_mode"] = result["active_mode"]
                        existing["context_window"] = result["profiles"][
                            result["active_mode"]
                        ]["context_length"]
                    else:
                        existing = {
                            "model": result["model_id"],
                            "base_url": base_url,
                            "api_type": "lmstudio",
                            "calibration_mode": result["active_mode"],
                            "context_window": result["profiles"][result["active_mode"]][
                                "context_length"
                            ],
                        }
                    _set_app_setting(session, "llm_connection", existing)
                    _set_app_setting(session, "calibration_result", result)
                    session.commit()
        except Exception as exc:
            _calibration_status.update(
                phase="error",
                message=f"Calibration failed: {exc}",
                running=False,
                progress=100,
            )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"status": "started", "message": "Calibration started in background"}

@router.get("/calibrate/status")
async def calibration_status(request: Request):
    """Return current calibration progress."""
    _require_admin(request)
    return _calibration_status or {
        "phase": "idle",
        "message": "No calibration running",
        "running": False,
        "progress": 0,
    }

@router.get("/calibration-result")
async def get_calibration_result(request: Request):
    """Return the last saved calibration result."""
    _require_admin(request)
    with registry.SessionLocal() as session:
        result = _get_app_setting(session, "calibration_result", None)
    return {"result": result}

@router.post("/calibration/apply")
async def apply_calibration_mode(request: Request):
    _require_admin(request)
    body = await request.json()
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in {"fast", "deep"}:
        raise HTTPException(status_code=400, detail="Mode must be 'fast' or 'deep'")

    with registry.SessionLocal() as session:
        result = _get_app_setting(session, "calibration_result", None)
        connection = _effective_llm_connection(session)
    if not isinstance(result, dict) or not isinstance(result.get("profiles"), dict):
        raise HTTPException(
            status_code=404, detail="No resource-aware calibration result found"
        )
    profile = result["profiles"].get(mode)
    if not isinstance(profile, dict):
        raise HTTPException(
            status_code=404, detail=f"Calibration mode '{mode}' is unavailable"
        )

    from server.model_calibration import apply_profile

    try:
        applied = await asyncio.to_thread(
            apply_profile,
            base_url=str(connection.get("base_url") or ""),
            model_id=str(result.get("model_id") or connection.get("model") or ""),
            api_key=str(connection.get("api_key") or "") or None,
            embedding_model=str(result.get("embedding_model") or "") or None,
            profile=profile,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not apply {mode} mode: {exc}"
        )

    result["active_mode"] = mode
    with registry.SessionLocal() as session:
        connection = _get_app_setting(session, "llm_connection", {})
        if not isinstance(connection, dict):
            connection = {}
        connection.update(
            {
                "model": result["model_id"],
                "calibration_mode": mode,
                "context_window": int(profile["context_length"]),
            }
        )
        _set_app_setting(session, "llm_connection", connection)
        _set_app_setting(session, "calibration_result", result)
        session.commit()
    return {
        "ok": True,
        "mode": mode,
        "profile": profile,
        "applied": applied,
        "result": result,
    }
