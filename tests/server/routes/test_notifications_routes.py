from __future__ import annotations

from datetime import datetime
import secrets

import pytest
from httpx import AsyncClient

from flocks.notifications import service as notification_service
from flocks.notifications.service import NotificationService


HOLIDAY_NOTICE_ID = "holiday-benefits-2026-09-23"
RELEASE_NOTICE_ID = "whats-new-2026.9.23"


@pytest.fixture
def holiday_clock(monkeypatch):
    current = [datetime.fromisoformat("2026-09-23T12:00:00+08:00")]

    class NoticeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(notification_service, "datetime", NoticeDateTime)
    return current


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
@pytest.mark.parametrize(
    ("locale", "title", "summary", "body_fragments", "alt", "caption"),
    [
        (
            "zh-CN",
            "10 月 Token 政策调整",
            "双节前做好切换，业务运行更安心。",
            (
                "10 月 1 日起",
                "每人每日保留 1000 万免费 Token",
                "超出部分按金额计费",
                "[微步大模型平台](https://portal.agentflocks.com)",
                "公共免费池及原有临时加额同步停止",
                "### 双节过渡保障",
                "**1亿Token**（2026年10月15日到期）",
            ),
            "微步在线小程序码",
            "扫码申请过渡保障",
        ),
        (
            "en-US",
            "October Token policy changes",
            "Prepare for the holiday transition and keep your services running.",
            (
                "Starting October 1",
                "daily free allowance of 10 million Tokens",
                "billed by monetary value",
                "[ThreatBook LLM Platform](https://portal.agentflocks.com)",
                "public free pool and existing temporary extra allowances",
                "### Holiday transition support",
                "**100 million Tokens** (expires on October 15, 2026)",
            ),
            "ThreatBook Online mini program code",
            "Scan to apply for transition support",
        ),
    ],
)
async def test_holiday_notification_localized_api_content(
    client: AsyncClient, holiday_clock, locale, title, summary, body_fragments, alt, caption
):
    response = await client.get("/api/notifications/active", params={"locale": locale})
    assert response.status_code == 200, response.text
    notices = response.json()
    assert [notice["id"] for notice in notices] == [HOLIDAY_NOTICE_ID, RELEASE_NOTICE_ID]
    notice = notices[0]
    assert notice["kind"] == "benefit"
    assert notice["priority"] == 10
    assert notice["title"] == title
    assert notice["summary"] == summary
    for fragment in body_fragments:
        assert fragment in notice["body"]
    assert notice["qr_code"] == {
        "src": "/notifications/holiday-transition-20260923.jpg",
        "alt": alt,
        "caption": caption,
    }
    assert notice["version"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locale", "title", "body_fragments"),
    [
        (
            "zh-CN",
            "Flocks v2026.9.23 更新内容",
            ("工作台与场景", "会话与工具", "插件与部署", "releases/tag/v2026.9.23"),
        ),
        (
            "en-US",
            "What's new in Flocks v2026.9.23",
            ("Workspaces and scenes", "Sessions and tools", "Plugins and deployment", "releases/tag/v2026.9.23"),
        ),
    ],
)
async def test_release_notification_is_served_without_update_check(
    client: AsyncClient, holiday_clock, locale, title, body_fragments
):
    response = await client.get("/api/notifications/active", params={"locale": locale})
    assert response.status_code == 200, response.text
    notice = response.json()[1]
    assert notice["id"] == RELEASE_NOTICE_ID
    assert notice["kind"] == "whats_new"
    assert notice["title"] == title
    assert notice["version"] == "2026.9.23"
    assert notice["priority"] == 20
    assert notice["qr_code"] is None
    for fragment in body_fragments:
        assert fragment in notice["body"]


