import datetime
import mimetypes
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from server.api import deps


router = APIRouter(prefix="/api/workspace", tags=["workspace"])

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
