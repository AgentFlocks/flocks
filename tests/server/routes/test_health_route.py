"""
Tests for /health, the probe the open runtime answers.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.server.routes.health import public_router, router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(public_router)
    return TestClient(app, raise_server_exceptions=True)


def test_unprefixed_health_alias_is_mounted():
    """``/health`` is public in server/auth.py; it must resolve to the same handler as ``/api/health``."""
    client = _client()

    health = client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "healthy"
    assert client.get("/api/health").status_code == 200
    # Only the probe gets the alias; /ping stays under /api.
    assert client.get("/ping").status_code == 404
    # Readiness for a load balancer is an extension's endpoint, not the runtime's.
    assert client.get("/ready").status_code == 404
    assert client.get("/api/ready").status_code == 404


def test_health_answers_head_requests():
    """Load-balancer probes commonly use HEAD; it must not be a 405."""
    client = _client()

    for path in ("/api/health", "/health"):
        response = client.head(path)
        assert response.status_code == 200, path
        assert response.content == b"", path
        assert client.get(path).json()["status"] == "healthy"
