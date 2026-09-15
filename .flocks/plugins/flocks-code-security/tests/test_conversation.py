from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks_code_security import conversation as chat
from flocks_code_security.service import AuditCaller, AuditServiceError
from flocks_code_security.store import ScanStore, STORE_SCHEMA_VERSION
from test_service_store import _store


@pytest.fixture
def audit(tmp_path):
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="parent", snapshot_id="snapshot_test", mode="standard", ruleset_digest="rules"
    )
    scan = {"scan_id": scan_id, "cleanup_summary_json": "{}"}
    detail = {"scan": {"lifecycle_status": "completed", "integrity_status": "valid"}, "phase_runs": [], "artifacts": []}
    caller = AuditCaller(subject="owner", source="web")

    def authorize(sid, who):
        if sid != scan_id or who.subject != caller.subject:
            raise AuditServiceError("not_found", "not found", status_code=404)
        return scan

    service = SimpleNamespace(
        store=store,
        read_only=False,
        _require_visible_scan=authorize,
        get_scan=AsyncMock(return_value=detail),
        get_artifact=AsyncMock(),
    )
    return service, scan_id, caller, scan, detail


def test_existing_database_migrates_without_losing_scans(audit):
    service, sid, *_ = audit
    with service.store._connect() as connection:
        connection.execute("DROP TABLE scan_phase_sessions")
        connection.execute("DROP TABLE audit_chat_turns")
        connection.execute("PRAGMA user_version = 8")
    ScanStore(service.store.database_path).initialize()
    with service.store._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == STORE_SCHEMA_VERSION
        assert connection.execute("SELECT scan_id FROM scans").fetchone()[0] == sid
        assert connection.execute("SELECT COUNT(*) FROM audit_chat_turns").fetchone()[0] == 0


def test_visible_transcripts_omit_system_and_reasoning():
    rows = [
        {
            "info": {"id": "a", "role": "assistant", "system": "secret"},
            "parts": [
                {"type": "reasoning", "text": "private"},
                {"type": "text", "text": "visible"},
                {"type": "text", "text": "internal", "synthetic": True},
                {
                    "type": "tool",
                    "tool": "read",
                    "state": {"status": "completed", "input": {"path": "a.py"}, "output": "source"},
                },
            ],
        },
        {"info": {"id": "s", "role": "system"}, "parts": [{"type": "text", "text": "hidden"}]},
    ]
    result = chat.public_messages(rows)
    assert len(result) == 1 and len(result[0]["parts"]) == 2
    assert result[0]["parts"][1]["output"] == "source"
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed", "cancelled", "interrupted"])
async def test_cannot_bypass_completion_gate(audit, monkeypatch, status):
    service, sid, caller, _, detail = audit
    detail["scan"]["lifecycle_status"] = status
    model = AsyncMock()
    monkeypatch.setattr(chat, "model_reply", model)
    with pytest.raises(AuditServiceError, match="全部审计流程"):
        await chat.ask(service, sid, caller, "why", "request")
    model.assert_not_called()


@pytest.mark.asyncio
async def test_session_access_is_authorized_before_read(audit, monkeypatch):
    service, sid, _, *_ = audit
    read = AsyncMock()
    monkeypatch.setattr(chat, "_read_session", read)
    with pytest.raises(AuditServiceError) as error:
        await chat.phase_sessions(service, sid, AuditCaller(subject="other", source="web"))
    assert error.value.status_code == 404
    read.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_repeated_phases_are_not_guessed(audit, monkeypatch):
    service, sid, caller, *_ = audit
    service.store.start_phase_run(sid, "baseline", ordinal=1)
    phase = service.store.start_phase_run(sid, "baseline", ordinal=2)
    unit = service.store.create_work_unit(scan_id=sid, phase="baseline", role="baseline", paths=["."])
    service.store.create_work_attempt(work_unit_id=unit, session_id="session", agent_name="baseline")
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(chat, "_read_session", read)
    result = await chat.phase_sessions(service, sid, caller, phase["phase_run_id"])
    assert result["items"] == [] and result["complete"] is False
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", ["read_only", "cleanup_intermediates", "missing_session", "active_worker"])
async def test_incomplete_context_never_unlocks(audit, monkeypatch, setting):
    service, sid, caller, scan, _ = audit
    if setting == "read_only":
        service.read_only = True
    elif setting == "cleanup_intermediates":
        scan[setting] = True
    else:
        monkeypatch.setattr(
            chat,
            "phase_sessions",
            AsyncMock(
                return_value={
                    "complete": setting != "missing_session",
                    "items": [{"status": "running"}] if setting == "active_worker" else [],
                }
            ),
        )
    result = await chat.readiness(service, sid, caller)
    assert result["ready"] is False


@pytest.mark.asyncio
async def test_answers_include_all_artifacts_persist_and_retry_idempotently(audit, monkeypatch):
    service, sid, caller, _, detail = audit
    detail["artifacts"] = [{"kind": "findings", "state": "sealed"}]
    service.get_artifact.return_value = {"content": "evidence"}
    model = AsyncMock(return_value="Evidence supports this [artifact:findings].")
    monkeypatch.setattr(chat, "model_reply", model)
    result = await chat.ask(service, sid, caller, "why", "request")
    assert result["sources"] == [{"id": "artifact:findings", "title": "findings"}]
    assert "evidence" in model.call_args.args[0][1].content
    assert await chat.ask(service, sid, caller, "why", "request") == result
    assert model.await_count == 1
    with pytest.raises(AuditServiceError) as conflict:
        await chat.ask(service, sid, caller, "different", "request")
    assert conflict.value.code == "request_conflict"