@pytest.mark.asyncio
async def test_holiday_notification_uses_english_for_unsupported_locale(
    client: AsyncClient, holiday_clock
):
    response = await client.get("/api/notifications/active", params={"locale": "fr-FR"})
    assert response.status_code == 200, response.text
    assert response.json()[0]["title"] == "October Token policy changes"
    assert response.json()[1]["title"] == "What's new in Flocks v2026.9.23"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "visible"),
    [
        ("2026-09-22T23:59:59.999999+08:00", False),
        ("2026-09-23T00:00:00+08:00", True),
        ("2026-10-14T23:59:59.999999+08:00", True),
        ("2026-10-15T00:00:00+08:00", False),
    ],
)
async def test_holiday_notification_time_window_boundaries(
    client: AsyncClient, holiday_clock, now, visible
):
    holiday_clock[0] = datetime.fromisoformat(now)
    response = await client.get("/api/notifications/active", params={"locale": "zh-CN"})
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()}
    assert (HOLIDAY_NOTICE_ID in ids) is visible
    assert (RELEASE_NOTICE_ID in ids) is visible


@pytest.mark.asyncio
async def test_holiday_dismissal_is_persisted_per_logged_in_user(
    client: AsyncClient, holiday_clock
):
    from flocks.auth.service import AuthService

    password = secrets.token_urlsafe(18)
    admin = await AuthService.bootstrap_admin(username="notice-admin", password=password)
    member = await AuthService._create_user_internal(username="notice-member", password=password)
    browser_headers = {"sec-fetch-mode": "cors"}

    login = await client.post(
        "/api/auth/login",
        json={"username": admin.username, "password": password},
        headers=browser_headers,
    )
    assert login.status_code == 200, login.text
    ack_response = await client.post(
        f"/api/notifications/{HOLIDAY_NOTICE_ID}/ack", headers=browser_headers
    )
    assert ack_response.status_code == 200, ack_response.text
    assert ack_response.json()["user_id"] == admin.id

    # A new login still reads the acknowledgement from the isolated real database.
    login = await client.post(
        "/api/auth/login",
        json={"username": admin.username, "password": password},
        headers=browser_headers,
    )
    assert login.status_code == 200, login.text
    status = await client.get(
        f"/api/notifications/{HOLIDAY_NOTICE_ID}/ack", headers=browser_headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["acknowledged"] is True
    response = await client.get("/api/notifications/active", headers=browser_headers)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [RELEASE_NOTICE_ID]

    release_ack = await client.post(
        f"/api/notifications/{RELEASE_NOTICE_ID}/ack", headers=browser_headers
    )
    assert release_ack.status_code == 200, release_ack.text
    response = await client.get("/api/notifications/active", headers=browser_headers)
    assert response.status_code == 200, response.text
    assert response.json() == []

    login = await client.post(
        "/api/auth/login",
        json={"username": member.username, "password": password},
        headers=browser_headers,
    )
    assert login.status_code == 200, login.text
    status = await client.get(
        f"/api/notifications/{HOLIDAY_NOTICE_ID}/ack", headers=browser_headers
    )
    assert status.status_code == 200, status.text
    assert status.json()["user_id"] == member.id
    assert status.json()["acknowledged"] is False
    response = await client.get("/api/notifications/active", headers=browser_headers)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [HOLIDAY_NOTICE_ID, RELEASE_NOTICE_ID]


@pytest.mark.asyncio
async def test_retired_notice_ack_does_not_hide_holiday_notice(
    client: AsyncClient, holiday_clock
):
    ack_response = await client.post("/api/notifications/token-free-period-extended-2026-04/ack")
    assert ack_response.status_code == 200, ack_response.text

    status = await client.get(f"/api/notifications/{HOLIDAY_NOTICE_ID}/ack")
    assert status.status_code == 200, status.text
    assert status.json()["acknowledged"] is False
    response = await client.get("/api/notifications/active")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == [HOLIDAY_NOTICE_ID, RELEASE_NOTICE_ID]


@pytest.mark.asyncio
async def test_config_can_disable_holiday_notice(
    client: AsyncClient, holiday_clock, monkeypatch
):
    from flocks.config.config import Config, ConfigInfo

    async def disabled_config():
        return ConfigInfo(notifications=[{
            "id": HOLIDAY_NOTICE_ID,
            "enabled": False,
            "priority": 999,
            "locales": {"zh-CN": {"title": "disabled"}},
        }])

    monkeypatch.setattr(Config, "get", disabled_config)
    response = await client.get("/api/notifications/active")
    assert response.status_code == 200, response.text
    assert HOLIDAY_NOTICE_ID not in {item["id"] for item in response.json()}


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
    assert items[0]["qr_code"] is None

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
