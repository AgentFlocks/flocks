from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.inbound.session_binding import SessionBindingService


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