@pytest.mark.asyncio
async def test_failed_request_can_retry_same_id(audit, monkeypatch):
    service, sid, caller, *_ = audit
    model = AsyncMock(side_effect=[RuntimeError("offline"), "No findings [audit]."])
    monkeypatch.setattr(chat, "model_reply", model)
    with pytest.raises(RuntimeError):
        await chat.ask(service, sid, caller, "why", "request")
    assert (await chat.ask(service, sid, caller, "why", "request"))["answer"] == "No findings [audit]."


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["No references", "Fake [session:missing]"])
async def test_unverifiable_answers_are_not_saved(audit, monkeypatch, answer):
    service, sid, caller, *_ = audit
    monkeypatch.setattr(chat, "model_reply", AsyncMock(return_value=answer))
    with pytest.raises(AuditServiceError) as error:
        await chat.ask(service, sid, caller, "why", "request")
    assert error.value.code == "invalid_citation"
    assert chat.history(service, sid, caller) == []


@pytest.mark.asyncio
async def test_session_metadata_must_match_scan(monkeypatch):
    monkeypatch.setattr(
        chat.Session,
        "get_by_id_unfiltered",
        AsyncMock(return_value=SimpleNamespace(metadata={"code_security_scan_id": "other"})),
    )
    read = AsyncMock()
    monkeypatch.setattr(chat.Message, "list_with_parts", read)
    assert await chat._read_session("session", {"scan_id": "mine"}) is None
    read.assert_not_called()


@pytest.mark.asyncio
async def test_auto_configuration_cannot_select_unauthorized_project(audit, monkeypatch):
    from flocks.server.routes import code_security as routes, project
    from fastapi import HTTPException

    service, _, _, *_ = audit
    monkeypatch.setattr(routes, "require_admin", lambda request: SimpleNamespace())
    monkeypatch.setattr(routes, "_service_types", lambda request: (service, AuditCaller, None, AuditServiceError))
    monkeypatch.setattr(
        project,
        "_list_project_summaries",
        AsyncMock(
            return_value=[SimpleNamespace(id="allowed", name="Project", path_status="available", can_write=True)]
        ),
    )
    values = routes.AuditConfigurationValues(workspaceId="forbidden")
    monkeypatch.setattr(
        chat, "model_reply", AsyncMock(return_value=json.dumps({"reply": "Ready", "values": values.model_dump()}))
    )
    payload = routes.AuditConfigurationRequest(message="audit project", values=routes.AuditConfigurationValues())
    with pytest.raises(HTTPException) as error:
        await routes.configure_audit(None, payload)
    assert error.value.status_code == 502
    values.workspaceId = "allowed"
    values.dynamicEnabled = True
    values.copySource = False
    chat.model_reply.return_value = json.dumps({"reply": "Review configuration", "values": values.model_dump()})
    result = await routes.configure_audit(None, payload)
    assert result["values"]["dynamicEnabled"] is False
    assert "dynamicConfirmed" not in result["values"]


def test_binding_preserves_original_run_when_an_attempt_is_seen_again(audit):
    service, sid, *_ = audit
    batch = service.store.create_worker_batch(
        scan_id=sid, phase="threat_modeling", units=[{"role": "threat_modeler", "paths": ["."]}]
    )
    attempt = service.store.create_work_attempt(
        work_unit_id=batch["units"][0]["work_unit_id"], session_id="worker", agent_name="threat-modeler"
    )
    first = service.store.start_phase_run(sid, "threat_modeling", ordinal=1)
    second = service.store.start_phase_run(sid, "threat_modeling", ordinal=2)
    service.store.bind_phase_sessions(sid, first["phase_run_id"], batch["batch_id"])
    service.store.bind_phase_sessions(sid, second["phase_run_id"], batch["batch_id"])
    rows = service.store.phase_session_attempts(sid)
    assert rows[0]["attempt_id"] == attempt["attempt_id"]
    assert rows[0]["phase_run_id"] == first["phase_run_id"]


@pytest.mark.asyncio
async def test_abandoned_pending_request_can_be_retried(audit, monkeypatch):
    service, sid, caller, *_ = audit
    with service.store._connect() as connection:
        connection.execute(
            "INSERT INTO audit_chat_turns(scan_id,subject,request_id,question,status,created_at) VALUES (?,?,?,?,?,?)",
            (sid, caller.subject, "request", "why", "pending", "2020-01-01T00:00:00+00:00"),
        )
    monkeypatch.setattr(chat, "model_reply", AsyncMock(return_value="Answer [audit]"))
    assert (await chat.ask(service, sid, caller, "why", "request"))["answer"] == "Answer [audit]"


@pytest.mark.asyncio
async def test_model_has_no_tools_and_refuses_oversized_context(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_model", AsyncMock(return_value=("provider", "model")))
    provider = SimpleNamespace(
        chat=AsyncMock(return_value=SimpleNamespace(content="answer", finish_reason="stop", tool_calls=[]))
    )
    monkeypatch.setattr(chat.Provider, "get", lambda _: provider)
    monkeypatch.setattr(chat.Provider, "resolve_model_info", lambda *_: (32768, 4096, None))
    await chat.model_reply([chat.ChatMessage(role="user", content="question")])
    assert provider.chat.call_args.kwargs["tools"] is None
    with pytest.raises(AuditServiceError) as error:
        await chat.model_reply([chat.ChatMessage(role="user", content="x" * 40000)])
    assert error.value.status_code == 413
    assert provider.chat.await_count == 1
