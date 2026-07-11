"""Security-context propagation for Channel → session → tool execution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.channel.inbound.dispatcher import InboundDispatcher
from flocks.session.streaming.stream_processor import StreamProcessor


class _Callbacks:
    def to_loop_callbacks(self):
        return object()

    async def deliver_text(self, _text: str) -> None:
        return None

    async def on_error(self, _error: str) -> None:
        return None


@pytest.mark.asyncio
async def test_channel_run_passes_resolved_subject_to_session_loop() -> None:
    security_context = {
        "entry": "channel",
        "subject": {"subject_id": "channel-user", "verified": True},
    }
    binding = SimpleNamespace(session_id="ses_channel_security", agent_id="rex")
    loop_result = SimpleNamespace(last_message=None)

    with patch(
        "flocks.session.session_loop.SessionLoop.run",
        new=AsyncMock(return_value=loop_result),
    ) as run:
        await InboundDispatcher._run_agent(
            binding,
            _Callbacks(),
            security_context=security_context,
        )

    assert run.await_args.kwargs["security_context"] == security_context


def test_stream_processor_keeps_subject_when_combining_sandbox_metadata() -> None:
    assistant_message = MagicMock(id="msg_channel_security")
    agent = MagicMock(name="rex")
    security_context = {
        "entry": "channel",
        "subject": {"subject_id": "channel-user", "verified": True},
    }
    processor = StreamProcessor(
        session_id="ses_channel_security",
        assistant_message=assistant_message,
        agent=agent,
        security_context=security_context,
    )

    extra = processor._tool_context_extra({"sandbox": {"workspace_access": "ro"}})

    assert extra["subject"] == security_context["subject"]
    assert extra["entry"] == "channel"
    assert extra["sandbox"] == {"workspace_access": "ro"}
