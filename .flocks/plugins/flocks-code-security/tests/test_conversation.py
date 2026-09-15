from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks_code_security import conversation as chat
from flocks_code_security import chat_runtime
from flocks_code_security.service import AuditCaller, AuditServiceError
from flocks_code_security.store import ScanStore, STORE_SCHEMA_VERSION
from test_service_store import _store


def answer_mock(*results):
    pending = iter(results)

    async def reply(service, scan_id, caller, question, turns, query, **kwargs):
        await query(chat.AuditQuery(view="overview"))
        result = next(pending)
        if isinstance(result, Exception):
            raise result
        return result

    return AsyncMock(side_effect=reply)


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


@pytest.mark.parametrize("existing_version", [8, 9, 10, 11, 12])
def test_existing_database_migrates_without_losing_scans(audit, existing_version):
    service, sid, *_ = audit
    with service.store._connect() as connection:
        connection.execute("DROP TABLE audit_chat_sessions")
        connection.execute("DROP TABLE scan_phase_sessions")
        connection.execute("DROP TABLE audit_chat_turns")
        connection.execute(f"PRAGMA user_version = {existing_version}")
    ScanStore(service.store.database_path).initialize()
    with service.store._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == STORE_SCHEMA_VERSION
        assert connection.execute("SELECT scan_id FROM scans").fetchone()[0] == sid
        assert connection.execute("SELECT COUNT(*) FROM audit_chat_sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_chat_turns").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM scan_phase_sessions").fetchone()[0] == 0


def test_visible_transcripts_preserve_workbench_parts_without_system_fields():
    rows = [
        {
            "info": {"id": "a", "role": "assistant", "system": "secret"},
            "parts": [
                {"id": "think-1", "type": "reasoning", "text": "Inspecting entry points", "time": {"start": 100, "end": 200}, "metadata": {"private": "secret"}},
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
    assert len(result) == 1 and len(result[0]["parts"]) == 3
    assert result[0]["parts"][2]["output"] == "source"
    assert result[0]["parts"][0]["text"] == "Inspecting entry points"
    assert result[0]["parts"][0]["time"] == {"start": 100, "end": 200}
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "failed", "cancelled", "interrupted"])
async def test_cannot_bypass_completion_gate(audit, monkeypatch, status):
    service, sid, caller, _, detail = audit
    detail["scan"]["lifecycle_status"] = status
    model = AsyncMock()
    monkeypatch.setattr(chat_runtime, "answer", model)
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
@pytest.mark.parametrize("setting", ["read_only", "invalid_artifacts", "active_worker"])
async def test_unready_results_never_unlock(audit, monkeypatch, setting):
    service, sid, caller, _, detail = audit
    if setting == "read_only":
        service.read_only = True
    elif setting == "invalid_artifacts":
        detail["scan"]["integrity_status"] = "invalid"
    else:
        monkeypatch.setattr(service.store, "phase_session_attempts", lambda _: [{"status": "running"}])
    assert (await chat.readiness(service, sid, caller))["ready"] is False


@pytest.mark.asyncio
async def test_readiness_allows_cleaned_sessions_without_reading_evidence(audit, monkeypatch):
    service, sid, caller, scan, _ = audit
    scan["cleanup_intermediates"] = True
    scan["cleanup_summary_json"] = '{"status":"completed"}'
    read = AsyncMock(side_effect=AssertionError("transcript must not be read"))
    monkeypatch.setattr(chat, "_read_session", read)
    assert (await chat.readiness(service, sid, caller))["ready"] is True
    monkeypatch.setattr(chat_runtime, "answer", answer_mock("Summary [audit]"))
    assert (await chat.ask(service, sid, caller, "summarize", "request"))["answer"] == "Summary [audit]"
    read.assert_not_called()
    service.get_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_answers_query_artifacts_on_demand_and_retry_idempotently(audit, monkeypatch):
    service, sid, caller, _, detail = audit
    detail["artifacts"] = [{"kind": "findings", "state": "sealed"}]
    service.get_artifact.return_value = {"content": "evidence"}

    async def reply(service, scan_id, caller, question, turns, query, **kwargs):
        service.get_artifact.assert_not_called()
        page = await query(chat.AuditQuery(view="artifact", artifact_kind="findings"))
        assert "evidence" in page["content"]
        return "Evidence supports this [artifact:findings]."

    model = AsyncMock(side_effect=reply)
    monkeypatch.setattr(chat_runtime, "answer", model)
    result = await chat.ask(service, sid, caller, "why", "request")
    assert result["sources"] == [{"id": "artifact:findings", "title": "findings"}]
    assert await chat.ask(service, sid, caller, "why", "request") == result
    assert model.await_count == 1
    with pytest.raises(AuditServiceError) as conflict:
        await chat.ask(service, sid, caller, "different", "request")
    assert conflict.value.code == "request_conflict"


@pytest.mark.asyncio
async def test_failed_request_can_retry_same_id(audit, monkeypatch):
    service, sid, caller, *_ = audit
    model = answer_mock(RuntimeError("offline"), "No findings [audit].")
    monkeypatch.setattr(chat_runtime, "answer", model)
    with pytest.raises(RuntimeError):
        await chat.ask(service, sid, caller, "why", "request")
    assert (await chat.ask(service, sid, caller, "why", "request"))["answer"] == "No findings [audit]."


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["No references", "Fake [session:missing]"])
async def test_unverifiable_answers_are_not_saved(audit, monkeypatch, answer):
    service, sid, caller, *_ = audit
    monkeypatch.setattr(chat_runtime, "answer", AsyncMock(return_value=answer))
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
    monkeypatch.setattr(chat_runtime, "answer", answer_mock("Answer [audit]"))
    assert (await chat.ask(service, sid, caller, "why", "request"))["answer"] == "Answer [audit]"


