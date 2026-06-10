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


def test_channel_message_schema_includes_weixin() -> None:
    schema = ToolRegistry.get_schema("channel_message")

    assert schema is not None
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
