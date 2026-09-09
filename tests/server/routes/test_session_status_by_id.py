"""Tests for querying one session's runtime status."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest
from fastapi import status
from flocks.auth.context import AuthUser
from flocks.project.instance import Instance
from flocks.server.routes import session as session_routes
from flocks.session.core.status import (
    SessionStatus,
    SessionStatusBusy,
    SessionStatusRetry,
)


async def _set_status_for_directory(directory: str, update: Callable[[], None]) -> None:
    await Instance.provide(directory=directory, fn=update)


@pytest.fixture(autouse=True)
def _reset_runtime_status() -> Iterator[None]:
    SessionStatus._state.clear()
    active_sessions = getattr(session_routes.router, "_prompt_queue_active_sessions", set())
    active_sessions.clear()
    yield
    SessionStatus._state.clear()
    active_sessions = getattr(session_routes.router, "_prompt_queue_active_sessions", set())
    active_sessions.clear()


@pytest.mark.asyncio
async def test_status_by_id_returns_explicit_idle(client, session_id: str) -> None:
    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["sessionID"] == session_id
    assert payload["lifecycleStatus"] == "active"
    assert payload["status"] == {"type": "idle"}
    assert payload["isProcessing"] is False
    assert payload["pendingPromptCount"] == 0
    assert isinstance(payload["observedAt"], int)


@pytest.mark.asyncio
async def test_status_by_id_returns_busy(client, session_id: str) -> None:
    session = await session_routes._get_session_by_id_unfiltered(session_id)
    assert session is not None
    await _set_status_for_directory(
        session.directory,
        lambda: SessionStatus.set(session_id, SessionStatusBusy()),
    )

    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == {"type": "busy"}
    assert response.json()["isProcessing"] is True


@pytest.mark.asyncio
async def test_status_by_id_preserves_retry_details(client, session_id: str) -> None:
    session = await session_routes._get_session_by_id_unfiltered(session_id)
    assert session is not None
    await _set_status_for_directory(
        session.directory,
        lambda: SessionStatus.set(
            session_id,
            SessionStatusRetry(attempt=2, message="Rate limited", next=123456789),
        ),
    )

    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == {
        "type": "retry",
        "attempt": 2,
        "message": "Rate limited",
        "next": 123456789,
    }
    assert response.json()["isProcessing"] is True


@pytest.mark.asyncio
async def test_status_by_id_reports_prompt_chain_as_queued(client, session_id: str) -> None:
    session_routes._set_prompt_chain_active(session_id, True)

    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == {"type": "queued"}
    assert response.json()["isProcessing"] is True


@pytest.mark.asyncio
async def test_prompt_async_is_observable_as_queued_before_background_loop(
    client,
    session_id: str,
    monkeypatch,
) -> None:
    def _hold_background_work(coro, **_kwargs) -> None:
        coro.close()

    monkeypatch.setattr(session_routes, "_schedule_background_coro", _hold_background_work)

    accepted = await client.post(
        f"/api/session/{session_id}/prompt_async",
        json={"parts": [{"type": "text", "text": "hello"}]},
    )
    status_response = await client.get(f"/api/session/{session_id}/status")

    assert accepted.status_code == status.HTTP_202_ACCEPTED
    assert accepted.json()["status"] == "accepted"
    assert status_response.status_code == status.HTTP_200_OK
    assert status_response.json()["status"] == {"type": "queued"}
    assert status_response.json()["isProcessing"] is True


@pytest.mark.asyncio
async def test_shell_is_busy_while_active_and_idle_after_completion(
    client,
    session_id: str,
    monkeypatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def _hold_shell(**_kwargs):
        started.set()
        await release.wait()
        return {"info": {"id": "msg_shell"}, "parts": []}

    monkeypatch.setattr(
        "flocks.session.runner.SessionRunner.shell",
        _hold_shell,
    )

    shell_request = asyncio.create_task(
        client.post(
            f"/api/session/{session_id}/shell",
            json={"agent": "rex", "command": "printf done"},
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    try:
        running_response = await client.get(f"/api/session/{session_id}/status")
    finally:
        release.set()

    shell_response = await asyncio.wait_for(shell_request, timeout=1)
    completed_response = await client.get(f"/api/session/{session_id}/status")

    assert running_response.status_code == status.HTTP_200_OK
    assert running_response.json()["status"] == {"type": "busy"}
    assert running_response.json()["isProcessing"] is True
    assert running_response.json()["pendingPromptCount"] == 0
    assert shell_response.status_code == status.HTTP_200_OK
    assert completed_response.status_code == status.HTTP_200_OK
    assert completed_response.json()["status"] == {"type": "idle"}
    assert completed_response.json()["isProcessing"] is False
    assert completed_response.json()["pendingPromptCount"] == 0
    assert not session_routes.Session.has_active_operations(session_id)


@pytest.mark.asyncio
async def test_shell_response_is_available_after_message_cache_reload(
    client,
    session_id: str,
    monkeypatch,
) -> None:
    from flocks.session.message import Message

    process = SimpleNamespace(
        communicate=lambda: asyncio.sleep(
            0,
            result=(b"PR741_SHELL_PERSISTENCE_OK", b""),
        ),
        returncode=0,
    )

    async def _create_subprocess(*_args, **_kwargs):
        return process

    async def _run_lifecycle(_payload, effect, **_kwargs):
        return await effect()

    monkeypatch.setattr(
        "flocks.session.runner.asyncio.create_subprocess_shell",
        _create_subprocess,
    )
    monkeypatch.setattr(
        "flocks.session.tool_execution.run_tool_execution_lifecycle",
        _run_lifecycle,
    )

    shell_response = await client.post(
        f"/api/session/{session_id}/shell",
        json={"agent": "rex", "command": "printf PR741_SHELL_PERSISTENCE_OK"},
    )
    assert shell_response.status_code == status.HTTP_200_OK
    assistant_id = shell_response.json()["info"]["id"]

    Message.invalidate_cache(session_id)
    history_response = await client.get(f"/api/session/{session_id}/message")

    assert history_response.status_code == status.HTTP_200_OK
    assistant = next(
        item
        for item in history_response.json()
        if item["info"]["id"] == assistant_id
    )
    assert assistant["info"]["finish"] == "stop"
    tool_part = next(part for part in assistant["parts"] if part["type"] == "tool")
    assert tool_part["tool"] == "bash"
    assert tool_part["state"]["status"] == "completed"
    assert tool_part["state"]["input"]["command"] == (
        "printf PR741_SHELL_PERSISTENCE_OK"
    )
    assert tool_part["state"]["output"] == "PR741_SHELL_PERSISTENCE_OK"


@pytest.mark.asyncio
async def test_status_by_id_prefers_busy_and_counts_queued_prompts(client, session_id: str) -> None:
    from flocks.session.interaction_queue import InteractionQueue

    session = await session_routes._get_session_by_id_unfiltered(session_id)
    assert session is not None
    await _set_status_for_directory(
        session.directory,
        lambda: SessionStatus.set(session_id, SessionStatusBusy()),
    )
    await InteractionQueue.enqueue(
        session_id,
        parts=[{"type": "text", "text": "next"}],
    )

    try:
        response = await client.get(f"/api/session/{session_id}/status")
    finally:
        await InteractionQueue.clear(session_id)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == {"type": "busy"}
    assert response.json()["pendingPromptCount"] == 1


@pytest.mark.asyncio
async def test_status_by_id_resolves_the_session_directory(client, tmp_path) -> None:
    directory = str(tmp_path.resolve())
    create_response = await client.post(
        "/api/session",
        json={"title": "other directory"},
        headers={"X-Flocks-Directory": directory},
    )
    assert create_response.status_code == status.HTTP_200_OK
    session_id = create_response.json()["id"]
    await _set_status_for_directory(
        directory,
        lambda: SessionStatus.set(session_id, SessionStatusBusy()),
    )

    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == {"type": "busy"}


@pytest.mark.asyncio
async def test_status_by_id_returns_archived_lifecycle(client, session_id: str) -> None:
    archive_response = await client.post(f"/api/session/{session_id}/archive")
    assert archive_response.status_code == status.HTTP_200_OK

    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["lifecycleStatus"] == "archived"
    assert response.json()["status"] == {"type": "idle"}
    assert response.json()["isProcessing"] is False


@pytest.mark.asyncio
async def test_status_by_id_returns_not_found(client) -> None:
    response = await client.get("/api/session/session_missing/status")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_status_by_id_enforces_read_access(client, session_id: str, monkeypatch) -> None:
    viewer = AuthUser(
        id="usr_other",
        username="other",
        role="user",
        status="active",
    )
    monkeypatch.setattr(session_routes, "require_user", lambda _request: viewer)

    response = await client.get(f"/api/session/{session_id}/status")

    assert response.status_code == status.HTTP_403_FORBIDDEN
