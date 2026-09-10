from __future__ import annotations

import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from flocks.security import batch
from flocks.server.routes import code_security as routes
from flocks_code_security.models import SnapshotRef
from flocks_code_security.store import ScanStore
from flocks_code_security import service


@pytest.fixture
def isolated_batch(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "run"
    batch_id = "batch_" + "a" * 32
    monkeypatch.setattr(batch, "registry_root", lambda: tmp_path / "registry")
    batch.atomic_json(root / "batch.json", {"batch_id": batch_id, "tasks": {"1": {}, "2": {}}})
    batch.atomic_json(batch.registry_root() / f"{batch_id}.json", {"run_dir": str(root)})
    stores = {}
    for task in ("1", "2"):
        store = ScanStore(root / f"tasks/{task}/data/code-security/data/code-security.db")
        store.initialize()
        store.save_snapshot(
            SnapshotRef(
                snapshot_id="snapshot_test",
                repository_identity=f"repo-{task}",
                source_revision="abc",
                tree_digest="a" * 64,
                scope_digest="b" * 64,
                file_count=0,
                total_bytes=0,
                created_at="2026-08-21T00:00:00+00:00",
                root_path=str(tmp_path),
                display_name=f"task-{task}",
            ),
            [],
        )
        scan = store.create_scan(
            parent_session_id="parent", snapshot_id="snapshot_test", mode="standard", ruleset_digest="rules"
        )
        store.append_scan_event(scan, "test.marker", f"task-{task}", {})
        batch.atomic_json(root / f"tasks/{task}/current.json", {"attempt": task, "scan_id": scan})
        stores[task] = (store, scan)
    monkeypatch.setattr(routes, "require_admin", lambda _: SimpleNamespace(id="admin", role="admin"))
    monkeypatch.setattr(routes, "require_user", lambda _: SimpleNamespace(id="admin", role="admin"))
    monkeypatch.setattr(service, "get_audit_service", lambda: pytest.fail("Must not access shared runtime"))
    return root, batch_id, stores


def request(batch_id, task_id):
    return SimpleNamespace(path_params={"batch_id": batch_id, "task_id": task_id})


@pytest.mark.asyncio
async def test_parallel_details_events_and_artifacts_use_only_selected_task(isolated_batch):
    root, batch_id, stores = isolated_batch
    environment = dict(os.environ)
    first, second = await asyncio.gather(
        *[routes.get_scan(request(batch_id, task), stores[task][1]) for task in ("1", "2")]
    )
    assert first["target"]["display_name"] == "task-1"
    assert second["target"]["display_name"] == "task-2"
    assert "task_id=1" in first["workspaceUrl"]
    assert "task_id=2" in second["workspaceUrl"]
    for task in ("1", "2"):
        context = request(batch_id, task)
        scan_id = stores[task][1]
        events = await routes.get_events(context, scan_id, after_seq=0, before_seq=None, limit=200, recent=False)
        assert any(event["title"] == f"task-{task}" for event in events["items"])
        artifact = await routes.get_artifact(context, scan_id, "snapshot_summary")
        assert artifact["content"]["display_name"] == f"task-{task}"
        other = "2" if task == "1" else "1"
        with pytest.raises(HTTPException) as error:
            await routes.get_scan(context, stores[other][1])
        assert error.value.status_code == 404
        read_service = routes._service_types(context)[0]
        with read_service.store._connect() as connection:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("DELETE FROM scans")
    assert dict(os.environ) == environment


@pytest.mark.asyncio
async def test_cancel_writes_only_selected_attempt_marker(isolated_batch):
    root, batch_id, stores = isolated_batch
    task_dir = batch.resolve_task(root, "1")
    with batch.file_lock(task_dir / "task.lock"):
        detail = await routes.cancel_scan(request(batch_id, "1"), stores["1"][1])
    assert detail["scan"]["can_cancel"] is False
    assert batch.read_json(task_dir / "cancel.json") == {"attempt": "1"}
    assert not (root / "tasks/2/cancel.json").exists()


def test_scoped_artifact_links_and_access_control(isolated_batch, monkeypatch):
    _, batch_id, _ = isolated_batch
    link = {"download_url": "/api/code-security/v1/scans/scan_1/downloads/report.md"}
    assert f"/batches/{batch_id}/tasks/1/" in routes._scope_links(link, request(batch_id, "1"))["download_url"]

    def deny(_):
        raise HTTPException(403)

    monkeypatch.setattr(routes, "require_admin", deny)
    with pytest.raises(HTTPException) as error:
        routes._service_types(request(batch_id, "1"))
    assert error.value.status_code == 403


def test_worker_budget_is_configurable_without_changing_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_CODE_SECURITY_WORKERS", "1")
    assert ScanStore(tmp_path / "one.db").worker_limit == 1
    monkeypatch.delenv("FLOCKS_CODE_SECURITY_WORKERS")
    assert ScanStore(tmp_path / "default.db").worker_limit == 10


def test_batch_cleanup_preserves_events_for_task_ui(isolated_batch):
    _, _, stores = isolated_batch
    store, scan = stores["1"]
    store.retain_ui_history = True
    with store._connect() as connection:
        connection.execute("UPDATE scans SET cleanup_intermediates = 1, status = 'failed' WHERE scan_id = ?", (scan,))
    store.prune_scan_execution_history(scan)
    events = store.list_scan_events(scan, limit=200)["items"]
    assert any(item["title"] == "task-1" for item in events)


@pytest.mark.asyncio
async def test_completed_evidence_uses_verified_sealed_excerpt_after_source_cleanup(tmp_path, monkeypatch):
    service_instance = object.__new__(service.AuditService)
    scan = {"status": "completed", "snapshot_id": "snapshot"}
    evidence = {"relative_path": "main.c", "start_line": 2, "end_line": 3}
    service_instance.store = SimpleNamespace(
        get_evidence_record=lambda *_: evidence,
        get_snapshot=lambda _: SimpleNamespace(root_path=str(tmp_path / "removed")),
    )
    monkeypatch.setattr(service_instance, "_require_visible_scan", lambda *_: scan)
    monkeypatch.setattr(service_instance, "_artifact_file", lambda *_: tmp_path / "findings.json")
    verified = []

    def read_verified(*_):
        verified.append(True)
        return b'{"findings":[{"codeEvidence":[{"path":"main.c","startLine":2,"endLine":3,"code":"source excerpt"}]}]}'

    monkeypatch.setattr(service_instance, "_read_verified_artifact", read_verified)
    result = await service_instance.get_evidence("scan", "evidence", SimpleNamespace())
    assert verified == [True]
    assert result["excerpt"] == "source excerpt"


@pytest.mark.asyncio
async def test_crash_cleanup_reconciles_real_database_and_preserves_task_ui(isolated_batch):
    from flocks.security.batch import cleanup_child_work

    root, batch_id, stores = isolated_batch
    task = root / "tasks/1"
    store, scan_id = stores["1"]
    with store._connect() as connection:
        connection.execute("UPDATE scans SET cleanup_intermediates = 1 WHERE scan_id = ?", (scan_id,))
        connection.execute("UPDATE snapshots SET copy_source = 0")
    scratch = task / "data/code-security/runtime/projection"
    scratch.mkdir(parents=True)
    (scratch / "source.c").write_text("temporary")
    result = {"attempt": "1", "status": "interrupted"}
    with batch.file_lock(task / "task.lock"):
        await cleanup_child_work(task, result)
    assert result["cleanup_status"] == "completed", result
    assert not scratch.exists()
    assert not (task / "data/flocks").exists()
    assert store.get_scan(scan_id)["status"] == "interrupted"
    assert stores["2"][0].get_scan(stores["2"][1])["status"] != "interrupted"
    detail = await routes.get_scan(request(batch_id, "1"), scan_id)
    assert detail["target"]["display_name"] == "task-1"
    assert any(event["title"] == "task-1" for event in store.list_scan_events(scan_id)["items"])
    with store._connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_invalid_final_bundle_retains_source_and_execution_data(isolated_batch):
    import tempfile
    from pathlib import Path
    from flocks.security.batch_worker import cleanup_work

    root, _, stores = isolated_batch
    task = root / "tasks/1"
    store, scan_id = stores["1"]
    with store._connect() as connection:
        connection.execute(
            "UPDATE scans SET cleanup_intermediates = 1, status = 'completed' WHERE scan_id = ?", (scan_id,)
        )
    work = Path(tempfile.mkdtemp(prefix="flocks-batch-"))
    (work / ".batch-owner").write_text(str(task))
    (work / "source.c").write_text("recoverable source")
    current = batch.read_json(task / "current.json")
    batch.atomic_json(task / "current.json", {**current, "work_dir": str(work)})
    scratch = task / "data/flocks"
    scratch.mkdir()
    (scratch / "session").touch()
    result = {"attempt": "1", "status": "completed"}
    try:
        with batch.file_lock(task / "task.lock"):
            await batch.cleanup_child_work(task, result)
        assert result["cleanup_status"] == "failed"
        assert "invalid" in result["cleanup_error"]
        assert (work / "source.c").exists()
        assert (scratch / "session").exists()
    finally:
        cleanup_work(str(work), task)


@pytest.mark.asyncio
async def test_recovered_completed_scan_is_not_reaudited(isolated_batch, monkeypatch):
    root, _, stores = isolated_batch
    task = root / "tasks/1"
    store, scan_id = stores["1"]
    with store._connect() as connection:
        connection.execute(
            "UPDATE scans SET cleanup_intermediates = 1, status = 'completed' WHERE scan_id = ?", (scan_id,)
        )
    # Stand in for a verified sealed bundle; database cleanup and resume are real.
    monkeypatch.setattr(
        ScanStore,
        "scan_status",
        lambda *_: {
            "status": "completed",
            "integrity_status": "valid",
            "counts": {"poc_bundles": 2},
            "cleanup_summary_json": '{"status":"completed"}',
        },
    )
    config = batch.read_json(root / "batch.json")
    config.update(tasks={"1": {}}, concurrency=1, task_timeout=60)
    batch.atomic_json(root / "batch.json", config)
    batch.atomic_json(root / "state.json", {"tasks": {"1": {"status": "running"}}})
    monkeypatch.setattr(batch, "_worker_command", lambda *_: pytest.fail("A sealed audit must not repeat"))
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result["counts"] == {"completed": 1}
    assert result["tasks"][0]["poc_count"] == 2
    assert batch.read_json(task / "current.json")["scan_id"] == scan_id
