from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import DeliveryResult
from flocks.server.routes.channel import SessionSendRequest, channel_session_send
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_channel_session_send_falls_back_to_latest_channel_binding() -> None:
    latest_binding = SimpleNamespace(
        session_id="ses_new",
        channel_id="wecom",
        account_id="default",
        chat_id="room_1",
    )
    svc = SimpleNamespace(
        list_bindings=AsyncMock(return_value=[latest_binding]),
        latest_active_user_binding=AsyncMock(return_value=latest_binding),
    )
    deliver_result = DeliveryResult(
        channel_id="wecom",
        message_id="msg_new",
        chat_id="room_1",
    )

    with patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(return_value=[deliver_result]),
    ) as deliver:
        result = await channel_session_send(
            SessionSendRequest(
                session_id="ses_old",
                text="hello",
                channel_type="wecom",
            )
        )

    assert result["ok"] is True
    assert result["session_id"] == "ses_new"
    assert result["message_ids"] == ["msg_new"]
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_awaited_once()
    assert deliver.await_args.kwargs["session_id"] == "ses_new"


@pytest.mark.asyncio
async def test_channel_session_send_returns_404_when_channel_binding_is_ambiguous() -> None:
    svc = SimpleNamespace(
        list_bindings=AsyncMock(return_value=[]),
        latest_active_user_binding=AsyncMock(return_value=None),
    )

    with patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(),
    ) as deliver:
        with pytest.raises(HTTPException) as exc_info:
            await channel_session_send(
                SessionSendRequest(
                    session_id="ses_old",
                    text="hello",
                    channel_type="wecom",
                )
            )

    assert exc_info.value.status_code == 404
    assert "im_send_message(resolve_only=true)" in str(exc_info.value.detail)
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_not_awaited()
