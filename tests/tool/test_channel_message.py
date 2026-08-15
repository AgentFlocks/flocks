from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import DeliveryResult
from flocks.tool.channel.channel_message import (
    _Candidate,
    _normalize_channel_type,
    channel_message,
)
from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult


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


def test_channel_message_normalizes_slack_aliases() -> None:
    assert _normalize_channel_type("slack") == "slack"
    assert _normalize_channel_type("sl") == "slack"


def test_channel_message_normalizes_telegram_whatsapp_email_aliases() -> None:
    assert _normalize_channel_type("telegram") == "telegram"
    assert _normalize_channel_type("tg") == "telegram"
    assert _normalize_channel_type("tele") == "telegram"
    assert _normalize_channel_type("whatsapp") == "whatsapp"
    assert _normalize_channel_type("wa") == "whatsapp"
    assert _normalize_channel_type("email") == "email"
    assert _normalize_channel_type("mail") == "email"
    assert _normalize_channel_type("邮件") == "email"


def _candidate(
    session_id: str = "ses_target",
    channel_id: str = "feishu",
) -> _Candidate:
    return _Candidate(
        session_id=session_id,
        channel_id=channel_id,
        account_id="default",
        chat_type="group",
        chat_id="chat_1",
        title="Feishu project chat",
        last_message_at=100.0,
    )


def test_channel_message_is_the_only_registered_message_tool() -> None:
    assert ToolRegistry.get("channel_message") is not None
    assert ToolRegistry.get("im_send_message") is None


def test_channel_message_schema_supports_resolution_and_custom_channels() -> None:
    schema = ToolRegistry.get_schema("channel_message")

    assert schema is not None
    assert "target" in schema.properties
    assert "message" not in schema.required
    assert "session_id" not in schema.required
    assert "enum" not in schema.properties["channel_type"]
    channel_description = schema.properties["channel_type"]["description"].lower()
    tool = ToolRegistry.get("channel_message")
    assert tool is not None
    tool_description = tool.info.description.lower()
    for channel in ("telegram", "whatsapp", "email", "slack", "custom"):
        assert channel in channel_description or channel in tool_description


@pytest.mark.asyncio
async def test_channel_message_rejects_empty_message() -> None:
    result = await channel_message(
        ToolContext(session_id="ses_current", message_id="msg_1"),
        session_id="ses_target",
        message="  ",
    )

    assert result.success is False
    assert result.error == "message must not be empty."


@pytest.mark.asyncio
async def test_channel_message_rejects_conflicting_target_inputs() -> None:
    result = await channel_message(
        ToolContext(session_id="ses_current", message_id="msg_1"),
        session_id="ses_target",
        target="project chat",
        message="hello",
    )

    assert result.success is False
    assert "cannot be used together" in (result.error or "")


@pytest.mark.asyncio
async def test_channel_message_without_message_resolves_without_sending() -> None:
    candidate = _candidate()

    with (
        patch(
            "flocks.tool.channel.channel_message._list_candidates",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "flocks.tool.channel.channel_message._send_message_to_session",
            AsyncMock(),
        ) as send,
    ):
        result = await channel_message(
            ToolContext(session_id="ses_current", message_id="msg_1"),
            session_id="ses_target",
        )

    assert result.success is True
    assert "session_id=ses_target" in str(result.output)
    assert result.metadata["mode"] == "resolve"
    assert result.metadata["target"]["channel_id"] == "feishu"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_message_resolves_target_then_sends_exact_binding() -> None:
    candidate = _candidate()
    send_result = ToolResult(success=True, output="sent")

    with (
        patch(
            "flocks.tool.channel.channel_message._list_candidates",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "flocks.tool.channel.channel_message._send_message_to_session",
            AsyncMock(return_value=send_result),
        ) as send,
    ):
        result = await channel_message(
            ToolContext(session_id="web_session", message_id="msg_1"),
            target="project chat",
            message="hello",
        )

    assert result is send_result
    send.assert_awaited_once()
    assert send.await_args.kwargs["session_id"] == "ses_target"
    assert send.await_args.kwargs["channel_type"] == "feishu"
    assert send.await_args.kwargs["account_id"] == "default"
    assert send.await_args.kwargs["chat_id"] == "chat_1"


