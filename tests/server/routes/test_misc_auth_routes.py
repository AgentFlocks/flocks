"""TUI-compatible ``PUT /auth/{provider_id}`` writes a provider's runtime API
key for everyone, so it is admin-only."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks.auth.service import AuthService

pytestmark = pytest.mark.asyncio

_BROWSER = {"sec-fetch-mode": "cors", "user-agent": "Mozilla/5.0", "Authorization": ""}


async def _member_cookie(client: AsyncClient) -> str:
    await AuthService.bootstrap_admin("admin", "Passw0rd!x")
    await AuthService._create_user_internal(username="member1", password="Member!Pass1", role="member")
    login = await client.post("/api/auth/login", json={"username": "member1", "password": "Member!Pass1"}, headers=_BROWSER)
    assert login.status_code == 200, login.text
    client.cookies.clear()
    return login.headers["set-cookie"].split(";")[0].split("=", 1)[1]


async def test_member_cannot_set_provider_auth(client: AsyncClient):
    sid = await _member_cookie(client)
    response = await client.put(
        "/auth/openai",
        json={"auth": {"type": "api", "key": "sk-member"}},
        headers={**_BROWSER, "cookie": f"flocks_session={sid}"},
    )
    assert response.status_code == 403
    assert response.json()["message"] == "仅管理员可执行该操作"


async def test_api_token_admin_can_set_provider_auth(client: AsyncClient):
    response = await client.put("/auth/unknown-provider-x", json={"auth": {"key": "x"}})
    assert response.status_code == 200 and response.json() == {"success": True}
