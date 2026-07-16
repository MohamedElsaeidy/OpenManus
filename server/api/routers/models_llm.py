import json
from typing import Any, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import APIRouter, HTTPException, Request

from app.config import config
from server.api.deps import _get_app_setting, _require_user, registry


router = APIRouter(prefix="/api", tags=["models"])


def _redact_config(value: Any) -> Any:
    secret_key_names = {
        "api_key",
        "apikey",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "bearer_token",
        "authorization",
        "auth_token",
    }
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in secret_key_names or lowered.endswith("_api_key"):
                redacted[key] = "********" if item else item
            else:
                redacted[key] = _redact_config(item)
        return redacted
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


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


def _merge_conversation_llm_connection(session, settings: dict, incoming: dict) -> dict:
    existing = settings.get("llm_connection", {}) if isinstance(settings, dict) else {}
    return {
        **_effective_llm_connection(session),
        **(existing if isinstance(existing, dict) else {}),
        **(incoming if isinstance(incoming, dict) else {}),
    }


def _lmstudio_native_base(base_url: str) -> Optional[str]:
    try:
        parsed = urlparse.urlparse(base_url.strip())
    except Exception:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    root = f"{parsed.scheme}://{parsed.netloc}"
    return f"{root}/api/v1"


def _lmstudio_api_request(
    method: str,
    base_url: str,
    subpath: str,
    payload: Optional[dict] = None,
    token: Optional[str] = None,
    timeout: int = 30,
) -> dict:
    try:
        parsed = urlparse.urlparse(base_url.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid LM Studio host URL")
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid LM Studio host URL")
    root = f"{parsed.scheme}://{parsed.netloc}"
    subpath = subpath if subpath.startswith("/") else f"/{subpath}"

    last_exc = None
    for prefix in ["/api/v1", "/api/v0"]:
        url = f"{root}{prefix}{subpath}"
        try:
            return _http_json(
                method, url, payload=payload, token=token, timeout=timeout
            )
        except urlerror.HTTPError as exc:
            last_exc = exc
            detail = ""
            try:
                detail = (
                    exc.read().decode("utf-8", errors="ignore")
                    if hasattr(exc, "read")
                    else ""
                )
            except Exception:
                pass
            if (
                exc.code == 404
                or "Unexpected endpoint or method" in detail
                or "Unexpected endpoint" in detail
            ):
                if prefix == "/api/v1":
                    continue
            if detail:
                raise HTTPException(status_code=exc.code, detail=detail) from exc
            raise
        except Exception as exc:
            last_exc = exc
            if prefix == "/api/v1":
                continue
            raise
    if isinstance(last_exc, HTTPException):
        raise last_exc
    raise HTTPException(
        status_code=502, detail=f"LM Studio API request failed: {last_exc}"
    )


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


def _extract_model_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data_rows = payload.get("data")
    if isinstance(data_rows, list):
        return [item for item in data_rows if isinstance(item, dict)]
    model_rows = payload.get("models")
    if isinstance(model_rows, list):
        return [item for item in model_rows if isinstance(item, dict)]
    return []


def _model_id_from_row(item: dict) -> str:
    return str(
        item.get("id")
        or item.get("key")
        or item.get("model")
        or item.get("name")
        or item.get("instance_id")
        or ""
    ).strip()


def _model_instance_id_from_row(item: dict) -> str:
    loaded_instances = item.get("loaded_instances")
    if isinstance(loaded_instances, list) and loaded_instances:
        first = loaded_instances[0]
        if isinstance(first, dict):
            instance_id = str(first.get("id") or "").strip()
            if instance_id:
                return instance_id
    return str(item.get("instance_id") or "").strip()


def _model_state_from_row(item: dict) -> str:
    state = str(item.get("state") or "").strip().lower()
    if state:
        return state
    loaded_instances = item.get("loaded_instances")
    if isinstance(loaded_instances, list):
        return "loaded" if len(loaded_instances) > 0 else "not-loaded"
    return ""


def _model_variant_tag_from_row(item: dict) -> str:
    quant = item.get("quantization")
    if isinstance(quant, dict):
        name = str(quant.get("name") or "").strip()
        if name:
            return name
    params = str(item.get("params_string") or "").strip()
    if params:
        return params
    fmt = str(item.get("format") or "").strip()
    if fmt:
        return fmt
    return ""


def _llm_connection_health(session) -> dict:
    connection = _effective_llm_connection(session)
    payload = {
        "configured": bool(connection),
        "live": False,
        "reason": "Not configured",
        "api_type": str(connection.get("api_type") or ""),
        "base_url": str(connection.get("base_url") or ""),
    }
    base_url = str(connection.get("base_url") or "").strip()
    if not base_url:
        return payload
    api_type = str(connection.get("api_type") or "").strip().lower()
    token = str(connection.get("api_key") or "").strip() or None
    try:
        if api_type in {"lmstudio", "local"}:
            data = _lmstudio_api_request(
                "GET", base_url, "/models", token=token, timeout=8
            )
        else:
            models_url = (
                base_url.rstrip("/") + "/models"
                if base_url.rstrip("/").endswith("/v1")
                else base_url.rstrip("/") + "/v1/models"
            )
            data = _http_json("GET", models_url, token=token)
        rows = _extract_model_rows(data)
        payload["live"] = True
        payload["reason"] = f"OK ({len(rows)} models)"
        payload["model_count"] = len(rows)
        return payload
    except Exception as exc:
        payload["live"] = False
        payload["reason"] = str(exc)
        return payload


@router.get("/models")
async def list_models(request: Request):
    _require_user(request)
    with registry.SessionLocal() as session:
        connection = _effective_llm_connection(session)

    configured = [
        {
            "id": settings.model,
            "name": name,
            "api_type": settings.api_type,
            "base_model": settings.model,
            "variant_tag": "",
            "raw_model_key": settings.model,
        }
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
                "base_model": connection["model"],
                "variant_tag": "",
                "raw_model_key": connection["model"],
            },
        )
    models = configured

    try:
        base_url = str(connection.get("base_url") or "")
        api_type = str(connection.get("api_type") or "").lower()
        api_key = str(connection.get("api_key") or "")
        native_base = _lmstudio_native_base(base_url)
        if native_base and (
            api_type in {"openai", "lmstudio", "local"}
            or "1234" in base_url
            or "lmstudio" in base_url.lower()
        ):
            listing = _lmstudio_api_request(
                "GET", base_url, "/models", token=api_key or None, timeout=8
            )
            lm_models = _extract_model_rows(listing)
            if isinstance(lm_models, list):
                for item in lm_models:
                    if not isinstance(item, dict):
                        continue
                    model_id = _model_id_from_row(item)
                    if not model_id:
                        continue
                    models.insert(
                        0,
                        {
                            "id": model_id,
                            "name": item.get("display_name")
                            or item.get("path")
                            or item.get("name")
                            or "lmstudio",
                            "api_type": "lmstudio",
                            "state": _model_state_from_row(item),
                            "instance_id": _model_instance_id_from_row(item),
                            "base_model": str(item.get("key") or model_id),
                            "variant_tag": _model_variant_tag_from_row(item),
                            "raw_model_key": str(item.get("key") or model_id),
                        },
                    )
    except Exception:
        pass

    seen = set()
    unique_models = []
    for model in models:
        if model["id"] in seen:
            continue
        seen.add(model["id"])
        unique_models.append(model)
    return {"models": unique_models}


