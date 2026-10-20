"""Session route regressions added by the 2026-09-14 full-case execution (G2a).

Each test names the case ids it covers (SES-xxxx) so the execution record can
be traced back.  They complement ``test_session_routes.py``; the shared
isolation comes from ``tests/server/conftest.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.server.routes import session as session_routes
from flocks.session.core.status import SessionStatus, SessionStatusBusy
from flocks.session.message import Message, MessageRole, ToolPart, ToolStateCompleted
from flocks.session.session import Session


def _user(user_id: str, role: str = "member") -> AuthUser:
    return AuthUser(id=user_id, username=user_id.replace("usr_", ""), role=role, status="active")


async def _owned_session(owner: AuthUser, title: str = "owned") -> "Session":
    return await Session.create(
        project_id="g2a",
        directory="/tmp",
        title=title,
        owner_user_id=owner.id,
        owner_username=owner.username,
    )


async def _noreply(client: AsyncClient, session_id: str, text: str, mock_reply: str | None = None) -> str:
    body = {"parts": [{"type": "text", "text": text}], "noReply": True}
    if mock_reply is not None:
        body["mockReply"] = mock_reply
    resp = await client.post(f"/api/session/{session_id}/message", json=body)
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# GET /status  (SES-0005)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_only_lists_sessions_the_caller_can_read(client: AsyncClient, monkeypatch):
    """SES-0005: another member's busy session id must not leak through /status."""
    owner, other = _user("usr_a"), _user("usr_b")
    session = await _owned_session(owner)
    SessionStatus.set(session.id, SessionStatusBusy())
    try:
        monkeypatch.setattr(session_routes, "require_user", lambda _request: other)
        resp = await client.get("/api/session/status")
        assert resp.status_code == status.HTTP_200_OK
        assert session.id not in resp.json()

        monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
        resp = await client.get("/api/session/status")
        assert resp.json()[session.id]["type"] == "busy"
        assert {"turn", "context"} <= set(resp.json()[session.id])
    finally:
        SessionStatus.clear(session.id)


def test_status_reads_across_instance_scopes():
    """A loop stores status under its project directory; readers elsewhere must still see it."""
    from flocks.session.core import status as status_module

    status_module.SessionStatus._state["/some/project"] = {"ses_scoped": SessionStatusBusy()}
    try:
        assert SessionStatus.get("ses_scoped").type == "busy"
        assert "ses_scoped" in SessionStatus.list()
    finally:
        status_module.SessionStatus._state.pop("/some/project", None)


# ---------------------------------------------------------------------------
# Validation limits  (SES-0033 / 0055 / 0088 / 0117 / 0132)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_rejects_oversized_title_and_free_permission_action(client: AsyncClient):
    resp = await client.post("/api/session", json={"title": "a" * 10240})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # SES-0033
    resp = await client.post("/api/session", json={"permission": [{"permission": "bash", "action": "maybe"}]})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # SES-0055
    resp = await client.post("/api/session", json={"permission": [{"permission": "bash", "action": "deny", "pattern": "rm *"}]})
    assert resp.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_patch_rejects_empty_and_oversized_title(client: AsyncClient, session_id: str):
    assert (await client.patch(f"/api/session/{session_id}", json={"title": ""})).status_code == 422  # SES-0117
    assert (await client.patch(f"/api/session/{session_id}", json={"title": "b" * 10240})).status_code == 422  # SES-0132
    assert (await client.patch(f"/api/session/{session_id}", json={"title": "ok"})).status_code == 200


@pytest.mark.asyncio
async def test_todo_invalid_status_is_422_not_500(client: AsyncClient, session_id: str):
    """SES-0088: the route model must reject bad status before the domain model raises."""
    resp = await client.post(f"/api/session/{session_id}/todo", json=[{"id": "t1", "content": "x", "status": "whatever"}])
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_todo_read_error_returns_empty_list(client: AsyncClient, session_id: str, monkeypatch):
    """SES-0085."""
    from flocks.session.features.todo import Todo

    monkeypatch.setattr(Todo, "get", AsyncMock(side_effect=RuntimeError("boom")))
    resp = await client.get(f"/api/session/{session_id}/todo")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