@pytest.mark.asyncio
async def test_channel_message_uses_current_messaging_session_by_default() -> None:
    candidate = _candidate(session_id="ses_current", channel_id="wecom")
    send_result = ToolResult(success=True, output="sent")

    with (
        patch(
            "flocks.tool.channel.channel_message._list_candidates",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "flocks.tool.channel.channel_message._send_message_to_session",
            AsyncMock(return_value=send_result),
        ) as send,
    ):
        result = await channel_message(
            ToolContext(session_id="ses_current", message_id="msg_1"),
            message="hello",
        )

    assert result is send_result
    assert send.await_args.kwargs["session_id"] == "ses_current"
    assert send.await_args.kwargs["channel_type"] == "wecom"


@pytest.mark.asyncio
async def test_channel_message_asks_when_multiple_targets_match() -> None:
    first = _candidate(session_id="ses_first", channel_id="feishu")
    second = _candidate(session_id="ses_second", channel_id="wecom")
    question_result = ToolResult(
        success=True,
        output="answered",
        metadata={"answers": [[second.label]]},
    )
    send_result = ToolResult(success=True, output="sent")

    with (
        patch(
            "flocks.tool.channel.channel_message._list_candidates",
            AsyncMock(return_value=[first, second]),
        ),
        patch(
            "flocks.tool.channel.channel_message._is_interactive_context",
            AsyncMock(return_value=True),
        ),
        patch(
            "flocks.tool.channel.channel_message._ask_user_to_choose",
            AsyncMock(return_value=question_result),
        ) as ask_user,
        patch(
            "flocks.tool.channel.channel_message._send_message_to_session",
            AsyncMock(return_value=send_result),
        ) as send,
    ):
        result = await channel_message(
            ToolContext(session_id="web_session", message_id="msg_1"),
            message="hello",
        )

    assert result is send_result
    ask_user.assert_awaited_once()
    assert send.await_args.kwargs["session_id"] == "ses_second"


@pytest.mark.asyncio
async def test_channel_message_does_not_ask_in_workflow_context() -> None:
    first = _candidate(session_id="ses_first", channel_id="feishu")
    second = _candidate(session_id="ses_second", channel_id="wecom")
    ctx = ToolContext(session_id="ses_workflow", message_id="msg_1")
    ctx.extra["workflow_context"] = {"source": "workflow_runtime"}

    with (
        patch(
            "flocks.tool.channel.channel_message._list_candidates",
            AsyncMock(return_value=[first, second]),
        ),
        patch(
            "flocks.tool.channel.channel_message._ask_user_to_choose",
            AsyncMock(),
        ) as ask_user,
    ):
        result = await channel_message(ctx, message="hello")

    assert result.success is False
    assert "must provide an exact session_id" in (result.error or "")
    ask_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_message_uses_runtime_server_port(monkeypatch) -> None:
    monkeypatch.setenv("_FLOCKS_SERVER_PORT", "5173")
    http_send = AsyncMock(return_value=ToolResult(success=True, output="ok"))

    with patch(
        "flocks.tool.channel.channel_message._http_session_send",
        http_send,
    ):
        result = await channel_message(
            ToolContext(session_id="ses_current", message_id="msg_1"),
            session_id="ses_target",
            message="hello",
        )

    assert result.success is True
    assert http_send.await_args.args[0] == 5173


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

    with (
        patch(
            "flocks.tool.channel.channel_message._http_session_send",
            AsyncMock(return_value=None),
        ),
        patch(
            "flocks.channel.inbound.session_binding.SessionBindingService",
            return_value=svc,
        ),
        patch(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            AsyncMock(return_value=[deliver_result]),
        ) as deliver,
    ):
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

    with (
        patch(
            "flocks.tool.channel.channel_message._http_session_send",
            AsyncMock(return_value=None),
        ),
        patch(
            "flocks.channel.inbound.session_binding.SessionBindingService",
            return_value=svc,
        ),
        patch(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            AsyncMock(return_value=[deliver_result]),
        ) as deliver,
    ):
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

    with (
        patch(
            "flocks.tool.channel.channel_message._http_session_send",
            AsyncMock(return_value=None),
        ),
        patch(
            "flocks.channel.inbound.session_binding.SessionBindingService",
            return_value=svc,
        ),
        patch(
            "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
            AsyncMock(),
        ) as deliver,
    ):
        result = await channel_message(
            ToolContext(session_id="ses_task", message_id="msg_1"),
            session_id="ses_old",
            message="hello",
            channel_type="wecom",
        )

    assert result.success is False
    assert "channel_message without message" in (result.error or "")
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_not_awaited()
