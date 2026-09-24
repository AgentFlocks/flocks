"""Real independent workers sharing PostgreSQL, with private homes and fixtures."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pytest

from flocks.session.message import Message, MessageRole, TextPart, ToolPart, ToolStateRunning
from flocks.session.ownership import SessionExecutionLease, SessionOwnershipLost
from flocks.session.session import Session
from flocks.storage.storage import Storage

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="isolated PostgreSQL required")

WORKER = r'''
import asyncio, json, sys
from flocks.storage.storage import Storage
from flocks.session.message import Message, MessageRole, TextPart, ToolPart, ToolStateRunning
from flocks.session.ownership import SessionExecutionLease

async def run():
    await Storage.init()
    Message._PARTS_PERSIST_DEBOUNCE_MS = 60000
    lease = None
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        command = json.loads(line)
        sid, op = command['sid'], command['op']
        try:
            if op == 'warm':
                result = [m.info.id for m in await Message.list_with_parts(sid, include_archived=True)]
            elif op == 'create':
                msg = await Message.create(sid, MessageRole.USER, command['id'], id=command['id'], time={'created': command.get('at', 100)})
                result = msg.id
            elif op == 'update':
                result = (await Message.update(sid, command['id'], **command['patch'])).model_dump()
            elif op == 'part':
                part = TextPart(id=command['part'], sessionID=sid, messageID=command['id'], text=command['text'])
                await Message.store_part(sid, command['id'], part)
                result = True
            elif op == 'pending':
                part = ToolPart(id=command['part'], sessionID=sid, messageID=command['id'], callID=command['part'], tool='fixture', state=ToolStateRunning(input={}, time={'start': 1}))
                await Message.store_part(sid, command['id'], part)
                result = True
            elif op == 'delete':
                result = await Message.delete(sid, command['id'])
            elif op == 'clear':
                result = await Message.clear(sid)
            elif op == 'archive':
                result = await Message.archive(sid, command['id'])
            elif op == 'claim':
                lease = await SessionExecutionLease.acquire(sid)
                assert lease is not None
                await lease.__aenter__()
                result = True
            elif op == 'read':
                all_messages = await Message.list_with_parts(sid, include_archived=True)
                page, *_ = await Message.list_page_with_parts(sid, limit=200)
                result = {'list': [m.info.id for m in all_messages],
                          'page': [m.info.id for m in page],
                          'document': [m['id'] for m in await Storage.get('message:'+sid)],
                          'messages': [m.model_dump() for m in all_messages]}
            elif op == 'quit':
                break
            else:
                raise ValueError(op)
            print('RESULT '+json.dumps({'result': result}), flush=True)
        except Exception as exc:
            print('RESULT '+json.dumps({'error': repr(exc)}), flush=True)
    if lease:
        await lease.__aexit__(None, None, None)
    await Storage.shutdown()
asyncio.run(run())
'''


@pytest.fixture
async def workers(tmp_path):
    processes = []
    handles = []
    for index in range(2):
        home = tmp_path / str(index)
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FLOCKS_", "FLOCKSPRO_"))}
        env.update(HOME=str(home), USERPROFILE=str(home), FLOCKS_ROOT=str(home / ".flocks"),
                   FLOCKS_DB_DSN=os.environ["FLOCKS_DB_DSN"], PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
        assert "search_path=test_" in env["FLOCKS_DB_DSN"]
        log = await asyncio.to_thread((home / "worker.log").open, "w")
        handles.append(log)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "-c", WORKER, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
        )
        processes.append(process)
    async def call(index, sid, op, **kwargs):
        process = processes[index]
        process.stdin.write((json.dumps(dict(sid=sid, op=op, **kwargs)) + "\n").encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            assert line, f"Worker {index} exited; see {tmp_path / str(index) / 'worker.log'}"
            if line.startswith(b"RESULT "):
                response = json.loads(line[7:])
                assert "error" not in response, response
                return response["result"]
    try:
        yield call
    finally:
        for process in processes:
            if process.returncode is None:
                process.stdin.close()
        for process in processes:
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.terminate()
                await process.wait()
        for handle in handles:
            handle.close()


@pytest.mark.asyncio
async def test_warm_workers_merge_create_delete_archive_and_clear(workers, tmp_path):
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    sid = session.id
    await asyncio.gather(workers(0, sid, "warm"), workers(1, sid, "warm"))
    await asyncio.gather(workers(0, sid, "create", id="msg_a"), workers(1, sid, "create", id="msg_b"))
    for index in range(2):
        state = await workers(index, sid, "read")
        assert state["list"] == state["page"] == state["document"] == ["msg_a", "msg_b"]
    await workers(0, sid, "delete", id="msg_a")
    await workers(1, sid, "create", id="msg_c", at=101)
    await workers(0, sid, "archive", id="msg_b")
    state = await workers(1, sid, "read")
    assert state["list"] == state["document"] == ["msg_b", "msg_c"]
    assert state["messages"][0]["info"]["compacted"] is True
    assert [m.id for m in await Message.list(sid)] == ["msg_c"]
    assert await workers(0, sid, "clear") == 2
    assert (await workers(1, sid, "read"))["list"] == []


@pytest.mark.asyncio
async def test_workers_preserve_other_message_fields_and_part_deltas(workers, tmp_path):
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    sid = session.id
    msg = await Message.create(sid, MessageRole.ASSISTANT, "original")
    await asyncio.gather(workers(0, sid, "warm"), workers(1, sid, "warm"))
    await asyncio.gather(workers(0, sid, "update", id=msg.id, patch={"finish": "stop"}),
                         workers(1, sid, "update", id=msg.id, patch={"cost": 2.5}))
    await asyncio.gather(workers(0, sid, "part", id=msg.id, part="part_a", text="A"),
                         workers(1, sid, "part", id=msg.id, part="part_b", text="B"))
    state = await workers(0, sid, "read")
    assert state["messages"][0]["info"]["finish"] == "stop"
    assert state["messages"][0]["info"]["cost"] == 2.5
    assert {p.get("text") for p in state["messages"][0]["parts"]} == {"original", "A", "B"}
    # A pending local tool delta survives invalidation by a peer's metadata write.
    await workers(0, sid, "pending", id=msg.id, part="part_pending")
    await workers(1, sid, "update", id=msg.id, patch={"cost": 3.5})
    state = await workers(0, sid, "read")
    assert any(p["id"] == "part_pending" for p in state["messages"][0]["parts"])
    state = await workers(1, sid, "read")
    assert any(p["id"] == "part_pending" for p in state["messages"][0]["parts"])


@pytest.mark.asyncio
async def test_foreign_live_runner_is_not_recovered_or_mutated_by_get(workers, tmp_path):
    import httpx
    from fastapi import FastAPI
    from flocks.auth.context import AuthUser
    from flocks.server.routes.session import router
    from flocks.session.orphan_tools import abort_all_orphan_running_parts
    session = await Session.create(project_id="shared", directory=str(tmp_path), owner_user_id="owner", owner_username="owner")
    sid = session.id
    msg = await Message.create(sid, MessageRole.ASSISTANT, "running")
    part = ToolPart(id="part_running", sessionID=sid, messageID=msg.id, callID="call", tool="fixture", state=ToolStateRunning(input={}, time={"start": 1}))
    await Message.store_part(sid, msg.id, part)
    await Message.quiesce_parts(sid, persist=True)
    await workers(0, sid, "claim")
    assert await SessionExecutionLease.acquire(sid) is None
    assert await abort_all_orphan_running_parts() == 0
    app = FastAPI()
    app.include_router(router, prefix="/api/session")
    @app.middleware("http")
    async def owner(request, call_next):
        request.state.auth_user = AuthUser(id="owner", username="owner", role="admin")
        return await call_next(request)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=10) as client:
        response = await client.get(f"/api/session/{sid}/message?page=true&limit=50")
        assert response.status_code == 200
        assert response.json()["items"][0]["info"].get("finish") is None
        status = await client.get(f"/api/session/{sid}/status")
        assert status.status_code == 200 and status.json()["isProcessing"] is True
        statuses = await client.get("/api/session/status")
        assert statuses.status_code == 200 and statuses.json()[sid]["type"] == "busy"
    assert (await Message.get(sid, msg.id)).finish is None
    assert next(p for p in await Message.parts(msg.id, sid) if p.id == part.id).state.status == "running"


@pytest.mark.asyncio
async def test_expired_lease_recovers_but_fences_old_writer(tmp_path):
    from flocks.session import ownership
    from flocks.session.orphan_tools import abort_orphan_running_parts
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    msg = await Message.create(session.id, MessageRole.ASSISTANT, "orphan")
    lease = await SessionExecutionLease.acquire(session.id, duration=0.08)
    assert lease is not None
    await asyncio.sleep(0.1)
    assert await abort_orphan_running_parts(session.id) == 1
    token = ownership._current.set(lease)
    try:
        with pytest.raises(SessionOwnershipLost):
            await Message.update(session.id, msg.id, finish="stop")
    finally:
        ownership._current.reset(token)
    # Failure cannot become a success in the local cache either.
    assert (await Message.get(session.id, msg.id)).finish == "error"


@pytest.mark.asyncio
async def test_renewal_timeout_cancels_loop_before_next_side_effect(monkeypatch):
    from flocks.session.session_loop import SessionLoop
    lease = await SessionExecutionLease.acquire("session_timeout", duration=0.12)
    assert lease is not None
    async def acquire(*args, **kwargs):
        return lease
    async def hang():
        await asyncio.Event().wait()
    effects = []
    async def run(*args, **kwargs):
        effects.append("started")
        await asyncio.sleep(5)
        effects.append("must-not-run")
    monkeypatch.setattr(SessionExecutionLease, "acquire", acquire)
    monkeypatch.setattr(lease, "_renew_once", hang)
    monkeypatch.setattr(SessionLoop, "_run_owned", run)
    start = time.monotonic()
    result = await SessionLoop.run("session_timeout")
    assert time.monotonic() - start < 1
    assert result.action == "error" and effects == ["started"]


@pytest.mark.asyncio
async def test_expired_session_cannot_start_next_tool_even_before_timer_runs(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from flocks.session import ownership
    from flocks.tool.registry import ToolRegistry, ToolContext, ToolResult
    lease = await SessionExecutionLease.acquire("session_tool", duration=30)
    assert lease is not None
    handler = AsyncMock(return_value=ToolResult(success=True, output="ok"))
    tool = SimpleNamespace(info=SimpleNamespace(source="builtin", provider=None, enabled=True), execute=handler)
    monkeypatch.setattr(ToolRegistry, "get", lambda name: tool)
    token = ownership._current.set(lease)
    try:
        assert (await ToolRegistry.execute("fixture", ctx=ToolContext(session_id=lease.session_id, message_id="msg"))).success
        handler.assert_awaited_once()
        # An event-loop pause may outlast the lease before the timer callback runs.
        lease.deadline = time.monotonic() - 1
        with pytest.raises(SessionOwnershipLost):
            await ToolRegistry.execute("fixture", ctx=ToolContext(session_id=lease.session_id, message_id="msg"))
        handler.assert_awaited_once()
    finally:
        ownership._current.reset(token)
        await lease.release()
    token = ownership._current.set(lease)
    lease.deadline = time.monotonic() + 30
    lease.lost = False
    try:
        with pytest.raises(SessionOwnershipLost):
            await ToolRegistry.execute("fixture", ctx=ToolContext(session_id=lease.session_id, message_id="msg"))
        handler.assert_awaited_once()
    finally:
        ownership._current.reset(token)


@pytest.mark.asyncio
async def test_prompt_history_refresh_is_batched_and_recomputes_after_peer_edit(workers, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from flocks.session import message_pages
    from flocks.session.runner import SessionRunner
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    for number in range(12):
        await Message.create(session.id, MessageRole.USER, f"original-{number}", id=f"msg_{number:02d}", part_id=f"part_{number:02d}")
    runner = SessionRunner(session=session)
    revision = AsyncMock(wraps=message_pages.revision)
    monkeypatch.setattr(message_pages, "revision", revision)
    messages = await Message.list(session.id)
    before = await runner._to_chat_messages(messages, [])
    assert revision.await_count <= 3, "Prompt construction must not check PG once per message"
    assert "original-0" in str([m.model_dump() for m in before])
    await workers(0, session.id, "part", id="msg_00", part="part_00", text="peer-replaced-text")
    after = await runner._to_chat_messages(await Message.list(session.id), [])
    rendered = str([m.model_dump() for m in after])
    assert "peer-replaced-text" in rendered and "original-0" not in rendered


@pytest.mark.asyncio
async def test_flush_keeps_pending_parts_for_multiple_messages(tmp_path, monkeypatch):
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 60000)
    messages = [await Message.create(session.id, MessageRole.ASSISTANT, "") for _ in range(2)]
    for number, msg in enumerate(messages):
        part = ToolPart(id=f"part_pending_{number}", sessionID=session.id, messageID=msg.id,
                        callID=f"call_{number}", tool="fixture", state=ToolStateRunning(input={}, time={"start": 1}))
        await Message.store_part(session.id, msg.id, part)
    await Message.quiesce_parts(session.id, persist=True)
    Message.invalidate_cache(session.id)
    state = await Message.list_with_parts(session.id)
    assert all(any(p.type == "tool" and p.state.status == "running" for p in item.parts) for item in state)


@pytest.mark.asyncio
async def test_delete_does_not_cancel_another_messages_pending_flush(tmp_path, monkeypatch):
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 60000)
    first = await Message.create(session.id, MessageRole.ASSISTANT, "first")
    second = await Message.create(session.id, MessageRole.ASSISTANT, "second")
    for msg in (first, second):
        await Message.store_part(session.id, msg.id, ToolPart(
            id="part_" + msg.id, sessionID=session.id, messageID=msg.id, callID=msg.id,
            tool="fixture", state=ToolStateRunning(input={}, time={"start": 1}),
        ))
    monkeypatch.setattr(Message, "_PARTS_PERSIST_DEBOUNCE_MS", 1)
    await Message.delete(session.id, first.id)
    task = Message._parts_flush_tasks.get(session.id)
    assert task is not None
    await asyncio.wait_for(task, timeout=5)
    stored = await Storage.get(f"message_parts:{session.id}:{second.id}")
    assert any(p["type"] == "tool" for p in stored)


@pytest.mark.asyncio
async def test_backdated_messages_keep_cache_and_directory_in_same_order(tmp_path):
    session = await Session.create(project_id="shared", directory=str(tmp_path))
    await Message.create(session.id, MessageRole.USER, "new", id="msg_new", time={"created": 200})
    await Message.create(session.id, MessageRole.USER, "old", id="msg_old", time={"created": 100})
    warm = [m.id for m in await Message.list(session.id)]
    page, *_ = await Message.list_page_with_parts(session.id)
    assert warm == [m.info.id for m in page] == ["msg_old", "msg_new"]
