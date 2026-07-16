import datetime
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("/{path:path}")
async def get_workspace(path: str = ""):
    """List files in /app/workspace or return file content."""
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