@router.post("/models/query")
async def query_models(request: Request):
    _require_user(request)
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    host = str((body or {}).get("host") or "").strip()
    api_key = str((body or {}).get("api_key") or "").strip() or None
    style = str((body or {}).get("style") or "custom").strip().lower()
    models_path = str((body or {}).get("models_path") or "").strip()

    if not host:
        raise HTTPException(status_code=400, detail="Host is required")

    models: list[dict] = []
    url = ""
    try:
        if (
            style in {"lm-studio", "lmstudio"}
            or "1234" in host
            or "lmstudio" in host.lower()
        ):
            data = _lmstudio_api_request(
                "GET", host, "/models", token=api_key, timeout=8
            )
            rows = _extract_model_rows(data)
            for item in rows:
                if not isinstance(item, dict):
                    continue
                model_id = _model_id_from_row(item)
                if not model_id:
                    continue
                models.append(
                    {
                        "id": model_id,
                        "name": item.get("display_name")
                        or item.get("path")
                        or item.get("name")
                        or model_id,
                        "api_type": "lmstudio",
                        "state": _model_state_from_row(item),
                        "instance_id": _model_instance_id_from_row(item),
                        "base_model": str(item.get("key") or model_id),
                        "variant_tag": _model_variant_tag_from_row(item),
                        "raw_model_key": str(item.get("key") or model_id),
                    }
                )
        elif style == "ollama":
            url = host.rstrip("/") + "/v1/models"
            try:
                data = _http_json("GET", url, token=api_key, timeout=8)
                rows = _extract_model_rows(data)
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    model_id = _model_id_from_row(item)
                    if model_id:
                        models.append(
                            {
                                "id": model_id,
                                "name": model_id,
                                "api_type": "ollama",
                                "base_model": model_id,
                                "variant_tag": "",
                                "raw_model_key": model_id,
                            }
                        )
            except Exception:
                url = host.rstrip("/") + "/api/tags"
                data = _http_json("GET", url, token=api_key, timeout=8)
                rows = _extract_model_rows(data)
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    model_id = _model_id_from_row(item)
                    if model_id:
                        models.append(
                            {
                                "id": model_id,
                                "name": model_id,
                                "api_type": "ollama",
                                "base_model": model_id,
                                "variant_tag": "",
                                "raw_model_key": model_id,
                            }
                        )
        else:
            if style == "openai":
                suffix = "/v1/models"
            else:
                suffix = models_path or "/v1/models"
                if not suffix.startswith("/"):
                    suffix = "/" + suffix
            url = host.rstrip("/") + suffix
            data = _http_json("GET", url, token=api_key, timeout=8)
            rows = _extract_model_rows(data)
            for item in rows:
                if not isinstance(item, dict):
                    continue
                model_id = _model_id_from_row(item)
                if model_id:
                    models.append(
                        {
                            "id": model_id,
                            "name": model_id,
                            "api_type": style or "custom",
                            "base_model": model_id,
                            "variant_tag": "",
                            "raw_model_key": model_id,
                        }
                    )

        seen: set[str] = set()
        unique_models: list[dict] = []
        for model in models:
            model_id = str(model.get("id") or "")
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            unique_models.append(model)
        return {"models": unique_models, "url": url}
    except HTTPException:
        raise
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=detail or f"HTTP {exc.code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Model query failed: {exc}")