@pytest.mark.asyncio
async def test_configuration_helper_has_no_tools_or_tokenizer(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_model", AsyncMock(return_value=("provider", "model")))
    provider = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(content="answer", finish_reason="stop", tool_calls=[])))
    monkeypatch.setattr(chat.Provider, "get", lambda _: provider)
    assert await chat.model_reply([chat.ChatMessage(role="user", content="question")]) == "answer"
    assert provider.chat.call_args.kwargs["tools"] is None


@pytest.mark.asyncio
async def test_phase_query_returns_only_its_sessions_without_reading_messages(audit, monkeypatch):
    service, sid, caller, _, detail = audit
    phases = []
    for ordinal in (1, 2):
        unit = service.store.create_work_unit(scan_id=sid, phase="verification", role="verifier", paths=["."])
        attempt = service.store.create_work_attempt(
            work_unit_id=unit, session_id=f"worker-{ordinal}", agent_name="verifier"
        )
        phase = service.store.start_phase_run(sid, "verification", ordinal=ordinal)
        with service.store._connect() as connection:
            connection.execute(
                "INSERT INTO scan_phase_sessions(attempt_id, phase_run_id) VALUES (?, ?)",
                (attempt["attempt_id"], phase["phase_run_id"]),
            )
        phases.append(phase)
    detail["phase_runs"] = phases
    detail["artifacts"] = [{"kind": "verification_index"}, {"kind": "threat_model"}]
    read = AsyncMock(side_effect=AssertionError("listing must not read transcripts"))
    monkeypatch.setattr(chat, "_read_session", read)
    page = await chat.code_audit_query(
        service, sid, caller, chat.AuditQuery(view="phase", phase_run_id=phases[1]["phase_run_id"])
    )
    content = json.loads(page["content"])
    assert [s["session_id"] for s in content["sessions"]["items"]] == ["worker-2"]
    assert "messages" not in content["sessions"]["items"][0]
    assert content["artifacts"] == [{"kind": "verification_index"}]
    assert page["phase_run_id"] == phases[1]["phase_run_id"]
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["overview", "phase", "artifact", "session"])
async def test_query_checks_audit_access_before_reading(audit, monkeypatch, view):
    service, sid, _, *_ = audit
    read = AsyncMock()
    monkeypatch.setattr(chat, "_read_session", read)
    with pytest.raises(AuditServiceError) as error:
        await chat.code_audit_query(service, sid, AuditCaller(subject="other", source="web"), chat.AuditQuery(view=view))
    assert error.value.status_code == 404
    read.assert_not_called()
    service.get_artifact.assert_not_called()
    service.get_scan.assert_not_called()


