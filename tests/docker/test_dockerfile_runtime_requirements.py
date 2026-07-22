import re
from pathlib import Path

import pytest

from flocks.cli.service_config import build_service_config


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"
PORT_ENV_NAMES = (
    "FLOCKS_PORT",
    "FLOCKS_PUBLIC_PORT",
    "FLOCKS_WEBUI_PORT",
    "FLOCKS_FRONTEND_PORT",
    "FLOCKS_SERVER_PORT",
    "FLOCKS_BACKEND_PORT",
)


def _docker_port_env() -> dict[str, str]:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    values = {}
    for name in PORT_ENV_NAMES:
        match = re.search(rf"\b{re.escape(name)}=([^\s\\]+)", dockerfile)
        if match:
            values[name] = match.group(1)
    return values


def _resolve_docker_port(monkeypatch: pytest.MonkeyPatch, runtime_env: dict[str, str]) -> int:
    for name in PORT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in _docker_port_env().items():
        monkeypatch.setenv(name, value)
    for name, value in runtime_env.items():
        monkeypatch.setenv(name, value)
    config = build_service_config(default_server_host="127.0.0.1", default_server_port=8000)
    return config.backend_port


def test_runtime_image_no_longer_bundles_system_chromium() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium" not in dockerfile
    assert "    chromium \\" not in dockerfile


def test_runtime_image_uses_unified_supervised_service_port() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FLOCKS_BACKEND_HOST=0.0.0.0" in dockerfile
    assert "FLOCKS_BACKEND_PORT=5173" in dockerfile
    assert "FLOCKS_HOST=0.0.0.0" not in dockerfile
    assert "FLOCKS_PORT=5173" not in dockerfile
    assert "EXPOSE 5173" in dockerfile
    assert "EXPOSE 8000" not in dockerfile
    assert 'ENTRYPOINT ["/usr/bin/tini", "-g", "--"]' in dockerfile


@pytest.mark.parametrize(
    ("runtime_env", "expected_port"),
    [
        ({}, 5173),
        ({"FLOCKS_BACKEND_PORT": "6184"}, 6184),
        ({"FLOCKS_FRONTEND_PORT": "6184"}, 6184),
        ({"FLOCKS_PORT": "6184"}, 6184),
    ],
)
def test_runtime_port_overrides_remain_backward_compatible(
    monkeypatch: pytest.MonkeyPatch,
    runtime_env: dict[str, str],
    expected_port: int,
) -> None:
    assert _resolve_docker_port(monkeypatch, runtime_env) == expected_port
