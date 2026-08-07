"""SessionLoop lifecycle and logical-turn ownership tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.session.core.status import SessionStatus
from flocks.session.message import MessageRole
from flocks.session.runtime.agent_loop import AgentLoop
from flocks.session.runtime.contracts import (
    AgentRunOutcome,
    AgentRunStatus,
    ContinuationDecision,
    StepResult,
)
from flocks.session.session import Session, SessionInfo
from flocks.session.session_loop import (
    SessionLoop,
    _SessionLeaseRegistry,
)


def _session() -> SessionInfo:
    return SessionInfo.model_construct(
        id="ses_runtime",
        projectID="project",
        directory="/tmp/project",
        agent="rex",
        provider="provider",
        model="model",
        category="user",
        status="active",
    )


def _message(message_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=message_id, role=MessageRole.USER)


def _outcome(
    user,
    label: str,
) -> AgentRunOutcome:
    return AgentRunOutcome(
        status=AgentRunStatus.COMPLETED,
        last_user=user,
        last_message=SimpleNamespace(label=label),
        step_result=StepResult(action="stop"),
    )


@pytest.fixture
def loop_io(monkeypatch):
    session = _session()
    active: dict[str, object] = {}
    monkeypatch.setattr(SessionLoop, "_active_turns", active)
    monkeypatch.setattr(
        SessionLoop,
        "_leases",
        _SessionLeaseRegistry(active),
    )
    monkeypatch.setattr(
        Session,
        "get_by_id",
        AsyncMock(return_value=session),
    )
    monkeypatch.setattr(
        "flocks.session.orphan_tools.abort_orphan_running_parts",
        AsyncMock(),
    )
    monkeypatch.setattr(Session, "touch", AsyncMock())
    monkeypatch.setattr("flocks.bus.bus.Bus.publish", AsyncMock())
    return session, active


@pytest.mark.asyncio
async def test_late_input_keeps_one_lease_and_runs_next_logical_turn(
    monkeypatch,
    loop_io,
) -> None:
    session, active = loop_io
    first_user = _message("msg_001")
    second_user = _message("msg_002")
    prepare = AsyncMock()

    async def prepare_turn(turn):
        turn.prepared_user_id = (
            first_user.id if prepare.await_count == 1 else second_user.id
        )

    prepare.side_effect = prepare_turn
    continuation = SimpleNamespace(
        prepare_logical_turn=prepare,
        resolve=AsyncMock(return_value=ContinuationDecision()),
    )
    monkeypatch.setattr(SessionLoop, "_continuation_policy", continuation)
    run = AsyncMock()
    lease_ids: list[int] = []

    async def run_turn(turn, _engine):
        lease_ids.append(id(active[session.id]))
        return _outcome(
            first_user if run.await_count == 1 else second_user,
            "first" if run.await_count == 1 else "second",
        )

    run.side_effect = run_turn
    monkeypatch.setattr(AgentLoop, "run", run)
    monkeypatch.setattr(
        "flocks.session.session_loop.Message.list",
        AsyncMock(
            side_effect=[
                [],
                [first_user, second_user],
                [first_user, second_user],
            ],
        ),
    )

    result = await SessionLoop.run(
        session.id,
        provider_id="provider",
        model_id="model",
    )

    assert result.last_message.label == "second"
    assert run.await_count == 2
    assert prepare.await_count == 2
    assert continuation.resolve.await_count == 2
    assert len(set(lease_ids)) == 1
    assert active == {}
    assert SessionStatus.get(session.id).type == "idle"


@pytest.mark.asyncio
async def test_agent_turn_error_settles_without_replaying_current_input(
    monkeypatch,
    loop_io,
) -> None:
    session, active = loop_io
    user = _message("msg_001")

    async def prepare(turn):
        turn.prepared_user_id = user.id

    continuation = SimpleNamespace(
        prepare_logical_turn=AsyncMock(side_effect=prepare),
        resolve=AsyncMock(),
    )
    monkeypatch.setattr(SessionLoop, "_continuation_policy", continuation)
    run = AsyncMock(side_effect=RuntimeError("turn failed"))
    monkeypatch.setattr(AgentLoop, "run", run)
    monkeypatch.setattr(
        "flocks.session.session_loop.Message.list",
        AsyncMock(side_effect=[[], [user]]),
    )

    result = await SessionLoop.run(
        session.id,
        provider_id="provider",
        model_id="model",
    )

    assert result.action == "error"
    assert result.error == "turn failed"
    assert run.await_count == 1
    assert active == {}
