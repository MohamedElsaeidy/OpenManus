from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = "MohamedElsaeidy/OpenManus"
GITHUB_API = f"https://api.github.com/repos/{REPOSITORY}"
CHECKSUM_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ROOT_DOCKERIGNORE = """.git
.github
.venv
__pycache__
*.py[cod]
*.egg-info
build
dist
config/config.toml
data
frontend
logs
research
tests
workspace
"""
FRONTEND_DOCKERIGNORE = """dist
node_modules
*.log
"""


class DeploymentError(RuntimeError):
    """Raised when a deployment cannot be initialized safely."""


@dataclass(frozen=True)
class ReleaseAssets:
    tag: str
    archive_name: str
    archive_url: str
    checksum_url: str


def release_tag_for_version(version: str) -> str:
    normalized = version.strip()
    if not normalized:
        raise DeploymentError("Package version is empty")
    return normalized if normalized.startswith("v") else f"v{normalized}"


def _request_bytes(url: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "openmanus-deployer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 404:
            raise DeploymentError(f"Release was not found: {url}") from exc
        raise DeploymentError(f"GitHub returned HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise DeploymentError(f"Could not reach GitHub: {exc.reason}") from exc


def fetch_release_assets(tag: str) -> ReleaseAssets:
    endpoint = (
        f"{GITHUB_API}/releases/latest"
        if tag == "latest"
        else f"{GITHUB_API}/releases/tags/{quote(tag, safe='')}"
    )
    try:
        payload = json.loads(_request_bytes(endpoint))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DeploymentError("GitHub returned invalid release metadata") from exc

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise DeploymentError("GitHub release metadata has no assets list")

    archives = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("name", "")).startswith("openmanus-production-")
        and str(asset.get("name", "")).endswith(".tar.gz")
    ]
    if len(archives) != 1:
        raise DeploymentError(
            "Release must contain exactly one OpenManus production archive"
        )

    archive = archives[0]
    archive_name = str(archive["name"])
    checksum_name = f"{archive_name}.sha256"
    checksum = next(
        (
            asset
            for asset in assets
            if isinstance(asset, dict) and asset.get("name") == checksum_name
        ),
        None,
    )
    if checksum is None:
        raise DeploymentError(f"Release is missing checksum asset {checksum_name}")

    archive_url = str(archive.get("browser_download_url") or "")
    checksum_url = str(checksum.get("browser_download_url") or "")
    if not archive_url or not checksum_url:
        raise DeploymentError("Release assets are missing download URLs")

    return ReleaseAssets(
        tag=str(payload.get("tag_name") or tag),
        archive_name=archive_name,
        archive_url=archive_url,
        checksum_url=checksum_url,
    )


def download_url(url: str, destination: Path) -> None:
    headers = {"User-Agent": "openmanus-deployer"}
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=120) as response, destination.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
    except HTTPError as exc:
        raise DeploymentError(f"Download failed with HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise DeploymentError(f"Download failed: {exc.reason}") from exc


def verify_checksum(archive: Path, checksum_file: Path) -> str:
    checksum_line = checksum_file.read_text(encoding="utf-8").strip().splitlines()
    expected = checksum_line[0].split()[0].lower() if checksum_line else ""
    if not CHECKSUM_RE.fullmatch(expected):
        raise DeploymentError("Release checksum file is malformed")

    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise DeploymentError(
            f"Release checksum mismatch: expected {expected}, received {actual}"
        )
    return actual


def _validate_tar_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    has_windows_path = "\\" in member.name or (
        bool(path.parts) and path.parts[0].endswith(":")
    )
    if not path.parts or path.is_absolute() or ".." in path.parts or has_windows_path:
        raise DeploymentError(f"Unsafe path in release archive: {member.name}")
    if member.issym() or member.islnk() or member.isdev():
        raise DeploymentError(f"Unsafe file type in release archive: {member.name}")
    if not (member.isfile() or member.isdir()):
        raise DeploymentError(
            f"Unsupported file type in release archive: {member.name}"
        )


def extract_release_archive(archive_path: Path, destination: Path) -> Path:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                _validate_tar_member(member)
            roots = {
                PurePosixPath(member.name).parts[0]
                for member in members
                if PurePosixPath(member.name).parts
            }
            if len(roots) != 1:
                raise DeploymentError(
                    "Release archive must contain one top-level deployment directory"
                )
            if sys.version_info >= (3, 12):
                archive.extractall(destination, members=members, filter="data")
            else:
                archive.extractall(destination, members=members)
    except (tarfile.TarError, OSError) as exc:
        raise DeploymentError(f"Could not extract release archive: {exc}") from exc

    bundle_root = destination / roots.pop()
    if not (bundle_root / "docker-compose.yml").is_file():
        raise DeploymentError("Release archive does not contain docker-compose.yml")
    if not (bundle_root / "Dockerfile").is_file():
        raise DeploymentError("Release archive does not contain the backend Dockerfile")
    if not (bundle_root / "frontend" / "Dockerfile").is_file():
        raise DeploymentError(
            "Release archive does not contain the frontend Dockerfile"
        )
    if not (bundle_root / "config" / "config.example.toml").is_file():
        raise DeploymentError(
            "Release archive does not contain the example configuration"
        )
    return bundle_root


def _merge_dockerignore(path: Path, required_content: str) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    merged = list(existing)
    for entry in required_content.splitlines():
        if entry and entry not in merged:
            merged.append(entry)
    path.write_text("\n".join(merged) + "\n", encoding="utf-8")


def prepare_deployment(bundle_root: Path) -> None:
    config_path = bundle_root / "config" / "config.toml"
    if not config_path.exists():
        example_path = bundle_root / "config" / "config.example.toml"
        config_text = example_path.read_text(encoding="utf-8")
        config_text = config_text.replace(
            "http://localhost:", "http://host.docker.internal:"
        ).replace("http://127.0.0.1:", "http://host.docker.internal:")
        config_path.write_text(config_text, encoding="utf-8")

    override_path = bundle_root / "docker-compose.openmanus.yml"
    override_path.write_text(
        """services:
  web:
    extra_hosts:
      - "host.docker.internal:host-gateway"
  worker:
    extra_hosts:
      - "host.docker.internal:host-gateway"
""",
        encoding="utf-8",
    )
    _merge_dockerignore(bundle_root / ".dockerignore", ROOT_DOCKERIGNORE)
    _merge_dockerignore(
        bundle_root / "frontend" / ".dockerignore", FRONTEND_DOCKERIGNORE
    )


def install_release(
    target: Path,
    tag: str,
    *,
    asset_loader: Callable[[str], ReleaseAssets] | None = None,
    downloader: Callable[[str, Path], None] | None = None,
) -> tuple[Path, ReleaseAssets, str]:
    target = target.expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise DeploymentError(f"Deployment directory is not empty: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    load_assets = asset_loader or fetch_release_assets
    download = downloader or download_url
    assets = load_assets(tag)

    with tempfile.TemporaryDirectory(
        prefix=".openmanus-install-", dir=target.parent
    ) as temp_dir:
        working = Path(temp_dir)
        archive_path = working / assets.archive_name
        checksum_path = working / f"{assets.archive_name}.sha256"
        download(assets.archive_url, archive_path)
        download(assets.checksum_url, checksum_path)
        digest = verify_checksum(archive_path, checksum_path)
        bundle_root = extract_release_archive(archive_path, working / "extracted")
        stage = working / "deployment"
        shutil.copytree(bundle_root, stage)
        prepare_deployment(stage)
        if target.exists():
            target.rmdir()
        os.replace(stage, target)

    return target, assets, digest