@pytest.mark.asyncio
async def test_arbitrary_session_id_cannot_read_other_sessions(audit, monkeypatch):
    service, sid, caller, scan, _ = audit
    scan["parent_session_id"] = "unowned-parent"
    read = AsyncMock()
    monkeypatch.setattr(chat, "_read_session", read)
    for session_id in ("another-audit", "unowned-parent"):
        with pytest.raises(AuditServiceError) as error:
            await chat.code_audit_query(service, sid, caller, chat.AuditQuery(view="session", session_id=session_id))
        assert error.value.status_code == 404
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["artifact", "session"])
async def test_large_evidence_is_paged_without_losing_content(audit, monkeypatch, view):
    service, sid, caller, scan, _ = audit
    text = "中文工具输出\\n" * 12000
    if view == "artifact":
        service.get_artifact.return_value = {"state": "sealed", "content": text}
        args = {"view": view, "artifact_kind": "findings"}
        expected = {"state": "sealed", "content": text}
    else:
        scan.update(task_owner_token="owned", parent_session_id="coordinator")
        expected = [{"id": "message-1", "parts": [{"type": "tool", "output": text}]}]
        monkeypatch.setattr(chat, "_read_session", AsyncMock(return_value=expected))
        args = {"view": view, "session_id": "coordinator"}
    chunks = []
    offset = 0
    while True:
        page = await chat.code_audit_query(service, sid, caller, chat.AuditQuery(**args, offset=offset))
        assert len(page["content"]) <= chat.QUERY_PAGE_CHARS
        assert page["offset"] == offset
        chunks.append(page["content"])
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert len(chunks) > 1
    assert json.loads("".join(chunks)) == expected


@pytest.mark.asyncio
async def test_missing_session_does_not_prevent_artifact_query(audit, monkeypatch):
    service, sid, caller, scan, _ = audit
    scan.update(task_owner_token="owned", parent_session_id="coordinator")
    monkeypatch.setattr(chat, "_read_session", AsyncMock(return_value=None))
    with pytest.raises(AuditServiceError) as error:
        await chat.code_audit_query(service, sid, caller, chat.AuditQuery(view="session", session_id="coordinator"))
    assert error.value.code == "session_unavailable"
    service.get_artifact.return_value = {"content": "retained findings"}
    page = await chat.code_audit_query(service, sid, caller, chat.AuditQuery(view="artifact", artifact_kind="findings"))
    assert "retained findings" in page["content"]


@pytest.mark.asyncio
async def test_missing_citation_keeps_answer_with_actual_query_provenance(audit, monkeypatch):
    service, sid, caller, *_ = audit
    monkeypatch.setattr(chat_runtime, "answer", answer_mock("There are seven findings."))
    turn = await chat.ask(service, sid, caller, "How many findings?", "request")
    assert turn["answer"] == "There are seven findings."
    assert [source["id"] for source in turn["sources"]] == ["audit"]


@pytest.mark.asyncio
async def test_query_provenance_does_not_allow_fabricated_citations(audit, monkeypatch):
    service, sid, caller, *_ = audit
    monkeypatch.setattr(chat_runtime, "answer", answer_mock("Seven findings [session:invented]"))
    with pytest.raises(AuditServiceError) as error:
        await chat.ask(service, sid, caller, "How many findings?", "request")
    assert error.value.code == "invalid_citation"
    assert chat.history(service, sid, caller) == []
