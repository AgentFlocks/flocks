from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
async def _bootstrap_admin():
    """Every test in this module needs a bootstrapped admin user."""
    from flocks.auth.service import AuthService

    if not await AuthService.has_users():
        await AuthService.bootstrap_admin(username="admin", password="Password123!")


@pytest.mark.asyncio
async def test_admin_routes_list_users(client: AsyncClient):
    response = await client.get("/api/admin/users")
    assert response.status_code == 200, response.text
    users = response.json()
    assert isinstance(users, list)
    assert len(users) >= 1
    admin_user = users[0]
    assert admin_user["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_routes_reset_password(client: AsyncClient):
    users = (await client.get("/api/admin/users")).json()
    assert users
    user_id = users[0]["id"]

    reset_response = await client.post(
        f"/api/admin/users/{user_id}/reset-password",
        json={"force_reset": True},
    )
    assert reset_response.status_code == 200, reset_response.text
    assert reset_response.json()["must_reset_password"] is True
    assert reset_response.json()["temporary_password"]


@pytest.mark.asyncio
async def test_admin_routes_create_user_not_allowed(client: AsyncClient):
    response = await client.post(
        "/api/admin/users",
        json={
            "username": "newuser",
            "password": "Password123!",
            "role": "member",
        },
    )
    assert response.status_code == 405, response.text


@pytest.mark.asyncio
async def test_admin_routes_audit_logs_not_available(client: AsyncClient):
    response = await client.get("/api/admin/audit-logs")
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_admin_routes_delete_user_not_allowed(client: AsyncClient):
    users = (await client.get("/api/admin/users")).json()
    assert users
    user_id = users[0]["id"]

    response = await client.delete(f"/api/admin/users/{user_id}")
    assert response.status_code == 404, response.text