@pytest.mark.asyncio
async def test_todo_write_with_stale_lifecycle_generation_is_409(client: AsyncClient, session_id: str, monkeypatch):
    """SES-0092: an archive racing the write must not land todos on the archived session."""
    real_generation = Session.lifecycle_generation(session_id)
    calls = iter(range(100))

    def _drifting_generation(cls, sid: str) -> int:
        # the route captures one value, the linearized write sees a newer one
        return real_generation + next(calls)

    monkeypatch.setattr(Session, "lifecycle_generation", classmethod(_drifting_generation))
    resp = await client.post(f"/api/session/{session_id}/todo", json=[{"id": "t1", "content": "x", "status": "pending"}])
    assert resp.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_callable_tools_init_failure_does_not_block_create(client: AsyncClient, monkeypatch):
    """SES-0060."""
    import flocks.session.callable_state as callable_state

    monkeypatch.setattr(
        callable_state,
        "initialize_session_callable_tools",
        AsyncMock(side_effect=RuntimeError("init boom")),
    )
    resp = await client.post("/api/session", json={"title": "tools-init-fail"})
    assert resp.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# context-usage cache  (SES-0027 / 0072 / 0074)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_usage_cache_builds_once_for_concurrent_and_repeated_reads(monkeypatch):
    """SES-0072 / SES-0074."""
    from flocks.session.context_usage import ContextUsageSnapshot

    session_routes._context_usage_cache.clear()
    session_routes._context_usage_inflight.clear()
    calls = 0
    session = SimpleNamespace(time=SimpleNamespace(updated=1), provider=None, model=None)

    async def fake_build(session_id: str, *, session=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return ContextUsageSnapshot(sessionID=session_id, usedTokens=7, contextWindow=100, estimatedTokens=7)

    monkeypatch.setattr(session_routes, "build_context_usage_snapshot", fake_build)
    try:
        results = await asyncio.gather(*[
            session_routes._cached_context_usage_snapshot("ses_cache", session=session) for _ in range(20)
        ])
        assert calls == 1
        assert {r.used_tokens for r in results} == {7}
        again = await session_routes._cached_context_usage_snapshot("ses_cache", session=session)
        assert again.used_tokens == 7 and calls == 1
        # a newer updated timestamp invalidates the key
        newer = SimpleNamespace(time=SimpleNamespace(updated=2), provider=None, model=None)
        await session_routes._cached_context_usage_snapshot("ses_cache", session=newer)
        assert calls == 2
    finally:
        session_routes._context_usage_cache.clear()
        session_routes._context_usage_inflight.clear()


@pytest.mark.asyncio
async def test_list_timing_log_carries_total_count_and_list_ms(client: AsyncClient, monkeypatch):
    """SES-0027: the light list logs total/count/list_ms (debug level when fast)."""
    captured = []

    def fake_timing(logger, event, *, started_at, extra=None, slow_threshold_ms=300):
        captured.append((event, extra or {}))
        return 0

    monkeypatch.setattr(session_routes, "log_route_timing", fake_timing)
    await client.post("/api/session", json={"title": "timing"})
    resp = await client.get("/api/session", params={"view": "list", "limit": 50})
    assert resp.status_code == status.HTTP_200_OK
    events = [e for e in captured if e[0] == "session.list.light.complete"]
    assert events and {"total", "count", "list_ms"} <= set(events[-1][1])


# ---------------------------------------------------------------------------
# PATCH clears failover state  (SES-0119 / 0126)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_pinning_clears_auto_failover_state(client: AsyncClient, session_id: str, monkeypatch):
    from flocks.session.session_loop import SessionLoop

    cleared = []
    monkeypatch.setattr(SessionLoop, "clear_auto_failover_state", classmethod(lambda cls, sid: cleared.append(sid)))
    resp = await client.patch(f"/api/session/{session_id}", json={"provider": "openai", "model": "gpt-4o"})
    assert resp.status_code == status.HTTP_200_OK and resp.json()["model_pinned"] is True
    resp = await client.patch(f"/api/session/{session_id}", json={"model_pinned": True})
    assert resp.status_code == status.HTTP_200_OK and resp.json()["model_auto"] is False
    assert cleared == [session_id, session_id]


# ---------------------------------------------------------------------------
# fork / revert with unknown message  (SES-0161 / 0185 / 0162)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fork_and_revert_reject_unknown_message(client: AsyncClient, session_id: str):
    await _noreply(client, session_id, "hi", mock_reply="hello")
    resp = await client.post(f"/api/session/{session_id}/fork", json={"messageID": "msg_zzz_not_exist"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND  # SES-0161
    resp = await client.post(f"/api/session/{session_id}/revert", json={"messageID": "msg_missing"})
    assert resp.status_code == status.HTTP_404_NOT_FOUND  # SES-0185
    session = await client.get(f"/api/session/{session_id}")
    assert session.json()["revert"] is None


@pytest.mark.asyncio
async def test_fork_copies_tool_parts_with_new_ids(client: AsyncClient, session_id: str):
    """SES-0162: tool parts are rewritten to the forked session/message ids."""
    user_id = await _noreply(client, session_id, "run it")
    assistant = await Message.create(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content="",
        parentID=user_id,
        modelID="mock",
        providerID="mock",
    )
    part = ToolPart(
        id="prt_tool_orig",
        sessionID=session_id,
        messageID=assistant.id,
        type="tool",
        callID="call_1",
        tool="bash",
        state=ToolStateCompleted(
            status="completed",
            input={"command": "ls"},
            output="ok",
            title="ls",
            metadata={},
            time={"start": 1, "end": 2},
        ),
    )
    await Message.add_part(session_id, assistant.id, part)

    resp = await client.post(f"/api/session/{session_id}/fork")
    assert resp.status_code == status.HTTP_200_OK
    forked_id = resp.json()["id"]
    messages = (await client.get(f"/api/session/{forked_id}/message")).json()
    tool_parts = [p for m in messages for p in m["parts"] if p["type"] == "tool"]
    assert len(tool_parts) == 1
    copied = tool_parts[0]
    assert copied["id"] != "prt_tool_orig"
    assert copied["sessionID"] == forked_id
    assert copied["messageID"] in {m["info"]["id"] for m in messages}
    assert copied["tool"] == "bash" and copied["state"]["status"] == "completed"


@pytest.mark.asyncio
async def test_revert_without_snapshot_config_keeps_snapshot_null(client: AsyncClient, session_id: str, monkeypatch):
    """SES-0190: snapshot disabled -> message-level revert only."""
    from flocks.snapshot import snapshot as snapshot_module

    monkeypatch.setattr(snapshot_module.Snapshot, "track", AsyncMock(return_value=None), raising=True)
    first = await _noreply(client, session_id, "one", mock_reply="1")
    await _noreply(client, session_id, "two", mock_reply="2")
    resp = await client.post(f"/api/session/{session_id}/revert", json={"messageID": first})
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["revert"]["messageID"] == first
    assert resp.json()["revert"].get("snapshot") in (None, "")


@pytest.mark.asyncio
async def test_unrevert_is_refused_while_the_session_runs_elsewhere(client: AsyncClient, session_id: str, monkeypatch):
    """Unrevert restores files outside a runner: like revert it needs the session's execution lease."""
    from flocks.session.ownership import SessionExecutionLease
    from flocks.snapshot import snapshot as snapshot_module

    monkeypatch.setattr(snapshot_module.Snapshot, "track", AsyncMock(return_value=None), raising=True)
    first = await _noreply(client, session_id, "one", mock_reply="1")
    await _noreply(client, session_id, "two", mock_reply="2")
    resp = await client.post(f"/api/session/{session_id}/revert", json={"messageID": first})
    assert resp.status_code == status.HTTP_200_OK, resp.text

    runner = await SessionExecutionLease.acquire(session_id)  # running in another process
    try:
        resp = await client.post(f"/api/session/{session_id}/unrevert")
        assert resp.status_code == status.HTTP_409_CONFLICT, resp.text
    finally:
        await runner.release()
    assert (await client.get(f"/api/session/{session_id}")).json()["revert"]["messageID"] == first

    resp = await client.post(f"/api/session/{session_id}/unrevert")
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["revert"] is None


# ---------------------------------------------------------------------------
# diff / metrics / recent / statistics access  (SES-0171 / 0172 / 0173 / 0196~0209 / 0213 / 0215 / 0216)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diff_requires_existing_readable_session(client: AsyncClient, monkeypatch):
    owner, other = _user("usr_a"), _user("usr_b")
    session = await _owned_session(owner)
    monkeypatch.setattr(session_routes, "require_user", lambda _request: other)
    assert (await client.get("/api/session/ses_x/diff")).status_code == status.HTTP_404_NOT_FOUND  # SES-0171
    assert (await client.get(f"/api/session/{session.id}/diff")).status_code == status.HTTP_403_FORBIDDEN  # SES-0172
    monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
    assert (await client.get(f"/api/session/{session.id}/diff")).json() == []


@pytest.mark.asyncio
async def test_diff_storage_error_returns_empty_list(client: AsyncClient, session_id: str, monkeypatch):
    """SES-0173."""
    from flocks.storage.storage import Storage

    monkeypatch.setattr(Storage, "read", AsyncMock(side_effect=RuntimeError("disk")))
    resp = await client.get(f"/api/session/{session_id}/diff")
    assert resp.status_code == status.HTTP_200_OK and resp.json() == []


@pytest.mark.asyncio
async def test_metrics_and_recent_are_not_shadowed_by_session_id_route(client: AsyncClient, monkeypatch):
    """SES-0196 / 0198 / 0199 / 0206 / 0207 / 0208 / 0209."""
    admin, member = _user("usr_admin", "admin"), _user("usr_member")
    monkeypatch.setattr(session_routes, "require_user", lambda _request: admin)
    await _owned_session(admin, "recent-a")
    await _owned_session(member, "recent-b")

    resp = await client.get("/api/session/metrics")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "success" and isinstance(resp.json()["metrics"], dict)

    resp = await client.get("/api/session/recent")
    assert resp.status_code == status.HTTP_200_OK
    titles = [item["title"] for item in resp.json()]
    assert "recent-a" in titles and "recent-b" not in titles
    updated = [item["time"]["updated"] for item in resp.json()]
    assert updated == sorted(updated, reverse=True)
    assert (await client.get("/api/session/recent", params={"limit": 51})).status_code == 422
    assert (await client.get("/api/session/recent", params={"limit": 0})).status_code == 422

    monkeypatch.setattr(session_routes, "require_user", lambda _request: member)
    assert (await client.get("/api/session/metrics")).status_code == status.HTTP_403_FORBIDDEN
    resp = await client.get("/api/session/recent")
    assert [item["title"] for item in resp.json()] == ["recent-b"]


@pytest.mark.asyncio
async def test_session_metrics_and_statistics_require_readable_session(client: AsyncClient, monkeypatch):
    """SES-0203 / 0204 / 0213."""
    owner, other = _user("usr_a"), _user("usr_b")
    session = await _owned_session(owner)
    monkeypatch.setattr(session_routes, "require_user", lambda _request: other)
    assert (await client.get("/api/session/ses_x/metrics")).status_code == status.HTTP_404_NOT_FOUND
    assert (await client.get(f"/api/session/{session.id}/metrics")).status_code == status.HTTP_403_FORBIDDEN
    assert (await client.get(f"/api/session/{session.id}/statistics")).status_code == status.HTTP_403_FORBIDDEN
    monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
    resp = await client.get(f"/api/session/{session.id}/metrics")
    assert resp.status_code == status.HTTP_200_OK and resp.json()["metrics"] is None


@pytest.mark.asyncio
async def test_statistics_counts_archived_messages_and_reports_errors(client: AsyncClient, session_id: str, monkeypatch):
    """SES-0215 / SES-0216."""
    first = await _noreply(client, session_id, "old", mock_reply="1")
    await Message.update(session_id, first, compacted=True)
    resp = await client.get(f"/api/session/{session_id}/statistics")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["messageCount"] == 2

    monkeypatch.setattr(Message, "list_with_parts", AsyncMock(side_effect=RuntimeError("stats boom")))
    resp = await client.get(f"/api/session/{session_id}/statistics")
    assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert resp.json()["message"].startswith("Failed to get session statistics:")


# ---------------------------------------------------------------------------
# audit payload carries affected_sessions  (SES-0094 / 0108 / 0115, OSS side)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_archive_and_delete_audit_carry_tree_size(client: AsyncClient, session_id: str, monkeypatch):
    emitted = []

    async def fake_emit(event_type, payload):
        emitted.append((event_type, payload))

    monkeypatch.setattr(session_routes, "emit_audit_event", fake_emit)
    forked = (await client.post(f"/api/session/{session_id}/fork")).json()
    assert forked["parentID"] == session_id
    assert (await client.post(f"/api/session/{session_id}/archive")).status_code == 200
    assert (await client.post(f"/api/session/{session_id}/restore")).status_code == 200
    assert (await client.delete(f"/api/session/{session_id}")).status_code == 200
    by_action = {p["action"]: p for _, p in emitted if _ == "session_action"}
    assert by_action["archive"]["affected_sessions"] == 2
    assert by_action["permanent_delete"]["affected_sessions"] == 2


# ---------------------------------------------------------------------------
# summarize forwards auto flag  (SES-0181)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summarize_forwards_auto_flag(client: AsyncClient, session_id: str, monkeypatch):
    seen = []

    async def fake_compaction(sid, **kwargs):
        seen.append(kwargs.get("auto"))
        return ("rex", kwargs.get("explicit_provider_id"), kwargs.get("explicit_model_id"))

    monkeypatch.setattr(session_routes, "_run_session_compaction", fake_compaction)
    await _noreply(client, session_id, "x", mock_reply="y")
    for auto in (True, False):
        resp = await client.post(f"/api/session/{session_id}/summarize", json={"providerID": "p", "modelID": "m", "auto": auto})
        assert resp.status_code == status.HTTP_200_OK and resp.json() is True
    for _ in range(50):
        if len(seen) == 2:
            break
        await asyncio.sleep(0.02)
    assert seen == [True, False]


# ---------------------------------------------------------------------------
# message parts / prompt queue / permissions access  (SES-0227 / 0345 / 0352 / 0296 / 0305 / 0313 / 0317 / 0324 / 0399 / 0400)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whitespace_only_text_is_rejected(client: AsyncClient, session_id: str):
    resp = await client.post(f"/api/session/{session_id}/message", json={"parts": [{"type": "text", "text": "   "}], "noReply": True})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST  # SES-0227


@pytest.mark.asyncio
async def test_unknown_part_delete_and_update_return_404(client: AsyncClient, session_id: str):
    message_id = await _noreply(client, session_id, "hi")
    resp = await client.delete(f"/api/session/{session_id}/message/{message_id}/part/prt_missing")
    assert resp.status_code == status.HTTP_404_NOT_FOUND  # SES-0345
    resp = await client.patch(
        f"/api/session/{session_id}/message/{message_id}/part/prt_missing",
        json={"id": "prt_missing", "messageID": message_id, "sessionID": session_id, "type": "text", "text": "x"},
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND  # SES-0352


@pytest.mark.asyncio
async def test_prompt_queue_routes_enforce_session_write_access(client: AsyncClient, monkeypatch):
    owner, reader, stranger = _user("usr_a"), _user("usr_b"), _user("usr_c")
    session = await Session.create(
        project_id="g2a", directory="/tmp", title="queue", owner_user_id=owner.id, owner_username=owner.username,
        metadata={"shared_local": True},
    )
    monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
    queued = await client.post(f"/api/session/{session.id}/prompt_queue", json={"parts": [{"type": "text", "text": "queued"}]})
    assert queued.status_code == status.HTTP_202_ACCEPTED
    queue_id = queued.json()["queueID"]

    monkeypatch.setattr(session_routes, "require_user", lambda _request: reader)
    assert (await client.get(f"/api/session/{session.id}/prompt_queue")).status_code == 200  # shared read-only may list
    assert (await client.post(f"/api/session/{session.id}/prompt_queue", json={"parts": [{"type": "text", "text": "x"}]})).status_code == 403  # SES-0305
    assert (await client.patch(f"/api/session/{session.id}/prompt_queue/{queue_id}", json={"text": "hijack"})).status_code == 403  # SES-0313
    assert (await client.delete(f"/api/session/{session.id}/prompt_queue/{queue_id}")).status_code == 403  # SES-0317
    assert (await client.post(f"/api/session/{session.id}/prompt_queue/{queue_id}/run_now")).status_code == 403  # SES-0324

    monkeypatch.setattr(session_routes, "require_user", lambda _request: stranger)
    assert (await client.get(f"/api/session/{session.id}/prompt_queue")).status_code == 200  # shared_local is readable by every local user

    private = await _owned_session(owner, "private")
    assert (await client.get(f"/api/session/{private.id}/prompt_queue")).status_code == 403  # SES-0296 (设计应然 403)
    assert (await client.post(f"/api/session/{private.id}/prompt_queue", json={"parts": [{"type": "text", "text": "x"}]})).status_code == 403


@pytest.mark.asyncio
async def test_permission_compat_route_checks_ownership_and_enum(client: AsyncClient, monkeypatch):
    owner, other = _user("usr_a"), _user("usr_b")
    session = await _owned_session(owner)
    monkeypatch.setattr(session_routes, "require_user", lambda _request: other)
    resp = await client.post(f"/api/session/{session.id}/permissions/perm_x", json={"response": "allow"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN  # SES-0399
    monkeypatch.setattr(session_routes, "require_user", lambda _request: owner)
    resp = await client.post(f"/api/session/{session.id}/permissions/perm_x", json={"response": "maybe"})
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # SES-0400
    resp = await client.post(f"/api/session/{session.id}/permissions/perm_x", json={"response": "deny"})
    assert resp.status_code == status.HTTP_200_OK  # SES-0398: unknown ids are persisted as deferred replies


# ---------------------------------------------------------------------------
# shell route  (SES-0389 / SES-0393)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_payload_carries_a_tool_call_id(monkeypatch):
    """SES-0389: audit bridges record the attempt under the call id."""
    from flocks.session import runner as runner_module
    from flocks.session import tool_execution as tool_execution_module

    seen = {}

    async def fake_lifecycle(payload, effect, *, patched_effect=None):
        seen["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(tool_execution_module, "run_tool_execution_lifecycle", fake_lifecycle)
    monkeypatch.setattr(
        runner_module.Session,
        "get_by_id",
        AsyncMock(return_value=SimpleNamespace(id="ses_shell", directory="/tmp")),
    )
    result = await runner_module.SessionRunner.shell(session_id="ses_shell", agent="rex", command="echo hi")
    assert result == {"ok": True}
    assert seen["payload"]["tool_execution"]["execution"]["tool_call_id"]


@pytest.mark.asyncio
async def test_shell_route_maps_gate_deny_to_forbidden(monkeypatch):
    """SES-0393: a policy deny (PermissionError) is a 403, not a 500."""
    monkeypatch.setattr(session_routes, "require_user", lambda _request: _user("usr_a"))
    monkeypatch.setattr(session_routes, "_get_session_by_id_unfiltered", AsyncMock(return_value=object()))
    monkeypatch.setattr(session_routes, "_require_session_write_access", lambda _s, _u: None)

    class _Guard:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(Session, "active_operation", classmethod(lambda cls, sid: _Guard()))
    monkeypatch.setattr(
        "flocks.session.runner.SessionRunner.shell",
        AsyncMock(side_effect=PermissionError("destructive shell commands are blocked")),
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await session_routes.run_shell_command(
            "ses_1",
            session_routes.ShellRequest(agent="rex", command="rm -rf /"),
            SimpleNamespace(),
        )
    assert error.value.status_code == status.HTTP_403_FORBIDDEN
    assert error.value.detail == "execution stopped by extension"


@pytest.mark.asyncio
async def test_assistant_summary_flag_is_serialized(client: AsyncClient, session_id: str):
    """SES-0174: the compaction summary message must be identifiable in the API."""
    user_id = await _noreply(client, session_id, "hi")
    summary = await Message.create(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content="summary",
        parentID=user_id,
        modelID="m",
        providerID="p",
        summary=True,
    )
    resp = await client.get(f"/api/session/{session_id}/message/{summary.id}")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["info"]["summary"] is True


@pytest.mark.asyncio
async def test_session_diff_reads_the_key_summary_writes(client: AsyncClient, session_id: str):
    """SES-0168: the route must read ``session_diff:<id>`` (what SessionSummary stores)."""
    from flocks.storage.storage import Storage

    await Storage.set(
        f"session_diff:{session_id}",
        [{"file": "a.txt", "before": "", "after": "x\n", "additions": 1, "deletions": 0}],
        "session_diff",
    )
    resp = await client.get(f"/api/session/{session_id}/diff")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == [{"file": "a.txt", "before": "", "after": "x\n", "additions": 1, "deletions": 0}]


# ---------------------------------------------------------------------------
# system prompt override is persisted on the user message  (SES-0275)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_system_override_is_stored_on_user_message(client: AsyncClient, session_id: str):
    """SES-0275: the runner reads ``system`` from the stored user message."""
    resp = await client.post(
        f"/api/session/{session_id}/message",
        json={"parts": [{"type": "text", "text": "ahoy"}], "noReply": True, "system": "You are a pirate."},
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    users = [m for m in await Message.list(session_id) if m.role == MessageRole.USER]
    assert users and users[-1].system == "You are a pirate."


# ---------------------------------------------------------------------------
# manual summarize surfaces the summary-model error  (SES-0179)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_summarize_reports_summary_model_failure(session_id: str, monkeypatch):
    """SES-0179: a 'skipped' round caused by a provider failure raises the provider text."""
    import flocks.session.lifecycle.compaction as compaction_pkg
    from flocks.session.lifecycle.compaction import compaction as compaction_mod

    async def fake_run_compaction(sid, **kwargs):
        # what SessionCompaction.process does when summarize_single_pass fails
        compaction_mod._record_compaction_error(sid, RuntimeError("OpenAI API key not configured"))
        return "skipped"

    monkeypatch.setattr(compaction_pkg, "run_compaction", fake_run_compaction)
    monkeypatch.setattr(session_routes, "_resolve_compaction_context", AsyncMock(return_value=("rex", "openai", "gpt-4o")))
    monkeypatch.setattr(session_routes, "_publish_context_usage_update", AsyncMock())
    await Message.create(session_id=session_id, role=MessageRole.USER, content="hi", time={"created": 1})

    with pytest.raises(RuntimeError, match="OpenAI API key not configured"):
        await session_routes._run_session_compaction(session_id, requested_agent="rex", explicit_provider_id="openai", explicit_model_id="gpt-4o", event_publish_callback=AsyncMock())

    # a legitimate skip (nothing stashed) stays silent
    async def skip_quietly(sid, **kwargs):
        return "skipped"

    monkeypatch.setattr(compaction_pkg, "run_compaction", skip_quietly)
    result = await session_routes._run_session_compaction(session_id, requested_agent="rex", explicit_provider_id="openai", explicit_model_id="gpt-4o", event_publish_callback=AsyncMock())
    assert result == ("rex", "openai", "gpt-4o")
