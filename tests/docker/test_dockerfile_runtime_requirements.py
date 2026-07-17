from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile"


def test_runtime_image_no_longer_bundles_system_chromium() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium" not in dockerfile
    assert "    chromium \\" not in dockerfile


def test_runtime_image_uses_unified_supervised_service_port() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FLOCKS_HOST=0.0.0.0" in dockerfile
    assert "FLOCKS_PORT=5173" in dockerfile
    assert "EXPOSE 5173" in dockerfile
    assert "EXPOSE 8000" not in dockerfile
    assert 'ENTRYPOINT ["/usr/bin/tini", "-g", "--"]' in dockerfile
