from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.agent.registry import Agent
from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.config.config import Config, ConfigInfo
from flocks.provider.provider import ChatResponse, ModelCapabilities, ModelInfo, Provider, StreamChunk
from flocks.session.message import Message
from flocks.session.session import Session
from flocks.session.session_loop import SessionLoop
from flocks.storage.storage import Storage
from flocks.tool.registry import ToolRegistry
from flocks_code_security import chat_runtime, conversation as chat
from flocks_code_security.agents import register_agents
from flocks_code_security.projection import code_security_tool_projection
from flocks_code_security.service import AuditCaller, AuditServiceError
from flocks_code_security.tools import register_tools
from test_conversation import audit  # noqa: F401


@pytest.fixture
async def native_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "home"))
    monkeypatch.setenv("FLOCKS_CODE_SECURITY_ROOT", str(tmp_path / "audit"))
    from flocks.session.lifecycle.title import SessionTitle
    from flocks.hooks.pipeline import HookPipeline

    monkeypatch.setattr(SessionTitle, "ensure_title", AsyncMock())
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    monkeypatch.setattr(HookPipeline, "_hooks", [])
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=ConfigInfo()))
    monkeypatch.setattr(Storage, "_initialized", False)
    monkeypatch.setattr(Storage, "_db_path", None)
    await Storage.init(tmp_path / "sessions.db")
    register_tools()
    register_agents()
    reader = Agent._custom_agents[chat_runtime.AGENT_NAME]
    monkeypatch.setattr(reader, "session_directory", str(tmp_path / "runtime"))
    Agent.invalidate_cache()
    token = set_current_auth_user(AuthUser(id="owner", username="owner", role="admin"))
    yield
    reset_current_auth_user(token)
    from flocks.workflow.store import WorkflowStore
    await WorkflowStore.close()
    await Storage.clear()
    Session._id_index.clear()
    Agent.invalidate_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", [False, True])
