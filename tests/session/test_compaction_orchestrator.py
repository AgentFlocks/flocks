from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.session.core.status import SessionStatus
from flocks.session.lifecycle.compaction import run_compaction


@pytest.mark.asyncio
async def test_run_compaction_restores_idle_status_by_default():
    SessionStatus.clear_all()
    events: list[tuple[str, dict]] = []

    async def publish_event(event_name: str, payload: dict) -> None:
        events.append((event_name, payload))

    with patch(
        "flocks.session.lifecycle.compaction.orchestrator.Provider.resolve_model_info",
        return_value=(200_000, 8_192, None),
    ), patch(
        "flocks.session.lifecycle.compaction.orchestrator.SessionCompaction.process",
        new=AsyncMock(return_value="continue"),
    ) as process_mock:
        result = await run_compaction(
            "ses_test",
            parent_message_id="msg_user",
            messages=[SimpleNamespace(id="msg_user")],
            provider_id="anthropic",
            model_id="claude-test",
            auto=False,
            event_publish_callback=publish_event,
        )

    assert result == "continue"
    process_mock.assert_awaited_once()
    assert process_mock.await_args.kwargs["messages"] == [{"id": "msg_user"}]
    assert process_mock.await_args.kwargs["policy"] is not None
    assert SessionStatus.get("ses_test").type == "idle"
    assert events[0] == (
        "session.status",
        {
            "sessionID": "ses_test",
            "status": {"type": "compacting", "message": "Compacting context…"},
        },
    )
    assert events[-1] == (
        "session.status",
        {"sessionID": "ses_test", "status": {"type": "idle"}},
    )


@pytest.mark.asyncio
async def test_run_compaction_can_restore_busy_status():
    SessionStatus.clear_all()
    events: list[tuple[str, dict]] = []

    async def publish_event(event_name: str, payload: dict) -> None:
        events.append((event_name, payload))

    with patch(
        "flocks.session.lifecycle.compaction.orchestrator.Provider.resolve_model_info",
        return_value=(200_000, 8_192, None),
    ), patch(
        "flocks.session.lifecycle.compaction.orchestrator.SessionCompaction.process",
        new=AsyncMock(return_value="continue"),
    ):
        await run_compaction(
            "ses_test",
            parent_message_id="msg_user",
            messages=[{"id": "msg_user"}],
            provider_id="anthropic",
            model_id="claude-test",
            auto=True,
            event_publish_callback=publish_event,
            status_after="busy",
        )

    assert SessionStatus.get("ses_test").type == "busy"
    assert events[-1] == (
        "session.status",
        {"sessionID": "ses_test", "status": {"type": "busy"}},
    )
    SessionStatus.clear("ses_test")
