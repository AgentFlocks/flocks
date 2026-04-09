from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.server.routes.session import (
    CommandRequest,
    _resolve_compaction_context,
    _run_session_compaction,
    send_session_command,
)
from flocks.session.lifecycle.compaction.compaction import SessionCompaction
from flocks.session.message import CompactionPart, MessageRole


@pytest.mark.asyncio
async def test_run_session_compaction_uses_latest_user_and_publishes_status():
    session = SimpleNamespace(id="ses_test", directory="/tmp")
    messages = [
        SimpleNamespace(
            id="msg_user",
            role=MessageRole.USER,
            agent="rex",
            model={"providerID": "anthropic", "modelID": "claude-test"},
        ),
        SimpleNamespace(id="msg_asst", role=MessageRole.ASSISTANT),
    ]
    events: list[tuple[str, dict]] = []

    async def publish_event(event_name: str, payload: dict) -> None:
        events.append((event_name, payload))

    with patch(
        "flocks.server.routes.session.Session.get_by_id",
        new=AsyncMock(return_value=session),
    ), patch(
        "flocks.server.routes.session._resolve_compaction_context",
        new=AsyncMock(return_value=("rex", "anthropic", "claude-test")),
    ), patch(
        "flocks.session.lifecycle.revert.SessionRevert.cleanup",
        new=AsyncMock(),
    ), patch(
        "flocks.session.message.Message.list",
        new=AsyncMock(return_value=messages),
    ), patch(
        "flocks.provider.provider.Provider.resolve_model_info",
        return_value=(200_000, 8_192, None),
    ), patch(
        "flocks.session.lifecycle.compaction.compaction.SessionCompaction.process",
        new=AsyncMock(return_value="continue"),
    ) as process_mock:
        result = await _run_session_compaction(
            "ses_test",
            event_publish_callback=publish_event,
        )

    assert result == ("rex", "anthropic", "claude-test")
    process_mock.assert_awaited_once()
    assert process_mock.await_args.kwargs["parent_id"] == "msg_user"
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
async def test_session_compaction_create_adds_compaction_part():
    created_message = SimpleNamespace(id="msg_compact")

    with patch(
        "flocks.session.message.Message.create",
        new=AsyncMock(return_value=created_message),
    ) as create_mock, patch(
        "flocks.session.message.Message.add_part",
        new=AsyncMock(),
    ) as add_part_mock:
        await SessionCompaction.create(
            session_id="ses_test",
            agent="rex",
            model_provider_id="anthropic",
            model_id="claude-test",
            auto=False,
        )

    create_mock.assert_awaited_once()
    add_part_mock.assert_awaited_once()
    created_part = add_part_mock.await_args.args[2]
    assert isinstance(created_part, CompactionPart)
    assert created_part.sessionID == "ses_test"
    assert created_part.messageID == "msg_compact"
    assert created_part.auto is False


@pytest.mark.asyncio
async def test_resolve_compaction_context_prefers_requested_agent_override():
    messages = [
        SimpleNamespace(
            id="msg_user",
            role=MessageRole.USER,
            agent="rex",
            model={"providerID": "history-provider", "modelID": "history-model"},
        ),
    ]
    override_model = {"providerID": "override-provider", "modelID": "override-model"}
    requested_agent = SimpleNamespace(model={"providerID": "agent-provider", "modelID": "agent-model"})

    with patch(
        "flocks.session.message.Message.list",
        new=AsyncMock(return_value=messages),
    ), patch(
        "flocks.storage.storage.Storage.read",
        new=AsyncMock(return_value={"security": override_model}),
    ), patch(
        "flocks.agent.registry.Agent.get",
        new=AsyncMock(side_effect=[requested_agent]),
    ), patch(
        "flocks.config.config.Config.resolve_default_llm",
        new=AsyncMock(return_value={"provider_id": "config-provider", "model_id": "config-model"}),
    ):
        resolved = await _resolve_compaction_context(
            "ses_test",
            requested_agent="security",
        )

    assert resolved == ("security", "override-provider", "override-model")


@pytest.mark.asyncio
async def test_compact_command_uses_resolved_compaction_context_for_message_metadata():
    session = SimpleNamespace(id="ses_test", directory="/tmp")

    async def provide_side_effect(*, directory, init, fn):
        return await fn()

    with patch(
        "flocks.server.routes.session.Session.get_by_id",
        new=AsyncMock(return_value=session),
    ), patch(
        "flocks.server.routes.session._resolve_compaction_context",
        new=AsyncMock(return_value=("security", "threatbook-cn-llm", "minimax-m2.7")),
    ), patch(
        "flocks.session.message.Message.create",
        new=AsyncMock(),
    ) as create_message_mock, patch(
        "flocks.server.routes.event.publish_event",
        new=AsyncMock(),
    ), patch(
        "flocks.project.instance.Instance.provide",
        new=AsyncMock(side_effect=provide_side_effect),
    ), patch(
        "flocks.server.routes.session._run_session_compaction",
        new=AsyncMock(),
    ) as run_compaction_mock:
        response = await send_session_command(
            "ses_test",
            CommandRequest(command="compact", arguments=""),
        )
        await asyncio.sleep(0.01)

    assert response["status"] == "accepted"
    create_message_mock.assert_awaited()
    assert create_message_mock.await_args.kwargs["agent"] == "security"
    assert create_message_mock.await_args.kwargs["model"] == {
        "providerID": "threatbook-cn-llm",
        "modelID": "minimax-m2.7",
    }
    run_compaction_mock.assert_awaited_once()
    assert run_compaction_mock.await_args.kwargs["explicit_provider_id"] == "threatbook-cn-llm"
    assert run_compaction_mock.await_args.kwargs["explicit_model_id"] == "minimax-m2.7"
