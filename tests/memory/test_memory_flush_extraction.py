"""Tests for lifecycle-owned Daily Memory extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.memory.flush import extract_and_save


class _ChatMessage:
    """Minimal chat message accepted by the extraction helper."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


@pytest.mark.asyncio
async def test_extract_and_save_honors_nothing_without_summary_fallback() -> None:
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(content="NOTHING")),
    )

    with patch(
        "flocks.memory.daily.DailyMemory.write_daily",
        new_callable=AsyncMock,
    ) as write_daily:
        await extract_and_save(
            session_id="ses_nothing",
            summary="transient compaction summary",
            chat_messages=[_ChatMessage("user", "hello")],
            model_id="model",
            provider=provider,
            ChatMessage=_ChatMessage,
        )

    write_daily.assert_not_awaited()
    prompt = provider.chat.await_args.kwargs["messages"][0].content
    assert "Exclude secrets" in prompt
    assert "task status" in prompt
    assert "facts cheaply rediscoverable" in prompt
