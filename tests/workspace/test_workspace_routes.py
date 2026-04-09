"""
Integration tests for Workspace API routes

Uses FastAPI's TestClient (synchronous, no live server needed).
Each test class gets a fresh isolated workspace via the `workspace_client` fixture.

Covered endpoints
-----------------
Directory:  GET /tree, GET /list, POST /dir, DELETE /dir
File:       POST /upload, GET /file, PUT /file, DELETE /file,
            GET /download, POST /download/zip, POST /move
Memory:     GET /memory/list, GET /memory/file
Stats:      GET /stats
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def workspace_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Return a TestClient pointed at the workspace router with an isolated
    temp workspace + memory directory.

    Two caches must be reset between tests:
    - WorkspaceManager._instance  (workspace module singleton)
    - Config._global_config       (flocks config module singleton, caches data_dir)
    """
    ws = tmp_path / "workspace"
    data = tmp_path / "data"
    mem = data / "memory"
    ws.mkdir()
    data.mkdir()
    mem.mkdir()
    # Pre-create conventional workspace subdirs so tests can write files directly
    for sub in ("outputs", "knowledge"):
        (ws / sub).mkdir()

    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(ws))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))

    # Reset both singletons so they re-read env vars
    from flocks.workspace.manager import WorkspaceManager
    from flocks.config.config import Config
    WorkspaceManager._instance = None
    Config._global_config = None

    # Build a minimal FastAPI app with only the workspace router
    from fastapi import FastAPI
    from flocks.server.routes.workspace import router

    app = FastAPI()
    app.include_router(router, prefix="/api/workspace")

    client = TestClient(app, raise_server_exceptions=True)

    yield client, ws, mem

    # Cleanup
    WorkspaceManager._instance = None
    Config._global_config = None


# Convenience unpacking helpers used in every test
def _client(fixture): return fixture[0]
def _ws(fixture): return fixture[1]
def _mem(fixture): return fixture[2]


# ─── Stats ───────────────────────────────────────────────────────────────────

