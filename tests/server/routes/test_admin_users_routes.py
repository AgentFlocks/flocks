from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.auth.service import AuthService
from flocks.session.session import Session


@pytest.mark.asyncio
async def test_admin_routes_enforce_account_limits(client: AsyncClient):
    for index in range(3):
        response = await client.post(
            "/api/admin/users",
            json={
                "username": f"admin{index}",
                "password": "Password123!",
                "role": "admin",
            },
        )
        assert response.status_code == 200, response.text

    admin_limit_response = await client.post(
        "/api/admin/users",
        json={
            "username": "admin-over-limit",
            "password": "Password123!",
            "role": "admin",
        },
    )
    assert admin_limit_response.status_code == 400
    assert "管理员账号最多 3 个" in admin_limit_response.text

    for index in range(20):
        response = await client.post(
            "/api/admin/users",
            json={
                "username": f"member{index}",
                "password": "Password123!",
                "role": "member",
            },
        )
        assert response.status_code == 200, response.text

    member_limit_response = await client.post(
        "/api/admin/users",
        json={
            "username": "member-over-limit",
            "password": "Password123!",
            "role": "member",
        },
    )
    assert member_limit_response.status_code == 400
    assert "普通用户账号最多 20 个" in member_limit_response.text


@pytest.mark.asyncio
async def test_admin_routes_protect_last_active_admin(client: AsyncClient):
    create_response = await client.post(
        "/api/admin/users",
        json={
            "username": "root-admin",
            "password": "Password123!",
            "role": "admin",
        },
    )
    assert create_response.status_code == 200, create_response.text
    user_id = create_response.json()["id"]

    disable_response = await client.patch(
        f"/api/admin/users/{user_id}/status",
        json={"status": "disabled"},
    )
    assert disable_response.status_code == 400
    assert "最后一个管理员" in disable_response.text

    role_response = await client.patch(
        f"/api/admin/users/{user_id}/role",
        json={"role": "member"},
    )
    assert role_response.status_code == 400
    assert "最后一个管理员" in role_response.text

    delete_response = await client.delete(f"/api/admin/users/{user_id}")
    assert delete_response.status_code == 400
    assert "最后一个管理员" in delete_response.text


@pytest.mark.asyncio
async def test_admin_routes_update_role_and_record_audit(client: AsyncClient):
    create_response = await client.post(
        "/api/admin/users",
        json={
            "username": "bob",
            "password": "Password123!",
            "role": "member",
        },
    )
    assert create_response.status_code == 200, create_response.text
    user_id = create_response.json()["id"]

    role_response = await client.patch(
        f"/api/admin/users/{user_id}/role",
        json={"role": "admin"},
    )
    assert role_response.status_code == 200, role_response.text
    assert role_response.json()["role"] == "admin"

    reset_response = await client.post(
        f"/api/admin/users/{user_id}/reset-password",
        json={"force_reset": True},
    )
    assert reset_response.status_code == 200, reset_response.text
    assert reset_response.json()["must_reset_password"] is True
    assert reset_response.json()["temporary_password"]

    audits = await AuthService.list_audits(limit=20)
    actions = [record.action for record in audits]
    assert "admin.update_user_role" in actions
    assert "admin.reset_password" in actions


@pytest.mark.asyncio
async def test_delete_user_revokes_login_sessions_and_retains_history(client: AsyncClient):
    create_response = await client.post(
        "/api/admin/users",
        json={
            "username": "alice",
            "password": "Password123!",
            "role": "member",
        },
    )
    assert create_response.status_code == 200, create_response.text
    deleted_user = create_response.json()

    _, login_session_id = await AuthService.login("alice", "Password123!")
    session = await Session.create(
        project_id="history-project",
        directory="/history",
        title="Alice History",
        owner_user_id=deleted_user["id"],
        owner_username="alice",
    )

    delete_response = await client.delete(f"/api/admin/users/{deleted_user['id']}")
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["success"] is True
    assert delete_response.json()["retained_sessions"] == 1

    assert await AuthService.get_user_by_session_id(login_session_id) is None

    stored = await Session.get("history-project", session.id)
    assert stored is not None
    assert stored.owner_user_id is None
    assert stored.owner_username == "alice"

    recreate_response = await client.post(
        "/api/admin/users",
        json={
            "username": "alice",
            "password": "Password123!",
            "role": "member",
        },
    )
    assert recreate_response.status_code == 200, recreate_response.text
    recreated_user = recreate_response.json()

    token = set_current_auth_user(
        AuthUser(
            id=recreated_user["id"],
            username="alice",
            role="member",
            status="active",
        )
    )
    try:
        visible_sessions = await Session.list_all()
        assert any(item.id == session.id for item in visible_sessions)
    finally:
        reset_current_auth_user(token)

    audits = await AuthService.list_audits(limit=20)
    delete_audit = next(record for record in audits if record.action == "admin.delete_user")
    assert delete_audit.metadata["retained_sessions"] == 1