async def test_native_loop_streams_tools_and_reuses_session(audit, native_storage, monkeypatch, overflow):
    service, sid, caller, _, detail = audit
    detail["artifacts"] = [{"kind": "findings", "state": "sealed"}]
    service.get_artifact.return_value = {"content": "specific evidence"}
    requests = []

    class ProviderStub:
        def is_configured(self):
            return True

        async def chat(self, **kwargs):
            assert overflow, "Audit answers must use the workbench streaming path"
            return ChatResponse(id="summary", model="reader-model", content="Keep audit evidence and source [artifact:findings].", finish_reason="stop", usage={"prompt_tokens": 100, "completion_tokens": 20})

        async def chat_stream(self, **kwargs):
            requests.append(kwargs)
            assert [t["function"]["name"] for t in kwargs["tools"]] == ["code_audit_query"]
            if len(requests) == 1:
                yield StreamChunk(tool_calls=[{
                    "id": "query-1", "type": "function",
                    "function": {"name": "code_audit_query", "arguments": json.dumps({"query": {"view": "artifact", "artifact_kind": "findings"}})},
                }], finish_reason="tool_calls", usage={"prompt_tokens": 60000 if overflow else 100, "completion_tokens": 20})
            else:
                yield StreamChunk(delta="Conclusion [artifact:findings]", finish_reason="stop", usage={"prompt_tokens": 200, "completion_tokens": 20})

    model = ModelInfo(id="reader-model", name="Reader", provider_id="reader-provider", capabilities=ModelCapabilities(context_window=32768, max_tokens=4096))
    monkeypatch.setattr(Provider, "get", lambda *_: ProviderStub())
    monkeypatch.setattr(Provider, "apply_config", AsyncMock())
    monkeypatch.setattr(Provider, "resolve_model", lambda *_: model)
    monkeypatch.setattr(Provider, "resolve_model_info", lambda *_: (32768, 4096, None))
    monkeypatch.setattr(SessionLoop, "_resolve_model", AsyncMock(return_value=("reader-provider", "reader-model")))
    from flocks.session import session_loop
    flush = AsyncMock(side_effect=AssertionError("Isolated audit conversations must not write memories"))
    monkeypatch.setattr(session_loop.SessionCompaction, "_flush_memory_to_daily", flush)
    compact = AsyncMock(wraps=session_loop.run_compaction)
    monkeypatch.setattr(session_loop, "run_compaction", compact)
    run = AsyncMock(wraps=SessionLoop.run)
    monkeypatch.setattr(SessionLoop, "run", run)
    first = await asyncio.wait_for(chat.ask(service, sid, caller, "What did it find?", "first", model="reader-provider/reader-model"), timeout=30)
    assert run.call_args.kwargs["provider_id"] == "reader-provider"
    assert run.call_args.kwargs["model_id"] == "reader-model"
    assert first["answer"].strip() == "Conclusion [artifact:findings]"
    service.get_artifact.assert_awaited_once()
    assert len(requests) == 2
    if overflow:
        assert compact.await_count >= 1
    if not overflow:
        assert any(m.role == "tool" and "specific evidence" in str(m.content) for m in requests[1]["messages"])
    second = await asyncio.wait_for(chat.ask(service, sid, caller, "Explain your conclusion", "second"), timeout=30)
    assert second["sources"] == first["sources"]
    assert first["answer_part_id"]
    assert second["answer_part_id"] != first["answer_part_id"]
    restored = await chat.readiness(service, sid, caller)
    assert restored["processing"] is False
    assert restored["turns"] == [first, second]
    with service.store._connect() as connection:
        rows = connection.execute("SELECT session_id FROM audit_chat_sessions").fetchall()
    assert len(rows) == 1
    session = await Session.get_by_id(rows[0][0])
    from flocks.server.routes.session import _session_to_list_item, _session_to_response
    assert _session_to_list_item(session).codeSecurityScanID == sid
    assert _session_to_response(session).codeSecurityScanID == sid
    assert restored["session_id"] == session.id
    assert session.memory_enabled is False
    assert session.metadata["hideFromSessionManager"] is False
    messages = await Message.list(session.id, include_archived=True)
    texts = [await Message.get_text_content(m) for m in messages if m.role == "user"]
    assert sum("用户问题：" in text for text in texts) == 2
    assert chat_runtime._query_binding.get() is None
    flush.assert_not_awaited()
    new = await chat.create_conversation(service, sid, caller)
    assert new["session_id"] != session.id
    assert new["turns"] == []
    assert len(new["sessions"]) == 2
    assert await Message.list(new["session_id"]) == []
    assert (await chat.readiness(service, sid, caller, session.id))["turns"] == [first, second]
    assert (await chat.readiness(service, sid, caller))["session_id"] == new["session_id"]


@pytest.mark.asyncio
async def test_session_mapping_is_per_caller_and_imports_legacy_history_once(audit, native_storage):
    service, sid, caller, *_ = audit
    turns = [{"question": "old question", "answer": "old answer"}]
    first = await chat_runtime.conversation_session(service, sid, caller, turns)
    second = await chat_runtime.conversation_session(service, sid, caller, turns)
    assert first.id == second.id
    assert len(await Message.list(first.id)) == 2
    # An administrator may access the same audit, but receives a separate session.
    service._require_visible_scan = lambda *_: {"scan_id": sid}
    other = await chat_runtime.conversation_session(service, sid, AuditCaller(subject="admin", source="web", is_admin=True), [])
    assert other.id != first.id
    assert other.owner_user_id == "admin"


@pytest.mark.asyncio
async def test_query_binding_does_not_leak_to_other_sessions():
    callback = AsyncMock(return_value={"id": "audit", "title": "Audit", "content": "evidence"})
    token = chat_runtime._query_binding.set(("bound-session", callback))
    try:
        denied = await chat_runtime.query_tool(SimpleNamespace(session_id="other-session"), {"view": "overview"})
        assert not denied.success
        callback.assert_not_called()
        allowed = await chat_runtime.query_tool(SimpleNamespace(session_id="bound-session"), {"view": "overview"})
        assert allowed.success
        assert callback.await_count == 1
    finally:
        chat_runtime._query_binding.reset(token)
    assert not (await chat_runtime.query_tool(SimpleNamespace(session_id="bound-session"), {"view": "overview"})).success


