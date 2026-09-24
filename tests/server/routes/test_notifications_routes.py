from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks.notifications.service import NotificationService


@pytest.fixture
def builtin_notice(monkeypatch):
    from flocks.notifications import service as notification_service

    monkeypatch.setattr(notification_service, "DEFAULT_NOTIFICATIONS", (
        notification_service.NotificationConfig(
            id="test-benefit",
            kind="benefit",
            locales={"en-US": notification_service.NotificationContent(title="Test benefit")},
        ),
    ))


@pytest.mark.asyncio
async def test_retired_token_notice_is_not_active(client: AsyncClient):
    response = await client.get("/api/notifications/active", params={"locale": "zh-CN"})
    assert response.status_code == 200, response.text
    assert "token-free-period-extended-2026-04" not in {item["id"] for item in response.json()}


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
async def test_active_notifications_and_dismiss_forever(client: AsyncClient, builtin_notice):
    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN"},
    )
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["id"] for item in items] == ["test-benefit"]
    assert items[0]["kind"] == "benefit"

    ack_response = await client.post("/api/notifications/test-benefit/ack")
    assert ack_response.status_code == 200, ack_response.text

    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []

    response = await client.get(
        "/api/notifications/active",
        params={"locale": "zh-CN"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.asyncio
async def test_notification_ack_is_per_user(builtin_notice):
    await NotificationService.acknowledge(
        user_id="user-a",
        notification_id="test-benefit",
    )

    user_a_items = await NotificationService.list_active(
        user_id="user-a",
        locale="en-US",
    )
    user_b_items = await NotificationService.list_active(
        user_id="user-b",
        locale="en-US",
    )

    assert "test-benefit" not in {item.id for item in user_a_items}
    assert "test-benefit" in {item.id for item in user_b_items}


@pytest.mark.asyncio
async def test_config_notification_overrides_builtin(monkeypatch, builtin_notice):
    from flocks.notifications import service as notification_service

    async def fake_load_config_notifications():
        return [
            notification_service.NotificationConfig(
                id="test-benefit",
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
    assert "test-benefit" not in {item.id for item in items}


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
async def test_arbitrary_whats_new_ack_status(client: AsyncClient):
    status_response = await client.get("/api/notifications/whats-new-2026.04.28/ack")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["acknowledged"] is False

    ack_response = await client.post("/api/notifications/whats-new-2026.04.28/ack")
    assert ack_response.status_code == 200, ack_response.text

    status_response = await client.get("/api/notifications/whats-new-2026.04.28/ack")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["acknowledged"] is True


@pytest.mark.asyncio
async def test_notification_ack_rejects_invalid_id(client: AsyncClient):
    response = await client.post("/api/notifications/bad id/ack")
    assert response.status_code == 422
