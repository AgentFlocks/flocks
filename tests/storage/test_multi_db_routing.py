from __future__ import annotations


import pytest

from flocks.storage.storage import Storage
from flocks.task.models import TaskScheduler
from flocks.task.store import TaskStore
from flocks.workflow.store import WorkflowStore


@pytest.mark.asyncio
async def test_workflow_prefix_clear_does_not_invalidate_session_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    await Storage.init()
    await Storage.write("workflow_execution_step/exec-1/00000001", {"ok": True})
    calls: list[str] = []

    monkeypatch.setattr(
        Session,
        "invalidate_cache",
        lambda session_id=None: calls.append(
            "session" if session_id is None else f"session:{session_id}"
        ),
    )
    monkeypatch.setattr(
        Message,
        "invalidate_cache",
        lambda session_id=None, **_kwargs: calls.append(f"message:{session_id}"),
    )

    assert await Storage.clear("workflow_execution_step/exec-1/") == 1
    assert calls == []


@pytest.mark.asyncio
async def test_session_and_message_prefix_clear_invalidate_matching_runtime_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    await Storage.init()
    await Storage.write("session:proj:ses-1", {"id": "ses-1"})
    await Storage.write("message:ses-1", [])
    await Storage.write("message_parts:ses-1/msg-1", [])
    calls: list[str] = []

    monkeypatch.setattr(
        Session,
        "invalidate_cache",
        lambda session_id=None: calls.append(
            "session" if session_id is None else f"session:{session_id}"
        ),
    )
    monkeypatch.setattr(
        Message,
        "invalidate_cache",
        lambda session_id=None, **_kwargs: calls.append(f"message:{session_id}"),
    )

    assert await Storage.clear("session:") == 1
    assert calls == ["session"]

    calls.clear()
    assert await Storage.clear("message:") == 1
    assert calls == ["message:None"]

    calls.clear()
    assert await Storage.clear("message_parts:") == 1
    assert calls == ["message:None"]


@pytest.mark.asyncio
async def test_task_store_init_failure_clears_half_open_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await Storage.init()

    async def fail_migration(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        TaskStore,
        "_add_missing_execution_columns",
        classmethod(fail_migration),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await TaskStore.init()

    assert TaskStore._handle is None
    assert TaskStore._initialized is False

@pytest.mark.asyncio
async def test_all_domains_share_postgres_without_creating_legacy_files(tmp_path):
    legacy_paths = [tmp_path / name for name in ("flocks.db", "tasks.db", "workflow.db")]
    await Storage.init(legacy_paths[0])
    await Storage.set("session:project:probe", {"id": "probe"})
    scheduler = TaskScheduler(title="postgres routing")
    await TaskStore.create_scheduler(scheduler)
    await WorkflowStore.kv_put("workflow_runtime/probe", {"state": "active"})
    async with Storage.transaction(legacy_paths[1]) as db:
        assert db.dialect.name == "postgres"
        assert await db.fetch_one("SELECT id FROM task_schedulers WHERE id = ?", (scheduler.id,)) == (scheduler.id,)
        assert await db.fetch_one("SELECT key FROM runtime_state WHERE key = ?", ("session:project:probe",)) is not None
        assert await db.fetch_one("SELECT key FROM workflow_kv WHERE key = ?", ("workflow_runtime/probe",)) is not None
    assert all(not path.exists() for path in legacy_paths)


@pytest.mark.asyncio
async def test_prefix_operations_treat_underscore_and_percent_literally():
    await Storage.set("literal_a%:one", 1)
    await Storage.set("literalXaY:two", 2)
    assert await Storage.list_keys("literal_a%:") == ["literal_a%:one"]
    assert await Storage.clear("literal_a%:") == 1
    assert await Storage.get("literalXaY:two") == 2


@pytest.mark.asyncio
async def test_clear_requires_scope_and_preserves_other_domains():
    await Storage.set("session:project:one", {"id": "one"})
    await WorkflowStore.kv_put("workflow_runtime/one", {"state": "active"})
    with pytest.raises(ValueError, match="prefix"):
        await Storage.clear()
    assert await Storage.clear("session:project:") == 1
    assert await WorkflowStore.kv_get("workflow_runtime/one") == {"state": "active"}
