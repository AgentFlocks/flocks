from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.inbound.session_binding import SessionBinding, SessionBindingService


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
