from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.inbound.session_binding import (
    SessionBinding,
    SessionBindingService,
    _build_title,
    _build_title_fallback,
    extract_channel_title_text,
    is_channel_media_placeholder,
)


def test_build_title_uses_first_user_input() -> None:
    msg = InboundMessage(
        channel_id="wecom",
        account_id="default",
        message_id="msg-1",
        sender_id="user-1",
        chat_id="room-1",
        chat_type=ChatType.DIRECT,
        text="你是谁",
    )

    assert _build_title(msg) == "[Wecom] 你是谁"


def test_build_title_prefers_mention_text_and_truncates_first_line() -> None:
    msg = InboundMessage(
        channel_id="feishu",
        account_id="default",
        message_id="msg-1",
        sender_id="user-1",
        chat_id="room-1",
        chat_type=ChatType.GROUP,
        text="@Rex ignored raw text",
        mention_text=f"{'a' * 55}\nsecond line",
    )

    assert _build_title(msg) == f"[Feishu] {'a' * 47}..."


def test_build_title_falls_back_when_user_input_is_empty() -> None:
    msg = InboundMessage(
        channel_id="wecom",
        account_id="default",
        message_id="msg-1",
        sender_id="user-1",
        sender_name="Alice",
        chat_id="room-1",
        chat_type=ChatType.DIRECT,
        text="  \n ",
    )

    assert _build_title(msg) == "[Wecom] DM — Alice"


@pytest.mark.parametrize("text", [
    "__merge_forward_expand__msg-1",
    "[文件消息: report.pdf]",
    "[Merged forward message]",
])
def test_build_title_falls_back_for_channel_placeholders(text: str) -> None:
    msg = InboundMessage(
        channel_id="feishu",
        account_id="default",
        message_id="msg-1",
        sender_id="user-1",
        chat_id="room-1",
        chat_type=ChatType.GROUP,
        text=text,
    )

    assert _build_title(msg) == "[Feishu] room-1"


def test_build_title_uses_media_caption_or_following_text() -> None:
    msg = InboundMessage(
        channel_id="telegram",
        account_id="default",
        message_id="msg-1",
        sender_id="user-1",
        chat_id="room-1",
        chat_type=ChatType.DIRECT,
        text="[图片]\n请分析这张图",
    )

    assert _build_title(msg, "[文件: report.pdf]: 总结报告") == "[Telegram] 总结报告"
    assert _build_title(msg) == "[Telegram] 请分析这张图"


def test_channel_title_helpers_share_placeholder_rules() -> None:
    assert is_channel_media_placeholder("[图片消息]") is True
    assert is_channel_media_placeholder("[文件: report.pdf]: 总结报告") is False
    assert is_channel_media_placeholder("[图片]\n请分析这张图") is False
    assert is_channel_media_placeholder("普通消息") is False
    assert extract_channel_title_text("[图片消息]\n请分析这张图") == "请分析这张图"
    assert extract_channel_title_text("[文件: report.pdf]: 总结报告") == "总结报告"


def test_build_title_fallback_does_not_reuse_command_text() -> None:
    msg = InboundMessage(
        channel_id="wecom",
        account_id="default",
        message_id="msg-1",
        sender_id="user-1",
        sender_name="Alice",
        chat_id="room-1",
        chat_type=ChatType.DIRECT,
        text="/new",
    )

    assert _build_title_fallback(msg) == "[Wecom] DM — Alice"


@pytest.mark.asyncio
async def test_list_bindings_filters_by_session_ids() -> None:
    cursor = SimpleNamespace(fetchall=AsyncMock(return_value=[]))
    db = SimpleNamespace(execute=AsyncMock(return_value=cursor))

    with patch(
        "flocks.channel.inbound.session_binding._get_db",
        AsyncMock(return_value=db),
    ):
        result = await SessionBindingService().list_bindings(
            channel_id="wecom",
            session_ids=["ses-1", "ses-2", "ses-1"],
        )

    assert result == []
    sql, params = db.execute.await_args.args
    assert "channel_id = ?" in sql
    assert "session_id IN (?,?)" in sql
    assert params == ["wecom", "ses-1", "ses-2"]


@pytest.mark.asyncio
async def test_latest_active_user_binding_returns_none_when_channel_is_ambiguous() -> None:
    first = SimpleNamespace(session_id="ses_newest")
    second = SimpleNamespace(session_id="ses_other")
    service = SessionBindingService()
    service.list_bindings = AsyncMock(return_value=[first, second])

    with patch(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(
            side_effect=[
                SimpleNamespace(status="active", category="user"),
                SimpleNamespace(status="active", category="user"),
            ]
        ),
    ):
        result = await service.latest_active_user_binding(channel_id="wecom")

    assert result is None


@pytest.mark.asyncio
async def test_resolve_or_create_replaces_archived_binding() -> None:
    service = SessionBindingService()
    existing = SessionBinding(
        channel_id="slack",
        account_id="default",
        chat_id="chat-1",
        chat_type=ChatType.DIRECT,
        thread_id=None,
        session_id="ses_archived",
        agent_id="rex",
        created_at=1,
        last_message_at=2,
    )
    archived = SimpleNamespace(
        status="archived",
        owner_user_id="usr_1",
        owner_username="alice",
    )
    service._find_binding = AsyncMock(return_value=existing)
    service.unbind = AsyncMock()
    service._create_session = AsyncMock(return_value="ses_replacement")
    service._insert = AsyncMock()
    msg = InboundMessage(
        channel_id="slack",
        account_id="default",
        message_id="msg-1",
        sender_id="alice",
        chat_id="chat-1",
        chat_type=ChatType.DIRECT,
        text="hello again",
    )

    with patch(
        "flocks.session.session.Session.get_by_id_unfiltered",
        AsyncMock(return_value=archived),
    ):
        binding = await service.resolve_or_create(msg, default_agent="rex")

    assert binding.session_id == "ses_replacement"
    service.unbind.assert_awaited_once_with("ses_archived")
    service._create_session.assert_awaited_once_with(
        msg,
        default_agent="rex",
        directory=None,
        source_session=archived,
    )
