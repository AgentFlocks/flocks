"""User-facing notification configuration and acknowledgement service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="notifications")

NotificationKind = Literal["benefit", "whats_new", "announcement"]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_locale(locale: str | None) -> str:
    if (locale or "").lower().startswith("zh"):
        return "zh-CN"
    return "en-US"


class NotificationAction(BaseModel):
    label: str
    url: str | None = None


class NotificationContent(BaseModel):
    title: str
    summary: str | None = None
    body: str | None = None
    highlights: list[str] = Field(default_factory=list)
    primary_action: NotificationAction | None = Field(
        default=None, alias="primaryAction"
    )
    secondary_action: NotificationAction | None = Field(
        default=None, alias="secondaryAction"
    )

    model_config = {"populate_by_name": True}


class NotificationConfig(BaseModel):
    id: str = Field(..., min_length=1)
    kind: NotificationKind = "announcement"
    enabled: bool = True
    priority: int = 100
    version: str | None = None
    starts_at: str | None = Field(default=None, alias="startsAt")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    locales: dict[str, NotificationContent]

    model_config = {"populate_by_name": True}


class NotificationResponse(BaseModel):
    id: str
    kind: NotificationKind
    title: str
    summary: str | None = None
    body: str | None = None
    highlights: list[str] = Field(default_factory=list)
    primary_action: NotificationAction | None = None
    secondary_action: NotificationAction | None = None
    version: str | None = None
    priority: int = 100


class NotificationAck(BaseModel):
    notification_id: str
    user_id: str
    acknowledged_at: str


class NotificationAckStatus(BaseModel):
    notification_id: str
    user_id: str
    acknowledged: bool


DEFAULT_NOTIFICATIONS: tuple[NotificationConfig, ...] = (
    NotificationConfig(
        id="token-free-period-extended-2026-04",
        kind="benefit",
        priority=10,
        starts_at="2026-03-30T00:00:00+08:00",
        locales={
            "zh-CN": NotificationContent(
                title="Token 免费期已延长",
                summary="福利已自动生效，无需额外操作。",
                body=(
                    "为了让你有更充足的时间体验 Flocks，我们已延长 token 免费使用期。"
                ),
                highlights=[
                    "3月30日-4月29日注册的老用户，授权自动延期至60天",
                    "4月29日之后注册的新用户，依旧默认30天注册授权",
                ],
                primaryAction=NotificationAction(label="知道了"),
            ),
            "en-US": NotificationContent(
                title="Token free period extended",
                summary="The benefit is active automatically. No action is required.",
                body=(
                    "We have extended the free token period so you have more time "
                    "to experience Flocks."
                ),
                highlights=[
                    "Existing users who registered between March 30 and April 29 will have their authorization automatically extended to 60 days",
                    "New users who register after April 29 will still receive the default 30-day trial authorization",
                ],
                primaryAction=NotificationAction(label="Got it"),
            ),
        },
    ),
)


class NotificationService:
    """Load active notifications and track per-user acknowledgements."""

    @classmethod
    def _ack_key(cls, user_id: str, notification_id: str) -> str:
        return f"notifications/dismissed/{user_id}/{notification_id}"

    @classmethod
    def _parse_window_time(cls, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            log.warn("notifications.time_window.invalid", {"value": value})
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _is_in_time_window(cls, notification: NotificationConfig, now: datetime) -> bool:
        starts_at = cls._parse_window_time(notification.starts_at)
        expires_at = cls._parse_window_time(notification.expires_at)
        if starts_at and now < starts_at:
            return False
        if expires_at and now >= expires_at:
            return False
        return True

    @classmethod
    def _notification_sort_key(
        cls,
        notification: NotificationConfig,
        config_ids: set[str],
    ) -> tuple[int, int]:
        # Config entries must win over built-ins with the same id, regardless of priority.
        return (0 if notification.id in config_ids else 1, notification.priority)

    @classmethod
    async def _load_config_notifications(cls) -> list[NotificationConfig]:
        try:
            config = await Config.get()
            raw_notifications = getattr(config, "notifications", None)
        except Exception as exc:
            log.warn("notifications.config.load_failed", {"error": str(exc)})
            return []

        if not raw_notifications:
            return []
        if not isinstance(raw_notifications, list):
            log.warn(
                "notifications.config.invalid",
                {"reason": "notifications must be a list"},
            )
            return []

        notifications: list[NotificationConfig] = []
        for item in raw_notifications:
            try:
                notifications.append(NotificationConfig.model_validate(item))
            except ValidationError as exc:
                log.warn("notifications.config.item_invalid", {"error": str(exc)})
        return notifications

    @classmethod
    async def _is_acknowledged(cls, user_id: str, notification_id: str) -> bool:
        return await Storage.get(cls._ack_key(user_id, notification_id)) is not None

    @classmethod
    async def acknowledgement_status(
        cls, *, user_id: str, notification_id: str
    ) -> NotificationAckStatus:
        return NotificationAckStatus(
            user_id=user_id,
            notification_id=notification_id,
            acknowledged=await cls._is_acknowledged(user_id, notification_id),
        )

    @classmethod
    async def list_active(
        cls,
        *,
        user_id: str,
        locale: str | None = None,
        current_version: str | None = None,
    ) -> list[NotificationResponse]:
        target_locale = _normalize_locale(locale)
        config_notifications = await cls._load_config_notifications()
        config_ids = {notification.id for notification in config_notifications}

        notifications = [
            *config_notifications,
            *(notification for notification in DEFAULT_NOTIFICATIONS if notification.id not in config_ids),
        ]

        active: list[NotificationResponse] = []
        seen_ids: set[str] = set()
        now = datetime.now(UTC)
        for notification in sorted(
            notifications,
            key=lambda item: cls._notification_sort_key(item, config_ids),
        ):
            if not notification.enabled or notification.id in seen_ids:
                continue
            seen_ids.add(notification.id)
            if not cls._is_in_time_window(notification, now):
                continue
            if await cls._is_acknowledged(user_id, notification.id):
                continue

            content = (
                notification.locales.get(target_locale)
                or notification.locales.get("en-US")
                or next(iter(notification.locales.values()), None)
            )
            if content is None:
                continue

            active.append(
                NotificationResponse(
                    id=notification.id,
                    kind=notification.kind,
                    title=content.title,
                    summary=content.summary,
                    body=content.body,
                    highlights=content.highlights,
                    primary_action=content.primary_action,
                    secondary_action=content.secondary_action,
                    version=notification.version,
                    priority=notification.priority,
                )
            )

        return active

    @classmethod
    async def acknowledge(
        cls, *, user_id: str, notification_id: str
    ) -> NotificationAck:
        ack = NotificationAck(
            user_id=user_id,
            notification_id=notification_id,
            acknowledged_at=_iso_now(),
        )
        await Storage.set(cls._ack_key(user_id, notification_id), ack)
        if notification_id.startswith("whats-new-"):
            await cls._prune_whats_new_acknowledgements(user_id=user_id, keep=5)
        return ack

    @classmethod
    async def _prune_whats_new_acknowledgements(cls, *, user_id: str, keep: int) -> None:
        prefix = f"notifications/dismissed/{user_id}/whats-new-"
        entries = await Storage.list_entries(prefix)
        if len(entries) <= keep:
            return

        def acknowledged_at(entry: tuple[str, object]) -> str:
            value = entry[1]
            if isinstance(value, dict):
                raw = value.get("acknowledged_at")
                return raw if isinstance(raw, str) else ""
            return ""

        for key, _ in sorted(entries, key=acknowledged_at, reverse=True)[keep:]:
            await Storage.delete(key)
