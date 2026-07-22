from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.channel.im_send_message import (
    _Candidate,
    _normalize_channel_type,
    im_send_message,
)
from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult


def _ctx(session_id: str = "ses_current") -> ToolContext:
    return ToolContext(session_id=session_id, message_id="msg_1")


def _candidate(session_id: str = "ses_target", channel_id: str = "feishu") -> _Candidate:
    return _Candidate(
        session_id=session_id,
        channel_id=channel_id,
        account_id="default",
        chat_type="group",
        chat_id="chat_1",
        title="Feishu project chat",
        last_message_at=100.0,
    )


def test_im_send_message_is_registered() -> None:
    tool = ToolRegistry.get("im_send_message")

    assert tool is not None
    schema = tool.info.get_schema()
    assert "message" in schema.properties
    assert "resolve_only" in schema.properties
    assert "session_id" not in schema.required


def test_im_send_message_normalizes_weixin_aliases() -> None:
    assert _normalize_channel_type("weixin") == "weixin"
    assert _normalize_channel_type("微信") == "weixin"
    assert _normalize_channel_type("wechat") == "weixin"
    assert _normalize_channel_type("wx") == "weixin"


def test_im_send_message_normalizes_wecom_aliases() -> None:
    assert _normalize_channel_type("wecom") == "wecom"
    assert _normalize_channel_type("企业微信") == "wecom"
    assert _normalize_channel_type("企微") == "wecom"
    assert _normalize_channel_type("wechat_work") == "wecom"
    assert _normalize_channel_type("wxwork") == "wecom"


def test_im_send_message_normalizes_telegram_whatsapp_email_aliases() -> None:
    assert _normalize_channel_type("telegram") == "telegram"
    assert _normalize_channel_type("tg") == "telegram"
    assert _normalize_channel_type("tele") == "telegram"
    assert _normalize_channel_type("whatsapp") == "whatsapp"
    assert _normalize_channel_type("wa") == "whatsapp"
    assert _normalize_channel_type("email") == "email"
    assert _normalize_channel_type("mail") == "email"
    assert _normalize_channel_type("邮件") == "email"


def test_im_send_message_schema_mentions_extended_builtin_channels() -> None:
    schema = ToolRegistry.get_schema("im_send_message")

    assert schema is not None
    channel_description = schema.properties["channel_type"]["description"]
    tool_description = ToolRegistry.get("im_send_message").info.description
    for channel in ("telegram", "whatsapp", "email"):
        assert channel in channel_description.lower()
        assert channel in tool_description.lower()


@pytest.mark.asyncio
async def test_im_send_message_requires_message_unless_resolve_only() -> None:
    result = await im_send_message(_ctx(), session_id="ses_target")

    assert result.success is False
    assert "message is required" in (result.error or "")


@pytest.mark.asyncio
async def test_im_send_message_resolve_only_returns_target() -> None:
    candidate = _candidate()

    with patch(
        "flocks.tool.channel.im_send_message._list_candidates",
        AsyncMock(return_value=[candidate]),
    ):
        result = await im_send_message(
            _ctx(),
            session_id="ses_target",
            resolve_only=True,
        )

    assert result.success is True
    assert "session_id=ses_target" in str(result.output)
    assert result.metadata["target"]["channel_id"] == "feishu"


@pytest.mark.asyncio
async def test_im_send_message_reuses_channel_message_after_resolution() -> None:
    candidate = _candidate()
    send_result = ToolResult(success=True, output="sent")

    with patch(
        "flocks.tool.channel.im_send_message._list_candidates",
        AsyncMock(return_value=[candidate]),
    ), patch(
        "flocks.tool.channel.channel_message.channel_message",
        AsyncMock(return_value=send_result),
    ) as channel_message:
        result = await im_send_message(
            _ctx(),
            message="hello",
            session_id="ses_target",
        )

    assert result is send_result
    channel_message.assert_awaited_once()
    _, kwargs = channel_message.await_args
    assert kwargs["session_id"] == "ses_target"
    assert kwargs["channel_type"] == "feishu"
    assert kwargs["account_id"] == "default"
    assert kwargs["chat_id"] == "chat_1"
    assert kwargs["message"] == "hello"


@pytest.mark.asyncio
async def test_im_send_message_uses_current_im_session_by_default() -> None:
    candidate = _candidate(session_id="ses_current", channel_id="wecom")
    send_result = ToolResult(success=True, output="sent")

    with patch(
        "flocks.tool.channel.im_send_message._list_candidates",
        AsyncMock(return_value=[candidate]),
    ), patch(
        "flocks.tool.channel.channel_message.channel_message",
        AsyncMock(return_value=send_result),
    ) as channel_message:
        result = await im_send_message(_ctx(), message="hello")

    assert result is send_result
    _, kwargs = channel_message.await_args
    assert kwargs["session_id"] == "ses_current"
    assert kwargs["channel_type"] == "wecom"
    assert kwargs["account_id"] == "default"
    assert kwargs["chat_id"] == "chat_1"


@pytest.mark.asyncio
async def test_im_send_message_asks_when_multiple_targets_match() -> None:
    first = _candidate(session_id="ses_first", channel_id="feishu")
    second = _candidate(session_id="ses_second", channel_id="wecom")
    question_result = ToolResult(
        success=True,
        output="answered",
        metadata={"answers": [[second.label]]},
    )
    send_result = ToolResult(success=True, output="sent")

    with patch(
        "flocks.tool.channel.im_send_message._list_candidates",
        AsyncMock(return_value=[first, second]),
    ), patch(
        "flocks.tool.channel.im_send_message._ask_user_to_choose",
        AsyncMock(return_value=question_result),
    ) as ask_user, patch(
        "flocks.tool.channel.channel_message.channel_message",
        AsyncMock(return_value=send_result),
    ) as channel_message:
        result = await im_send_message(_ctx(session_id="web_session"), message="hello")

    assert result is send_result
    ask_user.assert_awaited_once()
    _, kwargs = channel_message.await_args
    assert kwargs["session_id"] == "ses_second"
    assert kwargs["channel_type"] == "wecom"
    assert kwargs["account_id"] == "default"
    assert kwargs["chat_id"] == "chat_1"


@pytest.mark.asyncio
async def test_im_send_message_stops_when_channel_question_is_deferred() -> None:
    first = _candidate(session_id="ses_first", channel_id="feishu")
    second = _candidate(session_id="ses_second", channel_id="wecom")
    question_result = ToolResult(
        success=True,
        output="Question sent to the IM channel as plain text.",
        metadata={"deferred": True, "channel_session": True},
    )

    with patch(
        "flocks.tool.channel.im_send_message._list_candidates",
        AsyncMock(return_value=[first, second]),
    ), patch(
        "flocks.tool.channel.im_send_message._ask_user_to_choose",
        AsyncMock(return_value=question_result),
    ), patch(
        "flocks.tool.channel.channel_message.channel_message",
        AsyncMock(),
    ) as channel_message:
        result = await im_send_message(_ctx(session_id="ses_channel"), message="hello")

    assert result is question_result
    channel_message.assert_not_awaited()
