from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks.notifications.service import NotificationService


@pytest.mark.asyncio
async def test_notifications_require_browser_login(client: AsyncClient):
    response = await client.get(
        "/api/notifications/active",
        headers={"sec-fetch-mode": "cors"},
    )
    assert response.status_code == 401


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
