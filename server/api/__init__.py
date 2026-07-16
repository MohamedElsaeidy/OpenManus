import uvicorn

from server.api.app import app
from server.api.deps import registry
from server.api.event_mapping import _agent_event_to_progress


__all__ = ["app", "registry", "_agent_event_to_progress"]

if __name__ == "__main__":  # pragma: no cover
    uvicorn.run(app, host="0.0.0.0", port=8000)
