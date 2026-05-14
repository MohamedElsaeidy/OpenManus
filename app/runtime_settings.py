import os
import time
from typing import Any, Optional

from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/openmanus",
)

_engine = None
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_SECONDS = 5


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL)
    return _engine


def get_setting(key: str, default: Optional[Any] = None) -> Any:
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_SECONDS:
        return cached[1]

    try:
        with _get_engine().begin() as connection:
            value = connection.execute(
                text("SELECT value FROM app_settings WHERE key = :key"),
                {"key": key},
            ).scalar()
    except Exception:
        value = None

    if value is None:
        value = default
    _cache[key] = (now, value)
    return value


def get_disabled_tools() -> set[str]:
    tools = get_setting("tools", {})
    disabled = tools.get("disabled", []) if isinstance(tools, dict) else []
    return {str(name) for name in disabled}


def get_llm_connection() -> dict:
    settings = get_setting("llm_connection", {})
    return settings if isinstance(settings, dict) else {}


def get_config_overrides() -> dict:
    settings = get_setting("config_overrides", {})
    return settings if isinstance(settings, dict) else {}


def get_config_override(path: str, default: Any = None) -> Any:
    value: Any = get_config_overrides()
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value