def test_reader_projection_excludes_all_other_tools():
    register_tools()
    tools = [ToolRegistry.get("code_audit_query").info, ToolRegistry.get("audit_cancel").info, SimpleNamespace(name="bash")]
    assert [t.name for t in code_security_tool_projection(tools, {"agent": chat_runtime.AGENT_NAME})] == ["code_audit_query"]


@pytest.mark.asyncio
async def test_readiness_and_stop_use_owned_session(audit, native_storage, monkeypatch):
    from flocks.session.runner import SessionRunner
    service, sid, caller, *_ = audit
    assert (await chat.readiness(service, sid, caller))["session_id"] is None
    session = await chat_runtime.conversation_session(service, sid, caller, [])
    monkeypatch.setattr(SessionLoop, "abort", abort_mock := Mock())
    monkeypatch.setattr(SessionRunner, "cancel", cancel := Mock())
    assert await chat.stop(service, sid, caller) == {"stopped": True}
    abort_mock.assert_called_once_with(session.id)
    cancel.assert_called_once_with(session.id)
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=SimpleNamespace(owner_user_id="different")))
    with pytest.raises(AuditServiceError):
        await chat.stop(service, sid, caller)
    assert abort_mock.call_count == 1


@pytest.mark.asyncio
async def test_readiness_recovers_stale_pending_requests(audit, native_storage):
    service, sid, caller, *_ = audit
    with service.store._connect() as connection:
        connection.execute("INSERT INTO audit_chat_turns(scan_id,subject,request_id,question,status,created_at) VALUES (?,?, 'lost', 'question', 'pending', '2000-01-01T00:00:00+00:00')", (sid, caller.subject))
    assert (await chat.readiness(service, sid, caller))["processing"] is False


@pytest.mark.asyncio
async def test_migration_preserves_existing_conversation(audit, native_storage):
    from flocks_code_security.store import ScanStore
    service, sid, caller, *_ = audit
    session = await chat_runtime.conversation_session(service, sid, caller, [])
    with service.store._connect() as connection:
        connection.execute("ALTER TABLE audit_chat_turns DROP COLUMN session_id")
        connection.execute("INSERT INTO audit_chat_turns(scan_id,subject,request_id,question,status,answer,created_at) VALUES (?,?, 'legacy', 'question', 'completed', 'answer', '2026-01-01')", (sid, caller.subject))
        connection.execute("PRAGMA user_version=13")
    ScanStore(service.store.database_path).initialize()
    restored = await chat.readiness(service, sid, caller)
    assert restored['session_id'] == session.id
    assert restored['turns'][0]['answer'] == 'answer'
    new = await chat.create_conversation(service, sid, caller)
    assert new['turns'] == []
    assert (await chat.readiness(service, sid, caller, session.id))['turns'] == restored['turns']


@pytest.mark.asyncio
async def test_cannot_select_another_callers_conversation(audit, native_storage):
    service, sid, caller, *_ = audit
    first = await chat.create_conversation(service, sid, caller)
    other = AuditCaller(subject='other', source='web', is_admin=True)
    service._require_visible_scan = lambda *_: {'scan_id': sid}
    for operation in [chat.readiness, chat.stop]:
        with pytest.raises(AuditServiceError):
            await operation(service, sid, other, first['session_id'])


@pytest.mark.asyncio
async def test_new_conversation_keeps_unimported_legacy_history_separate(audit, native_storage):
    service, sid, caller, *_ = audit
    with service.store._connect() as connection:
        connection.execute("INSERT INTO audit_chat_turns(scan_id,subject,request_id,question,status,answer,created_at) VALUES (?,?, 'legacy', 'old question', 'completed', 'old answer', '2026-01-01')", (sid, caller.subject))
    new = await chat.create_conversation(service, sid, caller)
    assert new['turns'] == []
    assert len(new['sessions']) == 2
    previous_id = new['sessions'][1]['session_id']
    assert len(await Message.list(previous_id)) == 2
    assert (await chat.readiness(service, sid, caller, previous_id))['turns'][0]['answer'] == 'old answer'
