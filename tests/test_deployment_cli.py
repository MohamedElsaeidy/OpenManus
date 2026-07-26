import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from openmanus_cli import cli
from openmanus_cli.deployment import (
    DeploymentError,
    ReleaseAssets,
    extract_release_archive,
    fetch_release_assets,
    install_release,
    prepare_deployment,
    release_tag_for_version,
    verify_checksum,
)


def _write_release_archive(path: Path) -> None:
    files = {
        "bundle/docker-compose.yml": b"services: {}\n",
        "bundle/Dockerfile": b"FROM scratch\n",
        "bundle/frontend/Dockerfile": b"FROM scratch\n",
        "bundle/config/config.example.toml": (
            b'base_url = "http://localhost:1234/v1"\n'
        ),
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def test_release_tag_for_version():
    assert release_tag_for_version("2.2.1") == "v2.2.1"
    assert release_tag_for_version("v2.2.1") == "v2.2.1"


def test_fetch_release_assets_requires_archive_and_matching_checksum(monkeypatch):
    payload = {
        "tag_name": "v2.2.1",
        "assets": [
            {
                "name": "openmanus-production-build.tar.gz",
                "browser_download_url": "https://example.test/archive",
            },
            {
                "name": "openmanus-production-build.tar.gz.sha256",
                "browser_download_url": "https://example.test/checksum",
            },
        ],
    }
    monkeypatch.setattr(
        "openmanus_cli.deployment._request_bytes",
        lambda _url: json.dumps(payload).encode(),
    )

    assets = fetch_release_assets("v2.2.1")

    assert assets.tag == "v2.2.1"
    assert assets.archive_url == "https://example.test/archive"
    assert assets.checksum_url == "https://example.test/checksum"


def test_verify_checksum_rejects_modified_archive(tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(b"modified")
    checksum = tmp_path / "bundle.tar.gz.sha256"
    checksum.write_text(f"{'0' * 64}  bundle.tar.gz\n", encoding="utf-8")

    with pytest.raises(DeploymentError, match="checksum mismatch"):
        verify_checksum(archive, checksum)


@pytest.mark.parametrize(
    "member_name",
    ["../outside.txt", "/absolute.txt", "..\\outside.txt", "C:/outside.txt"],
)
def test_extract_release_archive_rejects_path_traversal(tmp_path, member_name):
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo(member_name)
        content = b"unsafe"
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))

    with pytest.raises(DeploymentError, match="Unsafe path"):
        extract_release_archive(archive_path, tmp_path / "extract")


def test_install_release_verifies_and_prepares_bundle(tmp_path):
    source_archive = tmp_path / "source.tar.gz"
    _write_release_archive(source_archive)
    digest = hashlib.sha256(source_archive.read_bytes()).hexdigest()
    source_checksum = tmp_path / "source.tar.gz.sha256"
    source_checksum.write_text(f"{digest}  source.tar.gz\n", encoding="utf-8")
    assets = ReleaseAssets(
        tag="v2.2.1",
        archive_name="openmanus-production-test.tar.gz",
        archive_url="archive",
        checksum_url="checksum",
    )

    def downloader(url: str, destination: Path) -> None:
        shutil.copy2(
            source_archive if url == "archive" else source_checksum, destination
        )

    target, installed_assets, installed_digest = install_release(
        tmp_path / "deployment",
        "v2.2.1",
        asset_loader=lambda _tag: assets,
        downloader=downloader,
    )

    assert installed_assets == assets
    assert installed_digest == digest
    assert (target / "docker-compose.yml").is_file()
    assert (target / "docker-compose.openmanus.yml").is_file()
    assert "config/config.toml" in (target / ".dockerignore").read_text()
    assert "node_modules" in (target / "frontend" / ".dockerignore").read_text()
    config = (target / "config" / "config.toml").read_text(encoding="utf-8")
    assert "host.docker.internal:1234" in config


def test_install_release_preserves_existing_dockerignore_rules(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    ignore = bundle / ".dockerignore"
    ignore.write_text("custom-cache\n", encoding="utf-8")

    (bundle / "config").mkdir()
    (bundle / "config" / "config.example.toml").write_text("", encoding="utf-8")
    (bundle / "frontend").mkdir()
    prepare_deployment(bundle)

    rules = ignore.read_text(encoding="utf-8")
    assert "custom-cache" in rules
    assert "config/config.toml" in rules


def test_compose_command_uses_generated_override(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.openmanus.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )

    command = cli._compose_command(tmp_path, "ps")

    assert command[:4] == ["docker", "compose", "--project-directory", str(tmp_path)]
    assert str(tmp_path / "docker-compose.openmanus.yml") in command
    assert command[-1] == "ps"


def test_run_compose_sets_deployment_as_pwd(tmp_path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._run_compose(tmp_path, "ps") == 0
    assert captured["cwd"] == tmp_path
    assert captured["env"]["PWD"] == str(tmp_path)


def test_cli_returns_clear_error_for_uninitialized_directory(tmp_path, capsys):
    result = cli.main(["status", str(tmp_path)])

    assert result == 2
    assert "No OpenManus deployment found" in capsys.readouterr().err
