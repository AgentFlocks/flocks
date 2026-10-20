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


def test_dangling_symlink_does_not_fail_directory_listing(client, tmp_path):
    """One broken link must not turn the whole listing (or tree) into a 500."""
    c, ws, _ = client
    folder = ws / "中文 空格"
    folder.mkdir()
    (folder / "note.txt").write_text("kept")
    (folder / "outside-dir-link").symlink_to(tmp_path / "not-visible-to-the-server" / "gone")

    listing = c.get("/api/workspace/list", params={"path": "中文 空格"})
    assert listing.status_code == 200, listing.text
    entries = {item["name"]: item for item in listing.json()}
    assert entries["note.txt"]["type"] == "file"
    assert entries["outside-dir-link"]["type"] == "file"

    tree = c.get("/api/workspace/tree", params={"path": "中文 空格", "depth": 2})
    assert tree.status_code == 200, tree.text
    assert {child["name"] for child in tree.json()["children"]} == {"note.txt", "outside-dir-link"}


@pytest.mark.parametrize("bad_name", [".", ".."])
def test_upload_reports_dot_filenames_per_file(client, bad_name):
    """A "." or ".." filename fails on its own; the rest of the batch keeps its results."""
    c, ws, _ = client
    r = c.post(
        "/api/workspace/upload",
        files=[
            ("files", ("partial-ok.txt", b"ok", "text/plain")),
            ("files", (bad_name, b"x", "text/plain")),
            ("files", ("after.txt", b"after", "text/plain")),
        ],
    )
    assert r.status_code == 200, r.text
    first, bad, last = r.json()["uploaded"]
    assert first["name"] == "partial-ok.txt" and "error" not in first
    assert bad == {"name": bad_name, "error": "Filename is invalid"}
    assert last["name"] == "after.txt" and "error" not in last
    assert (ws / "partial-ok.txt").read_bytes() == b"ok"
    assert (ws / "after.txt").read_bytes() == b"after"


def test_upload_onto_existing_directory_reports_that_file(client):
    c, ws, _ = client
    (ws / "taken").mkdir()
    r = c.post(
        "/api/workspace/upload",
        files=[("files", ("taken", b"x", "text/plain")), ("files", ("fine.txt", b"y", "text/plain"))],
    )
    assert r.status_code == 200, r.text
    taken, fine = r.json()["uploaded"]
    assert taken["name"] == "taken" and taken["error"].startswith("Failed to save upload")
    assert str(ws) not in taken["error"]
    assert fine["name"] == "fine.txt" and "error" not in fine
    assert (ws / "taken").is_dir()


def test_tree_skips_a_directory_removed_after_the_scan(client, monkeypatch):
    """A child directory that vanishes between the scan and its own listing is left out."""
    from flocks.server.routes import workspace as workspace_routes

    c, ws, _ = client
    (ws / "parent" / "kept").mkdir(parents=True)
    (ws / "parent" / "gone").mkdir()
    original = workspace_routes._visible_children

    def vanishing(directory, root):
        if directory.name == "gone":
            directory.rmdir()  # removed by someone else between its stat and its listing
        return original(directory, root)

    monkeypatch.setattr(workspace_routes, "_visible_children", vanishing)
    tree = c.get("/api/workspace/tree", params={"path": "parent", "depth": 3})
    assert tree.status_code == 200, tree.text
    assert [child["name"] for child in tree.json()["children"]] == ["kept"]