@router.post("/models/load")
async def load_model(request: Request):
    _require_user(request)
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    host = str((body or {}).get("host") or "").strip()
    api_key = str((body or {}).get("api_key") or "").strip() or None
    style = str((body or {}).get("style") or "custom").strip().lower()
    model = str((body or {}).get("model") or "").strip()
    context_length_raw = (body or {}).get("context_length")

    if not host:
        raise HTTPException(status_code=400, detail="Host is required")
    if not model:
        raise HTTPException(status_code=400, detail="Model is required")
    if style != "lm-studio":
        raise HTTPException(
            status_code=400,
            detail="Load model is currently supported for LM Studio profiles only",
        )

    native = _lmstudio_native_base(host)
    if not native:
        raise HTTPException(status_code=400, detail="Invalid LM Studio host URL")

    payload: dict[str, Any] = {"model": model, "echo_load_config": True}
    if context_length_raw not in (None, ""):
        try:
            context_length = int(context_length_raw)
            if context_length > 0:
                payload["context_length"] = context_length
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="context_length must be a positive integer"
            )

    try:
        data = _lmstudio_api_request(
            "POST",
            host,
            "/models/load",
            payload=payload,
            token=api_key,
            timeout=60,
        )
        return {"ok": True, "result": data}
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=detail or f"HTTP {exc.code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Load model failed: {exc}")


