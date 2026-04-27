"""Routes for user-facing WebUI notifications."""

from __future__ import annotations

from fastapi import APIRouter, Request

from flocks.notifications.service import NotificationAck, NotificationResponse, NotificationService
from flocks.server.auth import require_user

router = APIRouter()


@router.get("/active", response_model=list[NotificationResponse], summary="List active notifications")
async def list_active_notifications(
    request: Request,
    locale: str | None = None,
    current_version: str | None = None,
) -> list[NotificationResponse]:
    user = require_user(request)
    return await NotificationService.list_active(
        user_id=user.id,
        locale=locale,
        current_version=current_version,
    )


@router.post("/{notification_id}/ack", response_model=NotificationAck, summary="Acknowledge notification")
async def acknowledge_notification(notification_id: str, request: Request) -> NotificationAck:
    user = require_user(request)
    return await NotificationService.acknowledge(
        user_id=user.id,
        notification_id=notification_id,
    )
