from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import DeliveryResult
from flocks.tool.channel.channel_message import (
    _normalize_channel_type,
    channel_message,
)
from flocks.tool.registry import ToolContext, ToolRegistry


def test_channel_message_normalizes_weixin_aliases() -> None:
    assert _normalize_channel_type("weixin") == "weixin"
    assert _normalize_channel_type("微信") == "weixin"
    assert _normalize_channel_type("wechat") == "weixin"
    assert _normalize_channel_type("wx") == "weixin"


def test_channel_message_normalizes_wecom_aliases() -> None:
    assert _normalize_channel_type("wecom") == "wecom"
    assert _normalize_channel_type("企业微信") == "wecom"
    assert _normalize_channel_type("企微") == "wecom"
    assert _normalize_channel_type("wechat_work") == "wecom"
    assert _normalize_channel_type("wxwork") == "wecom"


def test_channel_message_schema_includes_weixin() -> None:
    schema = ToolRegistry.get_schema("channel_message")

    assert schema is not None
    assert "wecom" in schema.properties["channel_type"]["enum"]
    assert "企业微信" in schema.properties["channel_type"]["enum"]
    assert "weixin" in schema.properties["channel_type"]["enum"]
    assert "微信" in schema.properties["channel_type"]["enum"]


@pytest.mark.asyncio
async def test_channel_message_exact_binding_filters_selected_chat_only() -> None:
    bindings = [
        SimpleNamespace(
            session_id="ses_shared",
            channel_id="feishu",
            account_id="acct_1",
            chat_id="chat_1",
        ),
        SimpleNamespace(
            session_id="ses_shared",
            channel_id="feishu",
            account_id="acct_2",
            chat_id="chat_2",
        ),
    ]
    svc = SimpleNamespace(list_bindings=AsyncMock(return_value=bindings))
    deliver_result = DeliveryResult(
        channel_id="feishu",
        message_id="msg_2",
        chat_id="chat_2",
    )

    with patch(
        "flocks.tool.channel.channel_message._http_session_send",
        AsyncMock(return_value=None),
    ), patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(return_value=[deliver_result]),
    ) as deliver:
        result = await channel_message(
            ToolContext(session_id="ses_current", message_id="msg_1"),
            session_id="ses_shared",
            message="hello",
            channel_type="feishu",
            account_id="acct_2",
            chat_id="chat_2",
        )

    assert result.success is True
    deliver.assert_awaited_once()
    out_ctx = deliver.await_args.args[0]
    assert out_ctx.account_id == "acct_2"
    assert out_ctx.to == "chat_2"


@pytest.mark.asyncio
async def test_channel_message_falls_back_to_latest_channel_binding() -> None:
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
        "flocks.tool.channel.channel_message._http_session_send",
        AsyncMock(return_value=None),
    ), patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(return_value=[deliver_result]),
    ) as deliver:
        result = await channel_message(
            ToolContext(session_id="ses_task", message_id="msg_1"),
            session_id="ses_old",
            message="hello",
            channel_type="wecom",
        )

    assert result.success is True
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_awaited_once()
    assert deliver.await_args.kwargs["session_id"] == "ses_new"
    out_ctx = deliver.await_args.args[0]
    assert out_ctx.account_id == "default"
    assert out_ctx.to == "room_1"


@pytest.mark.asyncio
async def test_channel_message_does_not_fallback_when_channel_binding_is_ambiguous() -> None:
    svc = SimpleNamespace(
        list_bindings=AsyncMock(return_value=[]),
        latest_active_user_binding=AsyncMock(return_value=None),
    )

    with patch(
        "flocks.tool.channel.channel_message._http_session_send",
        AsyncMock(return_value=None),
    ), patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(),
    ) as deliver:
        result = await channel_message(
            ToolContext(session_id="ses_task", message_id="msg_1"),
            session_id="ses_old",
            message="hello",
            channel_type="wecom",
        )

    assert result.success is False
    assert "im_send_message(resolve_only=true)" in (result.error or "")
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_not_awaited()