class TestStats:
    def test_empty_stats(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["file_count"] == 0
        assert data["dir_count"] >= 2  # outputs/ knowledge/
        assert data["total_size_bytes"] == 0
        assert data["memory_file_count"] == 0

    def test_stats_reflect_uploaded_file(self, workspace_client):
        client = _client(workspace_client)
        client.post("/api/workspace/upload?dest=outputs", files=[("files", ("a.txt", b"hello", "text/plain"))])
        r = client.get("/api/workspace/stats")
        assert r.status_code == 200
        assert r.json()["file_count"] == 1
        assert r.json()["total_size_bytes"] == 5  # len("hello")


# ─── Directory operations ─────────────────────────────────────────────────────

class TestDirList:
    def test_list_root(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/list")
        assert r.status_code == 200
        names = {n["name"] for n in r.json()}
        assert {"outputs", "knowledge"}.issubset(names)

    def test_list_subdir(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "sub.txt").write_text("hi")
        r = _client(workspace_client).get("/api/workspace/list?path=outputs")
        assert r.status_code == 200
        assert any(n["name"] == "sub.txt" for n in r.json())

    def test_list_nonexistent_returns_404(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/list?path=does_not_exist")
        assert r.status_code == 404

    def test_list_file_path_returns_400(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "file.txt").write_text("x")
        r = _client(workspace_client).get("/api/workspace/list?path=file.txt")
        assert r.status_code == 400


class TestDirTree:
    def test_tree_root(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/tree")
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "directory"
        assert "children" in data
        # Root node path must be '' (not '.') so the frontend can use it directly
        assert data["path"] == ""

    def test_tree_depth_param(self, workspace_client):
        ws = _ws(workspace_client)
        deep = ws / "a" / "b" / "c"
        deep.mkdir(parents=True)
        r = _client(workspace_client).get("/api/workspace/tree?depth=1")
        assert r.status_code == 200
        # depth=1: children of root listed but not recursed into
        children_names = {c["name"] for c in (r.json().get("children") or [])}
        assert "a" in children_names

    def test_tree_nonexistent_returns_404(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/tree?path=nope")
        assert r.status_code == 404


class TestDirCreate:
    def test_create_new_dir(self, workspace_client):
        client = _client(workspace_client)
        r = client.post("/api/workspace/dir", json={"path": "outputs/reports"})
        assert r.status_code == 200
        assert r.json()["created"] is True
        assert (_ws(workspace_client) / "outputs" / "reports").is_dir()

    def test_create_nested_dir(self, workspace_client):
        r = _client(workspace_client).post("/api/workspace/dir", json={"path": "outputs/2026/03"})
        assert r.status_code == 200
        assert (_ws(workspace_client) / "outputs" / "2026" / "03").is_dir()

    def test_create_dir_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).post("/api/workspace/dir", json={"path": "../../evil"})
        assert r.status_code == 400

    def test_create_dir_absolute_rejected(self, workspace_client):
        r = _client(workspace_client).post("/api/workspace/dir", json={"path": "/tmp/evil"})
        assert r.status_code == 400


class TestDirDelete:
    def test_delete_existing_dir(self, workspace_client):
        ws = _ws(workspace_client)
        d = ws / "to_delete"
        d.mkdir()
        r = _client(workspace_client).delete("/api/workspace/dir?path=to_delete")
        assert r.status_code == 200
        assert not d.exists()

    def test_delete_dir_with_contents(self, workspace_client):
        ws = _ws(workspace_client)
        d = ws / "full_dir"
        d.mkdir()
        (d / "file.txt").write_text("content")
        r = _client(workspace_client).delete("/api/workspace/dir?path=full_dir")
        assert r.status_code == 200
        assert not d.exists()

    def test_delete_nonexistent_dir_returns_404(self, workspace_client):
        r = _client(workspace_client).delete("/api/workspace/dir?path=ghost")
        assert r.status_code == 404

    def test_delete_workspace_root_rejected(self, workspace_client):
        r = _client(workspace_client).delete("/api/workspace/dir?path=")
        # Empty path resolves to workspace root — must be rejected
        assert r.status_code in (400, 422)

    def test_delete_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).delete("/api/workspace/dir?path=../../etc")
        assert r.status_code == 400


# ─── File upload ─────────────────────────────────────────────────────────────

class TestUpload:
    def test_upload_text_file(self, workspace_client):
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload?dest=outputs",
            files=[("files", ("hello.txt", b"hello world", "text/plain"))],
        )
        assert r.status_code == 200
        result = r.json()["uploaded"][0]
        assert result["name"] == "hello.txt"
        assert result["size"] == 11
        assert result["is_text_file"] is True
        assert result.get("error") is None
        assert (_ws(workspace_client) / "outputs" / "hello.txt").read_text() == "hello world"

    def test_upload_binary_file_succeeds_without_chat_purpose(self, workspace_client):
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload",
            files=[("files", ("archive.zip", b"\x50\x4b\x03\x04", "application/zip"))],
        )
        assert r.status_code == 200
        result = r.json()["uploaded"][0]
        assert result.get("error") is None
        assert result["name"] == "archive.zip"
        assert (_ws(workspace_client) / "archive.zip").exists()

    def test_chat_upload_rejects_disallowed_file_type(self, workspace_client):
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload?purpose=chat",
            files=[("files", ("archive.zip", b"\x50\x4b\x03\x04", "application/zip"))],
        )
        assert r.status_code == 200
        result = r.json()["uploaded"][0]
        assert "Unsupported file type" in result["error"]
        assert not (_ws(workspace_client) / "archive.zip").exists()

    def test_upload_multiple_files(self, workspace_client):
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload?dest=knowledge",
            files=[
                ("files", ("a.md", b"# A", "text/plain")),
                ("files", ("b.json", b'{"x":1}', "application/json")),
            ],
        )
        assert r.status_code == 200
        uploaded = r.json()["uploaded"]
        assert len(uploaded) == 2
        names = {u["name"] for u in uploaded}
        assert names == {"a.md", "b.json"}

    def test_upload_strips_directory_from_client_filename(self, workspace_client):
        """A client sending '../../../etc/passwd' as filename must be sandboxed."""
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload",
            files=[("files", ("../../../evil.txt", b"evil", "text/plain"))],
        )
        assert r.status_code == 200
        # The file should be saved as just "evil.txt" in the workspace root
        uploaded = r.json()["uploaded"]
        assert uploaded[0]["name"] == "evil.txt"

    def test_upload_to_nonexistent_dest_creates_it(self, workspace_client):
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload?dest=new_folder",
            files=[("files", ("x.txt", b"x", "text/plain"))],
        )
        assert r.status_code == 200
        assert (_ws(workspace_client) / "new_folder" / "x.txt").exists()

    def test_upload_overwrites_duplicate_file_without_chat_purpose(self, workspace_client):
        client = _client(workspace_client)
        first = client.post(
            "/api/workspace/upload?dest=uploads",
            files=[("files", ("report.pdf", b"first", "application/pdf"))],
        )
        second = client.post(
            "/api/workspace/upload?dest=uploads",
            files=[("files", ("report.pdf", b"second", "application/pdf"))],
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_item = first.json()["uploaded"][0]
        second_item = second.json()["uploaded"][0]
        assert first_item["name"] == "report.pdf"
        assert second_item["name"] == "report.pdf"
        assert first_item["path"] == "uploads/report.pdf"
        assert second_item["path"] == "uploads/report.pdf"
        assert (_ws(workspace_client) / "uploads" / "report.pdf").read_bytes() == b"second"

    def test_chat_upload_renames_duplicate_file(self, workspace_client):
        client = _client(workspace_client)
        first = client.post(
            "/api/workspace/upload?dest=uploads&purpose=chat",
            files=[("files", ("report.pdf", b"first", "application/pdf"))],
        )
        second = client.post(
            "/api/workspace/upload?dest=uploads&purpose=chat",
            files=[("files", ("report.pdf", b"second", "application/pdf"))],
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_item = first.json()["uploaded"][0]
        second_item = second.json()["uploaded"][0]
        assert first_item["name"] == "report.pdf"
        assert second_item["name"] == "report (1).pdf"
        assert first_item["path"] == "uploads/report.pdf"
        assert second_item["path"] == "uploads/report (1).pdf"
        assert (_ws(workspace_client) / "uploads" / "report.pdf").read_bytes() == b"first"
        assert (_ws(workspace_client) / "uploads" / "report (1).pdf").read_bytes() == b"second"

    def test_chat_upload_returns_error_after_too_many_name_conflicts(self, workspace_client, monkeypatch):
        from flocks.server.routes import workspace as workspace_routes

        monkeypatch.setattr(workspace_routes, "_MAX_UPLOAD_RENAME_ATTEMPTS", 1)
        ws = _ws(workspace_client)
        uploads_dir = ws / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        (uploads_dir / "report.pdf").write_bytes(b"first")
        (uploads_dir / "report (1).pdf").write_bytes(b"second")

        r = _client(workspace_client).post(
            "/api/workspace/upload?dest=uploads&purpose=chat",
            files=[("files", ("report.pdf", b"third", "application/pdf"))],
        )

        assert r.status_code == 409
        assert "Too many conflicting filenames" in r.json()["detail"]
        result = r.json()["uploaded"][0]
        assert "Too many conflicting filenames" in result["error"]
        assert (uploads_dir / "report.pdf").read_bytes() == b"first"
        assert (uploads_dir / "report (1).pdf").read_bytes() == b"second"

    def test_upload_too_large_file_rejected(self, workspace_client, monkeypatch):
        # Set the limit to 0 MB; _max_upload_bytes() reads the env var at
        # request time so no module-level patching is needed.
        monkeypatch.setenv("FLOCKS_WORKSPACE_MAX_UPLOAD_MB", "0")
        client = _client(workspace_client)
        r = client.post(
            "/api/workspace/upload",
            files=[("files", ("big.txt", b"x", "text/plain"))],
        )
        assert r.status_code == 200
        result = r.json()["uploaded"][0]
        assert result.get("error") is not None


# ─── File read ───────────────────────────────────────────────────────────────

class TestReadFile:
    def test_read_text_file(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "note.md").write_text("# Hello\nWorld")
        r = _client(workspace_client).get("/api/workspace/file?path=outputs/note.md")
        assert r.status_code == 200
        data = r.json()
        assert data["path"] == "outputs/note.md"
        assert data["content"] == "# Hello\nWorld"

    def test_read_nonexistent_returns_404(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/file?path=ghost.txt")
        assert r.status_code == 404

    def test_read_binary_file_returns_400(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "archive.zip").write_bytes(b"\x50\x4b\x03\x04")
        r = _client(workspace_client).get("/api/workspace/file?path=archive.zip")
        assert r.status_code == 400

    def test_read_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/file?path=../../etc/passwd")
        assert r.status_code == 400

    def test_read_directory_returns_400(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/file?path=outputs")
        assert r.status_code == 400


# ─── File write ──────────────────────────────────────────────────────────────

class TestWriteFile:
    def test_write_new_file(self, workspace_client):
        client = _client(workspace_client)
        r = client.put("/api/workspace/file", json={"path": "outputs/result.md", "content": "# Done"})
        assert r.status_code == 200
        assert r.json()["written"] is True
        assert (_ws(workspace_client) / "outputs" / "result.md").read_text() == "# Done"

    def test_overwrite_existing_file(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "f.txt").write_text("old")
        client = _client(workspace_client)
        r = client.put("/api/workspace/file", json={"path": "outputs/f.txt", "content": "new"})
        assert r.status_code == 200
        assert (ws / "outputs" / "f.txt").read_text() == "new"

    def test_write_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).put(
            "/api/workspace/file",
            json={"path": "../../evil.txt", "content": "x"},
        )
        assert r.status_code == 400

    def test_write_creates_parent_dirs(self, workspace_client):
        client = _client(workspace_client)
        r = client.put(
            "/api/workspace/file",
            json={"path": "outputs/deep/nested/file.json", "content": "{}"},
        )
        assert r.status_code == 200
        assert (_ws(workspace_client) / "outputs" / "deep" / "nested" / "file.json").exists()


# ─── File delete ─────────────────────────────────────────────────────────────

class TestDeleteFile:
    def test_delete_existing_file(self, workspace_client):
        ws = _ws(workspace_client)
        f = ws / "outputs" / "del.txt"
        f.write_text("bye")
        r = _client(workspace_client).delete("/api/workspace/file?path=outputs/del.txt")
        assert r.status_code == 200
        assert not f.exists()

    def test_delete_nonexistent_returns_404(self, workspace_client):
        r = _client(workspace_client).delete("/api/workspace/file?path=ghost.txt")
        assert r.status_code == 404

    def test_delete_directory_returns_400(self, workspace_client):
        r = _client(workspace_client).delete("/api/workspace/file?path=outputs")
        assert r.status_code == 400

    def test_delete_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).delete("/api/workspace/file?path=../../etc/hosts")
        assert r.status_code == 400


# ─── File download ────────────────────────────────────────────────────────────

class TestDownload:
    def test_download_single_file(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "doc.pdf").write_bytes(b"%PDF-1.4")
        r = _client(workspace_client).get("/api/workspace/download?path=outputs/doc.pdf")
        assert r.status_code == 200
        assert r.content == b"%PDF-1.4"
        assert "attachment" in r.headers.get("content-disposition", "")

    def test_download_nonexistent_returns_404(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/download?path=missing.pdf")
        assert r.status_code == 404

    def test_download_zip_multiple_files(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "a.txt").write_text("aaa")
        (ws / "knowledge" / "b.txt").write_text("bbb")
        r = _client(workspace_client).post(
            "/api/workspace/download/zip",
            json={"paths": ["outputs/a.txt", "knowledge/b.txt"]},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        import zipfile, io
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        assert "outputs/a.txt" in names
        assert "knowledge/b.txt" in names

    def test_download_zip_skips_invalid_paths(self, workspace_client):
        """Invalid / traversal paths in zip request are silently skipped."""
        r = _client(workspace_client).post(
            "/api/workspace/download/zip",
            json={"paths": ["../../etc/passwd", "nonexistent.txt"]},
        )
        assert r.status_code == 200
        import zipfile, io
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert zf.namelist() == []


# ─── Move / rename ────────────────────────────────────────────────────────────

class TestMove:
    def test_move_file(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "old.txt").write_text("hello")
        r = _client(workspace_client).post(
            "/api/workspace/move",
            json={"src": "outputs/old.txt", "dst": "knowledge/new.txt"},
        )
        assert r.status_code == 200
        assert r.json()["moved"] is True
        assert not (ws / "outputs" / "old.txt").exists()
        assert (ws / "knowledge" / "new.txt").read_text() == "hello"

    def test_move_directory(self, workspace_client):
        ws = _ws(workspace_client)
        d = ws / "outputs" / "subdir"
        d.mkdir()
        (d / "file.txt").write_text("x")
        r = _client(workspace_client).post(
            "/api/workspace/move",
            json={"src": "outputs/subdir", "dst": "knowledge/subdir"},
        )
        assert r.status_code == 200
        assert (ws / "knowledge" / "subdir" / "file.txt").read_text() == "x"

    def test_move_nonexistent_src_returns_404(self, workspace_client):
        r = _client(workspace_client).post(
            "/api/workspace/move",
            json={"src": "ghost.txt", "dst": "outputs/ghost.txt"},
        )
        assert r.status_code == 404

    def test_move_to_existing_dst_returns_409(self, workspace_client):
        ws = _ws(workspace_client)
        (ws / "outputs" / "src.txt").write_text("a")
        (ws / "outputs" / "dst.txt").write_text("b")
        r = _client(workspace_client).post(
            "/api/workspace/move",
            json={"src": "outputs/src.txt", "dst": "outputs/dst.txt"},
        )
        assert r.status_code == 409

    def test_move_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).post(
            "/api/workspace/move",
            json={"src": "../../etc/passwd", "dst": "outputs/passwd"},
        )
        assert r.status_code == 400


# ─── Memory view (read-only) ─────────────────────────────────────────────────

class TestMemoryView:
    def test_list_memory_empty(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/memory/list")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_memory_with_files(self, workspace_client):
        mem = _mem(workspace_client)
        (mem / "MEMORY.md").write_text("# Memory")
        (mem / "2026-03-14.md").write_text("## Daily")
        r = _client(workspace_client).get("/api/workspace/memory/list")
        assert r.status_code == 200
        names = {n["name"] for n in r.json()}
        assert {"MEMORY.md", "2026-03-14.md"}.issubset(names)
        # All returned nodes should be text files
        for node in r.json():
            assert node["is_text_file"] is True

    def test_read_memory_file(self, workspace_client):
        mem = _mem(workspace_client)
        (mem / "MEMORY.md").write_text("# Key facts\n- item1")
        r = _client(workspace_client).get("/api/workspace/memory/file?path=MEMORY.md")
        assert r.status_code == 200
        data = r.json()
        assert data["path"] == "MEMORY.md"
        assert "Key facts" in data["content"]

    def test_read_memory_nonexistent_returns_404(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/memory/file?path=ghost.md")
        assert r.status_code == 404

    def test_memory_traversal_rejected(self, workspace_client):
        r = _client(workspace_client).get("/api/workspace/memory/file?path=../../etc/passwd")
        assert r.status_code == 400

    def test_memory_nested_file(self, workspace_client):
        mem = _mem(workspace_client)
        (mem / "daily").mkdir(exist_ok=True)
        (mem / "daily" / "2026-03-14.md").write_text("daily note")
        r = _client(workspace_client).get("/api/workspace/memory/file?path=daily/2026-03-14.md")
        assert r.status_code == 200
        assert r.json()["content"] == "daily note"

    def test_memory_write_not_allowed(self, workspace_client):
        """Memory directory has no write endpoint — PUT /file with memory path is confined to workspace."""
        # Trying to write to memory via workspace file endpoint should be rejected
        # because memory dir is outside workspace dir
        mem_path_attempt = "../data/memory/MEMORY.md"
        r = _client(workspace_client).put(
            "/api/workspace/file",
            json={"path": mem_path_attempt, "content": "hacked"},
        )
        assert r.status_code == 400
