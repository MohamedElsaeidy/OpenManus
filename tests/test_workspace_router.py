"""Regression tests for the workspace browse/download API.

The download route used to be missing entirely: "/api/workspace/download/x.pdf"
fell through to the catch-all listing route, resolved to a nonexistent
"<workspace>/download/x.pdf", and returned an empty JSON listing with HTTP 200.
Browsers happily saved that as the file, so every download came out as a 2-byte
"[]" — including LaTeX PDFs, which then would not open.
"""

import io
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server.api import deps
from server.api.routers import workspace


PDF_BYTES = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n1 0 obj\nendobj\n%%EOF\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    task_dir = tmp_path / "conversations" / "abc"
    task_dir.mkdir(parents=True)
    (task_dir / "paper.pdf").write_bytes(PDF_BYTES)
    (task_dir / "paper.tex").write_text("\\documentclass{article}")
    (task_dir / "my report.tex").write_text("spaces in the name")

    monkeypatch.setattr(deps, "WORKSPACE_ROOT", str(tmp_path))

    app = FastAPI()
    app.include_router(workspace.router)
    return TestClient(app)


def test_download_returns_the_actual_file_bytes(client):
    response = client.get("/api/workspace/download/conversations/abc/paper.pdf")

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]


def test_download_of_a_directory_returns_a_zip(client):
    response = client.get("/api/workspace/download/conversations/abc")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert sorted(names) == ["my report.tex", "paper.pdf", "paper.tex"]


def test_download_of_a_name_with_spaces(client):
    response = client.get("/api/workspace/download/conversations/abc/my%20report.tex")

    assert response.status_code == 200
    assert response.content == b"spaces in the name"


def test_preview_serves_file_inline(client):
    response = client.get("/api/workspace/conversations/abc/paper.pdf")

    assert response.status_code == 200
    assert response.content == PDF_BYTES
    assert "attachment" not in response.headers.get("content-disposition", "")


def test_directory_listing(client):
    response = client.get("/api/workspace/conversations/abc")

    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()] == [
        "my report.tex",
        "paper.pdf",
        "paper.tex",
    ]
    assert all(entry["type"] == "file" for entry in response.json())


def test_missing_path_is_404_not_an_empty_listing(client):
    response = client.get("/api/workspace/conversations/abc/nope.pdf")

    assert response.status_code == 404
    assert client.get("/api/workspace/download/nope.pdf").status_code == 404


def test_root_of_an_unwritten_workspace_lists_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "WORKSPACE_ROOT", str(tmp_path / "not-created-yet"))
    app = FastAPI()
    app.include_router(workspace.router)

    response = TestClient(app).get("/api/workspace/")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "path",
    [
        "conversations/abc/paper.pdf",
        "/conversations/abc/paper.pdf",
        # Agent events report paths as seen from inside the sandbox container
        # ("/workspace/...") or from inside the API container
        # ("/app/workspace/..."). Both name the same file on the shared volume.
        "/workspace/conversations/abc/paper.pdf",
        "/app/workspace/conversations/abc/paper.pdf",
    ],
)
def test_agent_reported_paths_resolve_to_the_same_file(client, path):
    resolved = workspace._resolve(path)

    assert resolved.read_bytes() == PDF_BYTES


def test_save_writes_file_and_reports_size(client, tmp_path):
    response = client.put(
        "/api/workspace/file/conversations/abc/paper.tex",
        json={"content": "\\documentclass{report}"},
    )

    assert response.status_code == 200
    assert response.json()["path"] == "conversations/abc/paper.tex"
    assert (
        tmp_path / "conversations/abc/paper.tex"
    ).read_text() == "\\documentclass{report}"


def test_save_creates_missing_parent_directories(client, tmp_path):
    response = client.put(
        "/api/workspace/file/conversations/abc/notes/new.md", json={"content": "# New"}
    )

    assert response.status_code == 200
    assert (tmp_path / "conversations/abc/notes/new.md").read_text() == "# New"


def test_save_leaves_no_temp_files_behind(client, tmp_path):
    client.put("/api/workspace/file/conversations/abc/paper.tex", json={"content": "x"})

    leftovers = [
        p.name for p in (tmp_path / "conversations/abc").iterdir() if ".tmp" in p.name
    ]
    assert leftovers == []


def test_save_preserves_the_existing_file_mode(client, tmp_path):
    """os.replace inherits the temp file's 0600, which would lock other uids out."""
    target = tmp_path / "conversations/abc/paper.tex"
    target.chmod(0o644)

    client.put("/api/workspace/file/conversations/abc/paper.tex", json={"content": "x"})

    assert target.stat().st_mode & 0o777 == 0o644


def test_new_files_are_group_and_world_readable(client, tmp_path):
    client.put("/api/workspace/file/conversations/abc/fresh.txt", json={"content": "x"})

    assert (tmp_path / "conversations/abc/fresh.txt").stat().st_mode & 0o777 == 0o644


def test_save_rejects_a_directory_target(client):
    response = client.put(
        "/api/workspace/file/conversations/abc", json={"content": "x"}
    )

    assert response.status_code == 400


def test_save_refuses_to_escape_the_workspace(client, tmp_path):
    response = client.put(
        "/api/workspace/file/../../escaped.txt", json={"content": "should not land"}
    )

    assert response.status_code in (400, 404)
    assert not (tmp_path.parent / "escaped.txt").exists()


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "conversations/../../outside.txt",
        # A sibling whose name merely starts with the root's name is not inside
        # it — the old string-prefix containment check let this through.
        "../workspace_secret/keys.txt",
    ],
)
def test_path_traversal_is_rejected(client, path):
    with pytest.raises(HTTPException) as excinfo:
        workspace._resolve(path)

    assert excinfo.value.status_code == 400


def test_absolute_paths_outside_the_workspace_stay_contained(client):
    """/etc/passwd is treated as a workspace-relative path, never the real one."""
    assert str(workspace._resolve("/etc/passwd")).endswith("/etc/passwd")
    assert workspace._workspace_root() in workspace._resolve("/etc/passwd").parents
