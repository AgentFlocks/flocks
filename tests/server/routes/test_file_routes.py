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


@pytest.fixture()
def private_path_policy(monkeypatch: pytest.MonkeyPatch):
    from flocks.utils import http_file_read_guard as guard

    monkeypatch.setattr(guard, "_private_path_providers", {})
    return guard


@pytest.mark.parametrize("route", ["content", "download"])
@pytest.mark.parametrize("alias", [False, True])
def test_extension_private_paths_override_a_broad_allow_root(
    file_client, private_path_policy, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, route: str, alias: bool,
):
    from flocks.config.config import Config, ConfigInfo
    from flocks.extensions import register_http_private_path_provider

    client, data, _workspace = file_client
    private = tmp_path / "extension-state" / "private.json"
    private.parent.mkdir()
    private.write_text("private extension value", encoding="utf-8")
    path = data / "harmless-name.json" if alias else private
    if alias:
        path.symlink_to(private)
    seen = []

    def is_private(resolved: Path) -> bool:
        seen.append(resolved)
        return resolved == private.resolve()

    async def config():
        return ConfigInfo.model_validate({"allowReadPaths": [str(tmp_path)]})

    monkeypatch.setattr(Config, "get", config)
    register_http_private_path_provider("example", is_private)
    response = client.get("/api/file/" + route, params={"path": str(path)})
    assert response.status_code == 403
    assert "private extension value" not in response.text
    assert private.resolve() in seen


def test_private_provider_does_not_block_unrelated_files_and_can_be_replaced(file_client, private_path_policy):
    from flocks.extensions import register_http_private_path_provider, unregister_http_private_path_provider

    client, data, _workspace = file_client
    private = data / "private.json"
    public = data / "public.txt"
    private.write_text("private value", encoding="utf-8")
    public.write_text("public value", encoding="utf-8")
    register_http_private_path_provider("example", lambda path: path == private.resolve())
    assert client.get("/api/file/content", params={"path": str(public)}).json()["content"] == "public value"
    names = {node["name"] for node in client.get("/api/file/list", params={"path": str(data)}).json()}
    assert "public.txt" in names and "private.json" not in names
    register_http_private_path_provider("example", lambda _path: False)
    assert client.get("/api/file/content", params={"path": str(private)}).status_code == 200
    assert unregister_http_private_path_provider("example") is True
    assert unregister_http_private_path_provider("example") is False


@pytest.mark.parametrize("result", [None, "false", "raises"])
def test_unavailable_private_policy_fails_closed_without_exposing_its_error(file_client, private_path_policy, result):
    from flocks.extensions import register_http_private_path_provider

    client, data, _workspace = file_client
    file = data / "private.json"
    file.write_text("sensitive data", encoding="utf-8")

    def predicate(_path):
        if result == "raises":
            raise RuntimeError("internal secret detail")
        return result

    register_http_private_path_provider("example", predicate)
    response = client.get("/api/file/content", params={"path": str(file)})
    assert response.status_code == 403
    assert "secret" not in response.text and "sensitive" not in response.text


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("kind", ["text", "file"])
def test_compatibility_search_filters_private_hits_under_an_allowed_directory(
    file_client, private_path_policy, monkeypatch: pytest.MonkeyPatch, fallback: bool, kind: str,
):
    import json
    import subprocess

    from flocks.extensions import register_http_private_path_provider
    from flocks.server.routes import find as find_routes

    client, data, _workspace = file_client
    private = data / "private.txt"
    public = data / "public.txt"
    private.write_text("private needle value", encoding="utf-8")
    public.write_text("public needle value", encoding="utf-8")
    register_http_private_path_provider("example", lambda path: path == private.resolve())
    client.app.include_router(find_routes.router, prefix="/find")

    def run(command, **_kwargs):
        if fallback and command[0] in {"rg", "fd"}:
            raise FileNotFoundError("exercise fallback")
        if kind == "file":
            output = "private.txt\npublic.txt\n"
        elif command[0] == "rg":
            output = "\n".join(json.dumps({"type": "match", "data": {
                "path": {"text": name}, "line_number": 1, "lines": {"text": content},
            }}) for name, content in [("private.txt", "private needle value"), ("public.txt", "public needle value")])
        else:
            output = "private.txt:1:private needle value\npublic.txt:1:public needle value\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(find_routes.subprocess, "run", run)
    path = "/find" if kind == "text" else "/find/file"
    params = {"directory": str(data), "pattern" if kind == "text" else "query": "needle"}
    response = client.get(path, params=params)
    assert response.status_code == 200
    assert "private" not in response.text
    assert "public.txt" in response.text
