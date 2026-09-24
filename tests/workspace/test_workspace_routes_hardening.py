"""Regression tests for workspace route edge cases that used to answer 500 or
leak client input into response headers.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = tmp_path / "workspace"; data = tmp_path / "data"; mem = data / "memory"
    ws.mkdir(); data.mkdir(); mem.mkdir()
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(ws))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))
    from flocks.workspace.manager import WorkspaceManager
    from flocks.config.config import Config
    WorkspaceManager._instance = None
    Config._global_config = None
    from fastapi import FastAPI
    from flocks.auth.context import AuthUser
    from flocks.server.routes.workspace import router
    app = FastAPI()

    # The memory write route checks the user's projects for read-only paths,
    # so requests need an authenticated user like the real app provides.
    @app.middleware("http")
    async def inject_auth_user(request, call_next):
        request.state.auth_user = AuthUser(id="usr_hardening", username="hardening", role="member", status="active")
        return await call_next(request)

    app.include_router(router, prefix="/api/workspace")
    yield TestClient(app, raise_server_exceptions=False), ws, mem
    WorkspaceManager._instance = None
    Config._global_config = None


def test_create_dir_reports_existing_and_rejects_file_collision(client):
    c, ws, _ = client
    assert c.post("/api/workspace/dir", json={"path": "d"}).json() == {"path": "d", "created": True}
    assert c.post("/api/workspace/dir", json={"path": "d"}).json() == {"path": "d", "created": False}
    (ws / "f.txt").write_text("x")
    r = c.post("/api/workspace/dir", json={"path": "f.txt"})
    assert r.status_code == 409
    assert (ws / "f.txt").is_file()


@pytest.mark.parametrize("path", ["", " ", ".", "./"])
def test_create_dir_rejects_blank_path(client, path):
    c, _, _ = client
    assert c.post("/api/workspace/dir", json={"path": path}).status_code == 422


def test_write_file_on_directory_is_400(client):
    c, ws, mem = client
    (ws / "d").mkdir()
    r = c.put("/api/workspace/file", json={"path": "d", "content": "x"})
    assert r.status_code == 400 and "Not a file" in r.text
    (mem / "daily").mkdir()
    r = c.put("/api/workspace/memory/file", json={"path": "daily", "content": "x"})
    assert r.status_code == 400 and "Not a file" in r.text


def test_invalid_upload_limit_env_falls_back(client, monkeypatch):
    c, ws, _ = client
    monkeypatch.setenv("FLOCKS_WORKSPACE_MAX_UPLOAD_MB", "abc")
    r = c.post("/api/workspace/upload", files=[("files", ("s.txt", b"s", "text/plain"))])
    assert r.status_code == 200 and r.json()["uploaded"][0]["size"] == 1
    monkeypatch.setenv("FLOCKS_WORKSPACE_MAX_READ_BYTES", "-5")
    (ws / "s.txt").write_text("hello")
    r = c.get("/api/workspace/file", params={"path": "s.txt"})
    assert r.status_code == 200 and r.json()["content"] == "hello"


def test_zip_reports_skipped_paths_and_sanitises_archive_name(client):
    c, ws, _ = client
    (ws / "ok.txt").write_text("ok")
    (ws / "dir").mkdir()
    r = c.post(
        "/api/workspace/download/zip",
        json={"paths": ["ok.txt", "../../etc/passwd", "missing.txt", "dir"], "archive_name": 'x";\r\nX-Evil: 1.zip'},
    )
    assert r.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["ok.txt"]
    assert json.loads(r.headers["X-Skipped-Paths"]) == ["../../etc/passwd", "missing.txt", "dir"]
    assert "X-Evil" not in r.headers
    disposition = r.headers["Content-Disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition == 'attachment; filename="xX-Evil: 1.zip"'
    r = c.post("/api/workspace/download/zip", json={"paths": ["ok.txt"], "archive_name": "../../"})
    assert r.headers["Content-Disposition"] == 'attachment; filename="workspace_files.zip"'


def test_move_directory_into_itself_is_400(client):
    c, ws, _ = client
    (ws / "a" / "b").mkdir(parents=True)
    r = c.post("/api/workspace/move", json={"src": "a", "dst": "a/b/c"})
    assert r.status_code == 400 and "into itself" in r.text
    assert (ws / "a" / "b").is_dir()
