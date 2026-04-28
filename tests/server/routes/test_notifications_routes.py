from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks.notifications.service import NotificationService


@pytest.mark.asyncio
async def test_notifications_require_browser_login(client: AsyncClient):
    from flocks.auth.service import AuthService

    if not await AuthService.has_users():
        await AuthService.bootstrap_admin(username="admin", password="Password123!")

    response = await client.get(
        "/api/notifications/active",
        headers={"sec-fetch-mode": "cors"},
    )
    assert response.status_code == 401
    assert "请先登录" in response.text


@pytest.mark.asyncio
async def test_active_notifications_and_dismiss_forever(client: AsyncClient):
    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN", "current_version": "2026.04.27"},
    )
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["id"] for item in items] == [
        "token-free-period-extended-2026-04",
        "whats-new-2026.04.27",
    ]
    assert items[0]["kind"] == "benefit"
    assert items[1]["kind"] == "whats_new"

    ack_response = await client.post("/api/notifications/token-free-period-extended-2026-04/ack")
    assert ack_response.status_code == 200, ack_response.text

    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN", "current_version": "2026.04.27"},
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == ["whats-new-2026.04.27"]

    await client.post("/api/notifications/whats-new-2026.04.27/ack")
    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN", "current_version": "2026.04.27"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []

    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN", "current_version": "2026.05.01"},
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == ["whats-new-2026.05.01"]


@pytest.mark.asyncio
async def test_notification_ack_is_per_user():
    await NotificationService.acknowledge(
        user_id="user-a",
        notification_id="token-free-period-extended-2026-04",
    )

    user_a_items = await NotificationService.list_active(
        user_id="user-a",
        locale="en-US",
    )
    user_b_items = await NotificationService.list_active(
        user_id="user-b",
        locale="en-US",
    )

    assert "token-free-period-extended-2026-04" not in {item.id for item in user_a_items}
    assert "token-free-period-extended-2026-04" in {item.id for item in user_b_items}


@pytest.mark.asyncio
async def test_config_notification_overrides_builtin(monkeypatch):
    from flocks.notifications import service as notification_service

    async def fake_load_config_notifications():
        return [
            notification_service.NotificationConfig(
                id="token-free-period-extended-2026-04",
                enabled=False,
                priority=999,
                locales={
                    "zh-CN": notification_service.NotificationContent(
                        title="disabled",
                    ),
                },
            )
        ]

    monkeypatch.setattr(
        NotificationService,
        "_load_config_notifications",
        fake_load_config_notifications,
    )

    items = await NotificationService.list_active(
        user_id="user-a",
        locale="zh-CN",
    )
    assert "token-free-period-extended-2026-04" not in {item.id for item in items}


@pytest.mark.asyncio
async def test_notification_time_window_filters_expired(monkeypatch):
    from flocks.notifications import service as notification_service

    async def fake_load_config_notifications():
        return [
            notification_service.NotificationConfig(
                id="expired-notice",
                kind="announcement",
                startsAt="2026-01-01T00:00:00+00:00",
                expiresAt="2026-01-02T00:00:00+00:00",
                locales={
                    "zh-CN": notification_service.NotificationContent(
                        title="expired",
                    ),
                },
            )
        ]

    monkeypatch.setattr(
        NotificationService,
        "_load_config_notifications",
        fake_load_config_notifications,
    )

    items = await NotificationService.list_active(
        user_id="user-a",
        locale="zh-CN",
    )
    assert "expired-notice" not in {item.id for item in items}


@pytest.mark.asyncio
async def test_notifications_fallback_to_backend_current_version(monkeypatch):
    monkeypatch.setattr(
        NotificationService,
        "_resolve_current_version",
        lambda current_version: current_version or "2026.06.01",
    )

    items = await NotificationService.list_active(
        user_id="user-a",
        locale="zh-CN",
        current_version=None,
    )
    assert "whats-new-2026.06.01" in {item.id for item in items}


@pytest.mark.asyncio
async def test_notification_ack_rejects_invalid_id(client: AsyncClient):
    response = await client.post("/api/notifications/bad id/ack")
    assert response.status_code == 422
