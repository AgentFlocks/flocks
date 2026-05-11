from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.provider.usage_service import RecordUsageRequest, get_usage_records, record_usage
from flocks.storage.storage import Storage
from flocks.task.models import TaskExecution, TaskScheduler, TaskStatus
from flocks.task.store import TaskStore


@pytest.fixture(autouse=True)
async def isolated_storage_and_task_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    Config._global_config = None
    Config._cached_config = None
    Storage._initialized = False
    Storage._db_path = None
    TaskStore._initialized = False
    TaskStore._conn = None

    await Storage.init()
    await TaskStore.init()

    yield

    await TaskStore.close()
    Config._global_config = None
    Config._cached_config = None
    Storage._initialized = False
    Storage._db_path = None
    TaskStore._initialized = False
    TaskStore._conn = None


@pytest.mark.asyncio
async def test_mixed_storage_task_and_usage_access_share_consistent_sqlite_config() -> None:
    scheduler = TaskScheduler(title="sqlite-mixed-access")
    await TaskStore.create_scheduler(scheduler)

    async def write_storage(idx: int) -> None:
        await Storage.set(f"mixed:key:{idx}", {"value": idx})

    async def write_usage(idx: int) -> None:
        await record_usage(
            RecordUsageRequest(
                provider_id="test-provider",
                model_id="test-model",
                session_id=f"session-{idx}",
                message_id=f"message-{idx}",
                input_tokens=idx + 1,
                output_tokens=idx + 2,
            )
        )

    async def write_task_execution(idx: int) -> None:
        execution = TaskExecution(
            scheduler_id=scheduler.id,
            title=f"execution-{idx}",
            status=TaskStatus.QUEUED,
        )
        await TaskStore.create_execution(execution)
        await TaskStore.enqueue_execution_ref(execution.id)

    await asyncio.gather(
        *[
            asyncio.gather(
                write_storage(idx),
                write_usage(idx),
                write_task_execution(idx),
            )
            for idx in range(5)
        ]
    )

    keys = await Storage.list_keys(prefix="mixed:key:")
    usage_records = await get_usage_records()
    executions, total = await TaskStore.list_executions(scheduler_id=scheduler.id, limit=20)

    assert len(keys) == 5
    assert len(usage_records) == 5
    assert total == 5
    assert len(executions) == 5