@router.post("/models/eject")
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

    base_url = str(
        (body or {}).get("host")
        or (body or {}).get("base_url")
        or connection.get("base_url")
        or ""
    ).strip()
    api_type = (
        str(
            (body or {}).get("style")
            or (body or {}).get("api_type")
            or connection.get("api_type")
            or ""
        )
        .strip()
        .lower()
    )
    api_key = str(
        (body or {}).get("api_key") or connection.get("api_key") or ""
    ).strip()

    if not base_url:
        raise HTTPException(status_code=400, detail="LLM base_url is not configured")
    if (
        api_type not in {"openai", "lmstudio", "lm-studio", "local"}
        and "1234" not in base_url
    ):
        raise HTTPException(
            status_code=400, detail="Eject is only supported for LM Studio connections"
        )

    try:
        listing = _lmstudio_api_request(
            "GET", base_url, "/models", token=api_key, timeout=8
        )
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
                if isinstance(m, dict)
                and (
                    str(m.get("id") or "") == target_instance_id
                    or str(m.get("instance_id") or "") == target_instance_id
                )
            ),
            None,
        )
        if exact:
            instance_id = str(
                exact.get("instance_id") or exact.get("id") or target_instance_id
            )

        unloaded = _lmstudio_api_request(
            "POST",
            base_url,
            "/models/unload",
            payload={"instance_id": instance_id},
            token=api_key,
            timeout=15,
        )
        return {
            "ok": True,
            "requested_model": requested_model or instance_id,
            "instance_id": unloaded.get("instance_id", instance_id)
            if isinstance(unloaded, dict)
            else instance_id,
        }
    except HTTPException as exc:
        detail_raw = str(exc.detail or "")
        try:
            parsed = (
                json.loads(detail_raw)
                if detail_raw and detail_raw.startswith("{")
                else {}
            )
            err = parsed.get("error") if isinstance(parsed, dict) else None
            err_type = str((err or {}).get("type") or "")
            if err_type == "model_not_found" or exc.status_code == 404:
                return {
                    "ok": True,
                    "requested_model": requested_model or "",
                    "instance_id": requested_model or "",
                    "already_unloaded": True,
                }
        except Exception:
            pass
        raise
    except urlerror.HTTPError as exc:
        detail_raw = ""
        try:
            detail_raw = (
                exc.read().decode("utf-8", errors="ignore")
                if hasattr(exc, "read")
                else ""
            )
            parsed = (
                json.loads(detail_raw)
                if detail_raw and detail_raw.startswith("{")
                else {}
            )
            err = parsed.get("error") if isinstance(parsed, dict) else None
            err_type = str((err or {}).get("type") or "")
            if err_type == "model_not_found" or exc.code == 404:
                return {
                    "ok": True,
                    "requested_model": requested_model or "",
                    "instance_id": requested_model or "",
                    "already_unloaded": True,
                }
        except Exception:
            pass
        raise HTTPException(
            status_code=exc.code, detail=detail_raw or "LM Studio eject failed"
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LM Studio eject failed: {exc}")


@router.post("/connection/verify")
async def verify_connection(request: Request):
    _require_user(request)
    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    host = str((body or {}).get("host") or "").strip()
    api_key = str((body or {}).get("api_key") or "").strip() or None
    style = str((body or {}).get("style") or "custom").strip().lower()
    models_path = str((body or {}).get("models_path") or "").strip()

    if not host:
        raise HTTPException(status_code=400, detail="Host is required")

    if style == "lm-studio":
        data = _lmstudio_api_request("GET", host, "/models", token=api_key, timeout=8)
        count = len(_extract_model_rows(data))
        return {"ok": True, "url": host, "models_count": count}
    elif style in {"openai", "ollama"}:
        suffix = models_path or "/v1/models"
        if not suffix.startswith("/"):
            suffix = "/" + suffix
        url = host.rstrip("/") + suffix

    try:
        data = _http_json("GET", url, token=api_key, timeout=8)
        count = len(_extract_model_rows(data))
        return {"ok": True, "url": url, "models_count": count}
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=exc.code, detail=detail or f"HTTP {exc.code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Connection verify failed: {exc}")
