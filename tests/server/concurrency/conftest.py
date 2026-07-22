"""
Fixtures for concurrency tests.
The isolation (isolated_env) is now provided by tests/server/conftest.py.
This file only provides concurrency-specific helpers: client, session_id.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from flocks.server.app import app
    transport = ASGITransport(app=app)
    headers = {"Authorization": "Bearer abc123", "User-Agent": "curl/8.0"}
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def _concurrency_test_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a valid API token for concurrency tests."""
    from flocks.server import auth as auth_module

    class SecretManagerStub:
        def __init__(self, values: dict[str, str]):
            self._values = values

        def get(self, key: str):
            return self._values.get(key)

    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: SecretManagerStub({auth_module.API_TOKEN_SECRET_ID: "abc123"}),
    )


@pytest.fixture
async def session_id(client: AsyncClient) -> str:
    resp = await client.post("/api/session", json={"title": "concurrency-test"})
    assert resp.status_code == 200
    return resp.json()["id"]
