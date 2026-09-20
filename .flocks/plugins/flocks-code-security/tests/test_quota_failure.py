"""Quota failures stop recovery without overriding completed business facts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.session.lifecycle.retry import MODEL_QUOTA_EXHAUSTED, ModelQuotaExhaustedError
from flocks_code_security import tools
from flocks_code_security.cli import _require_success
from flocks_code_security.service import AuditService


@pytest.fixture
def workers(monkeypatch):
    units = [{
        "work_unit_id": key, "attempt_id": key, "background_task_id": key,
        "status": "running", "role": "baseline", "resume_count": 0,
        "started_at": None, "finished_at": None,
    } for key in ("sibling", "quota")]
    batch = {"batch_id": "batch", "status": "running", "units": units}
    complete = set()
    tasks = {
        "sibling": SimpleNamespace(status="error", error="429 rate limit", execution_metadata={}),
        "quota": SimpleNamespace(status="error", error="provider failed", execution_metadata={"error_code": MODEL_QUOTA_EXHAUSTED}),
    }

    def finish(attempt_id, *, status, failure_class=None, work_unit_status=None):
        unit = next(unit for unit in units if unit["attempt_id"] == attempt_id)
        unit["status"] = work_unit_status or status
        unit["attempt_failure_class"] = failure_class

    store = SimpleNamespace(
        get_worker_batch=Mock(return_value=batch),
        work_unit_has_required_facts=Mock(side_effect=lambda key, **kw: key in complete),
        work_attempt_has_analysis_progress=Mock(return_value=True),
        record_worker_failure=Mock(), finish_work_attempt=Mock(side_effect=finish),
        set_work_unit_timing=Mock(),
        update_worker_batch_status=Mock(side_effect=lambda key, status: batch.update(status=status)),
    )
    resume, fresh = AsyncMock(), AsyncMock()
    monkeypatch.setattr(tools, "get_runtime", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(tools, "_background_manager", lambda: SimpleNamespace(get_task=tasks.get))
    monkeypatch.setattr(tools, "_resume_worker_attempt", resume)
    monkeypatch.setattr(tools, "_start_fresh_worker_attempt", fresh)
    return SimpleNamespace(units=units, batch=batch, tasks=tasks, complete=complete,
                           store=store, resume=resume, fresh=fresh)


@pytest.mark.asyncio
@pytest.mark.parametrize("facts_complete", [False, True])
@pytest.mark.parametrize("structured", [False, True])
async def test_quota_precedes_recovery_but_complete_facts_win(workers, facts_complete, structured):
    if not structured:
        workers.tasks["quota"].error = "429 insufficient_quota"
        workers.tasks["quota"].execution_metadata = {}
    if facts_complete:
        workers.complete.update({"sibling", "quota"})
        result = await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
        assert result["status"] == "completed"
        assert all(unit["status"] == "completed" for unit in workers.units)
        workers.store.record_worker_failure.assert_not_called()
    else:
        with pytest.raises(ModelQuotaExhaustedError) as caught:
            await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
        assert workers.units[1]["status"] == "failed"
        assert workers.units[1]["attempt_failure_class"] == MODEL_QUOTA_EXHAUSTED
        result = tools._error(caught.value, title="Worker failed")
        with pytest.raises(ModelQuotaExhaustedError) as propagated:
            _require_success(result)
        assert AuditService._failure_code(propagated.value) == MODEL_QUOTA_EXHAUSTED
    workers.resume.assert_not_awaited()
    workers.fresh.assert_not_awaited()
    workers.store.work_attempt_has_analysis_progress.assert_not_called()


@pytest.mark.asyncio
async def test_quota_arriving_after_precheck_does_not_resume_coverage(workers):
    workers.units[:] = [workers.units[1]]
    task = workers.tasks["quota"]
    task.status, task.error, task.execution_metadata = "running", None, {}
    task.started_at = 1000

    def finish_during_timing(*args, **kwargs):
        task.status, task.error = "error", "429 insufficient_quota"
        task.execution_metadata = {"error_code": MODEL_QUOTA_EXHAUSTED}

    workers.store.set_work_unit_timing.side_effect = finish_during_timing
    with pytest.raises(ModelQuotaExhaustedError):
        await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
    assert workers.batch["status"] == "failed"
    workers.resume.assert_not_awaited()
    workers.fresh.assert_not_awaited()
    workers.store.work_attempt_has_analysis_progress.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("quota_first", [False, True])
@pytest.mark.parametrize("task_missing", [False, True])
async def test_failed_quota_still_reconciles_completed_sibling(workers, quota_first, task_missing):
    workers.complete.add("sibling")
    if task_missing:
        del workers.tasks["sibling"]
    else:
        workers.tasks["sibling"].status = "completed"
    if quota_first:
        workers.units.reverse()
    with pytest.raises(ModelQuotaExhaustedError):
        await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
    assert {unit["work_unit_id"]: unit["status"] for unit in workers.units} == {
        "sibling": "completed", "quota": "failed",
    }
    assert workers.batch["status"] == "partial"
    workers.resume.assert_not_awaited()
    workers.fresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "completed", "cancelled"])
async def test_persisted_quota_respects_terminal_outcome(workers, status):
    workers.units[:] = [workers.units[1]]
    workers.units[0].update(status=status, attempt_failure_class=MODEL_QUOTA_EXHAUSTED)
    workers.tasks.clear()
    if status == "failed":
        with pytest.raises(ModelQuotaExhaustedError):
            await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
    else:
        result = await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
        assert result["status"] == status
    workers.resume.assert_not_awaited()
    workers.fresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("quota_first", [False, True])
@pytest.mark.parametrize("recovery", ["coverage", "transient", "fresh", "missing"])
async def test_sibling_quota_arriving_during_recovery_reads(workers, quota_first, recovery):
    task = workers.tasks["quota"]
    task.status, task.error, task.execution_metadata = "running", None, {}
    if quota_first:
        workers.units.reverse()
    if recovery == "missing":
        del workers.tasks["sibling"]
    elif recovery == "fresh":
        workers.tasks["sibling"].error = "worker failed"

    def analysis_progress(attempt_id):
        assert attempt_id == "sibling"
        task.status, task.error = "error", "429 insufficient_quota"
        task.execution_metadata = {"error_code": MODEL_QUOTA_EXHAUSTED}
        return recovery in {"coverage", "missing"}

    workers.store.work_attempt_has_analysis_progress.side_effect = analysis_progress
    with pytest.raises(ModelQuotaExhaustedError):
        await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
    workers.resume.assert_not_awaited()
    workers.fresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_write", [
    "record_worker_failure", "finish_work_attempt", "update_worker_batch_status",
])
async def test_quota_survives_failed_state_writes(workers, failed_write):
    import sqlite3

    workers.units[:] = [workers.units[1]]
    locked = sqlite3.OperationalError("database is locked")
    getattr(workers.store, failed_write).side_effect = locked
    with pytest.raises(ModelQuotaExhaustedError) as caught:
        await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())
    assert caught.value.__cause__ is locked
    with pytest.raises(ModelQuotaExhaustedError) as propagated:
        _require_success(tools._error(caught.value, title="Worker failed"))
    assert AuditService._failure_code(propagated.value) == MODEL_QUOTA_EXHAUSTED


@pytest.mark.asyncio
async def test_database_error_without_quota_keeps_original_cause(workers):
    import sqlite3

    workers.tasks["quota"].status = "running"
    workers.store.work_unit_has_required_facts.side_effect = sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        await tools._refresh_worker_batch("batch", ctx=SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("quota_during", ["prepare", "persist_runtime"])
async def test_resume_checks_quota_before_launch_and_start_gate(monkeypatch, quota_during):
    from flocks.session.session import Session

    sibling = SimpleNamespace(status="running", error=None, execution_metadata={})
    batch = {"units": [{"status": "running", "background_task_id": "sibling"}]}
    manager = SimpleNamespace(
        get_task=lambda _: sibling,
        run_existing_session=AsyncMock(return_value=SimpleNamespace(id="resumed")),
        cancel=Mock(),
    )

    def exhaust_quota(*args, **kwargs):
        sibling.status = "error"
        sibling.execution_metadata = {"error_code": MODEL_QUOTA_EXHAUSTED}
        return {"resume_count": 1}

    store = SimpleNamespace(
        require_binding=Mock(return_value={}), verify_execution_capsule=Mock(return_value={}),
        prepare_work_attempt_resume=Mock(return_value={"resume_count": 1}),
        set_work_attempt_runtime=Mock(), append_scan_event=Mock(),
    )
    method = store.prepare_work_attempt_resume if quota_during == "prepare" else store.set_work_attempt_runtime
    method.side_effect = exhaust_quota
    monkeypatch.setattr(tools, "get_runtime", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(tools, "_background_manager", lambda: manager)
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=SimpleNamespace(agent="worker")))
    unit = {"attempt_id": "attempt", "session_id": "session", "role": "baseline", "agent_name": "worker", "phase": "baseline"}
    with pytest.raises(ModelQuotaExhaustedError):
        await tools._resume_worker_attempt(SimpleNamespace(session_id="parent", extra={}), unit, batch=batch)
    if quota_during == "prepare":
        manager.run_existing_session.assert_not_awaited()
    else:
        assert not manager.run_existing_session.await_args.kwargs["start_gate"].is_set()
        manager.cancel.assert_called_once_with(task_id="resumed")
    store.append_scan_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_locked", [False, True])
async def test_fresh_launch_checks_sibling_quota_after_preparation(monkeypatch, cleanup_locked):
    import sqlite3
    from flocks.session.message import Message
    from flocks.session.session import Session
    from flocks_code_security import builtin_tools

    sibling = SimpleNamespace(status="running", error=None, execution_metadata={})
    batch = {"units": [{"status": "running", "background_task_id": "sibling"}]}
    manager = SimpleNamespace(get_task=lambda _: sibling, run_existing_session=AsyncMock())
    store = SimpleNamespace(
        get_knowledge_base_metadata=Mock(return_value=None), reserve_worker_capacity=Mock(),
        create_work_attempt=Mock(return_value={"attempt_id": "attempt", "capsule": {}}),
        require_binding=Mock(return_value={}), verify_execution_capsule=Mock(), finish_work_attempt=Mock(),
    )
    if cleanup_locked:
        store.finish_work_attempt.side_effect = sqlite3.OperationalError("database is locked")

    async def message_created(**kwargs):
        sibling.status = "error"
        sibling.execution_metadata = {"error_code": MODEL_QUOTA_EXHAUSTED}

    parent = SimpleNamespace(id="parent", project_id="project", directory=".", provider=None, model=None)
    monkeypatch.setattr(tools, "get_runtime", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(tools, "_background_manager", lambda: manager)
    monkeypatch.setattr(tools, "get_session_callable_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builtin_tools, "source_workspace_prompt", lambda *args: "")
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", AsyncMock(return_value=SimpleNamespace(id="child")))
    monkeypatch.setattr(Message, "create", message_created)
    with pytest.raises(ModelQuotaExhaustedError):
        await tools._launch_worker(
            SimpleNamespace(session_id="parent", extra={}), "scan", "snapshot", "threat_modeling",
            {"role": "threat_modeler", "work_unit_id": "unit", "paths": ["."]},
            candidate=None, recovery_batch=batch,
        )
    manager.run_existing_session.assert_not_awaited()
    assert store.finish_work_attempt.call_args.kwargs["failure_class"] == MODEL_QUOTA_EXHAUSTED


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", ["resume", "fresh"])
async def test_quota_from_new_task_blocks_next_recovery_in_same_refresh(monkeypatch, recovery):
    from copy import deepcopy
    from flocks.session.message import Message
    from flocks.session.session import Session
    from flocks_code_security import builtin_tools

    # Store reads return detached snapshots, as real SQLite reads do.
    persisted = {"batch_id": "batch", "scan_id": "scan", "phase": "threat_modeling", "status": "running", "units": [{
        "work_unit_id": key, "attempt_id": key, "background_task_id": key,
        "session_id": key, "scan_id": "scan", "status": "running", "role": "threat_modeler",
        "phase": "threat_modeling", "agent_name": "code-security-threat-modeler",
        "resume_count": 0, "attempt_ordinal": 1, "paths": ["."],
        "started_at": None, "finished_at": None,
    } for key in ("first", "second")]}
    tasks = {key: SimpleNamespace(
        status="error", error="429 rate limit" if recovery == "resume" else "worker failed",
        execution_metadata={},
    ) for key in ("first", "second")}

    started = []

    async def run_session(**kwargs):
        task = SimpleNamespace(id=f"new-task-{len(started)}", status="running", error=None, execution_metadata={})
        tasks[task.id] = task
        started.append(task)
        return task

    def bind_runtime(attempt_id, *, background_task_id, **kwargs):
        persisted["units"][0]["background_task_id"] = background_task_id

    def record_started(scan_id, event, *args, **kwargs):
        if event in {"worker.resume_started", "worker.attempt_started"}:
            started[-1].status = "error"
            started[-1].execution_metadata = {"error_code": MODEL_QUOTA_EXHAUSTED}

    store = SimpleNamespace(
        get_worker_batch=Mock(side_effect=lambda _: deepcopy(persisted)),
        work_unit_has_required_facts=Mock(return_value=False), record_worker_failure=Mock(),
        require_binding=Mock(return_value={}), verify_execution_capsule=Mock(return_value={}),
        prepare_work_attempt_resume=Mock(return_value={"resume_count": 1}),
        set_work_attempt_runtime=Mock(side_effect=bind_runtime),
        append_scan_event=Mock(side_effect=record_started), finish_work_attempt=Mock(),
        get_scan=Mock(return_value={"snapshot_id": "snapshot"}),
        get_knowledge_base_metadata=Mock(return_value=None), reserve_worker_capacity=Mock(),
        create_work_attempt=Mock(return_value={"attempt_id": "new-attempt", "ordinal": 2, "capsule": {}, "capsule_digest": "digest"}),
    )
    manager = SimpleNamespace(get_task=tasks.get, run_existing_session=AsyncMock(side_effect=run_session), cancel=Mock())
    parent = SimpleNamespace(id="parent", project_id="project", directory=".", provider=None, model=None)
    monkeypatch.setattr(tools, "get_runtime", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(tools, "_background_manager", lambda: manager)
    monkeypatch.setattr(tools, "get_session_callable_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(builtin_tools, "source_workspace_prompt", lambda *args: "")
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", AsyncMock(return_value=SimpleNamespace(id="child")))
    monkeypatch.setattr(Message, "create", AsyncMock())
    with pytest.raises(ModelQuotaExhaustedError):
        await tools._refresh_worker_batch("batch", ctx=SimpleNamespace(session_id="parent", extra={}))
    manager.run_existing_session.assert_awaited_once()
