from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def file_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()

    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))

    from flocks.config.config import Config
    from flocks.workspace.manager import WorkspaceManager

    Config._global_config = None
    WorkspaceManager._instance = None

    from flocks.server.routes.file import router

    app = FastAPI()
    app.include_router(router, prefix="/api/file")

    yield TestClient(app, raise_server_exceptions=True), data, workspace

    Config._global_config = None
    WorkspaceManager._instance = None


def test_download_file_returns_binary_from_data_dir(file_client):
    client, data, _workspace = file_client
    image = data / "channel_media" / "wecom" / "image.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    response = client.get("/api/file/download", params={"path": str(image)})

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"
    assert response.headers["content-type"].startswith("image/png")


def test_oss_workspace_access_keeps_core_containment_policy(file_client):
    client, _data, workspace = file_client
    file = workspace / "users" / "local-user" / "note.txt"
    file.parent.mkdir(parents=True)
    file.write_text("OSS workspace content", encoding="utf-8")
    assert client.get("/api/file/content", params={"path": str(file)}).json()["content"] == "OSS workspace content"
    assert client.get("/api/file/download", params={"path": str(file)}).text == "OSS workspace content"
    assert client.get("/api/file/list", params={"path": str(workspace)}).status_code == 200


def test_list_skips_symlink_loop_instead_of_failing(file_client):
    client, _data, workspace = file_client
    (workspace / "note.txt").write_text("ok", encoding="utf-8")
    (workspace / "a").symlink_to(workspace / "b")
    (workspace / "b").symlink_to(workspace / "a")

    response = client.get("/api/file/list", params={"path": str(workspace)})

    assert response.status_code == 200
    names = {node["name"] for node in response.json()}
    assert "note.txt" in names
    assert not names & {"a", "b"}


def test_hit_checker_resolves_workspace_once_and_decides_each_path_once(
    file_client, monkeypatch: pytest.MonkeyPatch,
):
    _client, _data, workspace = file_client
    from flocks.server.routes import file as file_routes

    note = workspace / "note.txt"
    note.write_text("ok", encoding="utf-8")
    original = file_routes.assert_http_workspace_path_access
    calls = []

    def spy(path, **kwargs):
        calls.append((path, kwargs.get("workspace")))
        return original(path, **kwargs)

    monkeypatch.setattr(file_routes, "assert_http_workspace_path_access", spy)
    readable = file_routes._workspace_hit_checker()

    assert readable(str(note)) and readable(str(note))
    assert calls == [(str(note), workspace.resolve())]


def test_download_file_rejects_unallowed_path(file_client, tmp_path: Path):
    client, _data, _workspace = file_client
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"nope")

    response = client.get("/api/file/download", params={"path": str(secret)})

    assert response.status_code == 403


def test_read_directory_is_400_not_500(file_client):
    client, data, _workspace = file_client
    (data / "sub").mkdir()

    response = client.get("/api/file/content", params={"path": str(data / "sub")})

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Not a file: ")


def test_read_missing_file_does_not_echo_resolved_path(file_client):
    client, data, _workspace = file_client

    response = client.get("/api/file/content", params={"path": str(data / "missing.txt")})

    assert response.status_code == 404
    assert response.json()["detail"] == f"File not found: {data / 'missing.txt'}"


def test_list_internal_error_is_generic(file_client, monkeypatch: pytest.MonkeyPatch):
    client, data, _workspace = file_client
    from flocks.server.routes import file as file_routes

    async def boom(_path):
        raise RuntimeError("boom /secret/path")

    monkeypatch.setattr(file_routes.File, "list", boom)
    response = client.get("/api/file/list", params={"path": str(data)})

    assert response.status_code == 500
    assert "/secret/path" not in response.text
    assert response.json()["detail"] == "Internal server error"


def test_find_text_timeout_returns_empty(file_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import subprocess

    client, _data, _workspace = file_client
    from flocks.server.routes import file as file_routes

    (tmp_path / ".flocks").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)

    def slow_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(file_routes.subprocess if hasattr(file_routes, "subprocess") else subprocess, "run", slow_run)
    response = client.get("/api/file/find/text", params={"pattern": "x"})

    assert response.status_code == 200
    assert response.json() == []


def test_read_file_over_size_cap_is_413(file_client, monkeypatch: pytest.MonkeyPatch):
    client, data, _workspace = file_client
    from flocks.server.routes import file as file_routes

    big = data / "big.bin"
    big.write_bytes(b"x" * 2048)
    monkeypatch.setattr(file_routes, "_CONTENT_MAX_BYTES", 1024)

    response = client.get("/api/file/content", params={"path": str(big)})

    assert response.status_code == 413
    assert "use /api/file/download" in response.json()["detail"]
    # download is unaffected by the inline cap
    assert client.get("/api/file/download", params={"path": str(big)}).status_code == 200
