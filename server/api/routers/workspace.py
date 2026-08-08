import datetime
import mimetypes
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from server.api import deps


router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# An editor save is a human typing, not a data import. Anything past this is a
# mistake, and refusing keeps a runaway paste from filling the volume.
MAX_WRITE_BYTES = 10 * 1024 * 1024

# New files land readable by the host user and any sandbox uid, matching what
# the agent's own tools produce.
DEFAULT_FILE_MODE = 0o644


class WriteFileRequest(BaseModel):
    content: str


# Paths the agent reports are absolute inside its own sandbox ("/workspace/x")
# or inside the API container ("/app/workspace/x"). Both mean the same file on
# the shared volume, so strip either prefix before resolving.
_ABSOLUTE_PREFIXES = ("/app/workspace", "/workspace")


def _workspace_root() -> Path:
    # Read through the module so tests and reconfiguration take effect.
    return Path(deps.WORKSPACE_ROOT).resolve()


def _resolve(path: str) -> Path:
    """Map a client-supplied path onto a real path inside the workspace root."""
    base = _workspace_root()
    relative = (path or "").strip()

    for prefix in (str(base), *_ABSOLUTE_PREFIXES):
        if relative == prefix or relative.startswith(prefix + "/"):
            relative = relative[len(prefix) :]
            break
    relative = relative.lstrip("/")

    target = (base / relative).resolve()
    # resolve() collapses "..", so containment is decided on the final path.
    if target != base and base not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return target


def _listing(target: Path) -> list[dict]:
    entries = []
    try:
        with os.scandir(target) as scan:
            listing = sorted(scan, key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return entries

    for entry in listing:
        try:
            stat = entry.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "size": stat.st_size,
                "modifiedTime": datetime.datetime.fromtimestamp(
                    stat.st_mtime
                ).isoformat(),
            }
        )
    return entries


def _media_type(target: Path) -> str:
    guessed, _ = mimetypes.guess_type(target.name)
    return guessed or "application/octet-stream"


def _zip_directory(target: Path) -> FileResponse:
    """Zip a directory to a temp file and delete it once the response is sent."""
    handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    handle.close()
    archive = Path(handle.name)
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file_path in sorted(target.rglob("*")):
                if file_path.is_file() and not file_path.is_symlink():
                    bundle.write(file_path, file_path.relative_to(target))
    except Exception:
        archive.unlink(missing_ok=True)
        raise

    return FileResponse(
        archive,
        filename=f"{target.name or 'workspace'}.zip",
        media_type="application/zip",
        background=BackgroundTask(archive.unlink, missing_ok=True),
    )


@router.get("/download/{path:path}")
async def download_workspace(path: str = ""):
    """Download a workspace file, or a directory as a zip archive.

    Registered before the catch-all listing route so that download URLs are not
    swallowed by it — otherwise they resolve to a nonexistent
    "<workspace>/download/..." path and the client saves the JSON listing
    instead of the requested file.
    """
    target = _resolve(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")

    if target.is_file():
        return FileResponse(
            target, filename=target.name, media_type=_media_type(target)
        )

    return _zip_directory(target)


@router.put("/file/{path:path}")
async def write_workspace_file(path: str, body: WriteFileRequest):
    """Save editor content back to a workspace file.

    Writes through a temporary file in the same directory and replaces the
    target, so an interrupted save leaves the original intact rather than a
    truncated file the agent would then read as gospel.
    """
    target = _resolve(path)

    if target.is_dir():
        raise HTTPException(status_code=400, detail="Path is a directory")
    if len(body.content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise HTTPException(status_code=413, detail="File too large to save")

    # os.replace carries the temp file's mode across, and NamedTemporaryFile
    # creates at 0600. Without restoring the original mode, saving a file
    # silently strips group/other access — locking the host user, and any
    # sandbox running as another uid, out of the agent's own workspace.
    mode = target.stat().st_mode & 0o777 if target.exists() else DEFAULT_FILE_MODE

    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle as stream:
            stream.write(body.content)
        os.chmod(handle.name, mode)
        os.replace(handle.name, target)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise

    stat = target.stat()
    return {
        "path": str(target.relative_to(_workspace_root())),
        "size": stat.st_size,
        "modifiedTime": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


@router.get("/{path:path}")
async def get_workspace(path: str = ""):
    """List a workspace directory, or return file content for preview."""
    target = _resolve(path)

    if not target.exists():
        # A workspace that has not been written to yet is empty, not missing.
        if target == _workspace_root():
            return []
        raise HTTPException(status_code=404, detail="Not found")

    if target.is_file():
        # No filename= here: the preview fetches this inline, and an attachment
        # disposition would force a download in browsers that render it directly.
        return FileResponse(target, media_type=_media_type(target))

    return _listing(target)
