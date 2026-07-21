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


def test_download_file_rejects_unallowed_path(file_client, tmp_path: Path):
    client, _data, _workspace = file_client
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"nope")

    response = client.get("/api/file/download", params={"path": str(secret)})

    assert response.status_code == 403
