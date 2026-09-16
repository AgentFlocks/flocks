"""Routes for user-facing WebUI notifications.

This is a WebUI-only surface; no TUI-compatible non-/api route is needed.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, Request

from flocks.notifications.service import (
    NotificationAck,
    NotificationAckStatus,
    NotificationResponse,
    NotificationService,
)
from flocks.server.auth import require_user
from flocks.notifications.token_policy import PolicyClaim, PolicyConfirmation, PolicyStatus, TokenPolicyService

router = APIRouter()


@router.get("/token-policy", response_model=PolicyStatus)
async def token_policy_status(request: Request) -> PolicyStatus:
    return await TokenPolicyService.status_for_user(require_user(request).id)


@router.post("/token-policy/claim", response_model=PolicyStatus)
async def claim_token_policy(request: Request, claim: PolicyClaim) -> PolicyStatus:
    return await TokenPolicyService.claim(require_user(request).id, claim)


@router.post("/token-policy/displayed", response_model=PolicyConfirmation)
async def confirm_token_policy_display(request: Request, claim: PolicyClaim) -> PolicyConfirmation:
    return await TokenPolicyService.confirm_display(require_user(request).id, claim)


@router.get(
    "/active",
    response_model=list[NotificationResponse],
    summary="List active notifications",
)
async def list_active_notifications(
    request: Request,
    locale: str | None = None,
) -> list[NotificationResponse]:
    user = require_user(request)
    return await NotificationService.list_active(
        user_id=user.id,
        locale=locale,
    )


@router.get(
    "/{notification_id}/ack",
    response_model=NotificationAckStatus,
    summary="Get notification acknowledgement status",
)
async def get_notification_acknowledgement(
    request: Request,
    notification_id: str = Path(..., pattern=r"^[a-zA-Z0-9._:-]{1,128}$"),
) -> NotificationAckStatus:
    user = require_user(request)
    return await NotificationService.acknowledgement_status(
        user_id=user.id,
        notification_id=notification_id,
    )


@router.post(
    "/{notification_id}/ack",
    response_model=NotificationAck,
    summary="Acknowledge notification",
)
async def acknowledge_notification(
    request: Request,
    notification_id: str = Path(..., pattern=r"^[a-zA-Z0-9._:-]{1,128}$"),
) -> NotificationAck:
    user = require_user(request)
    return await NotificationService.acknowledge(
        user_id=user.id,
        notification_id=notification_id,
    )
