"""The task-center projection hides monitor failures without changing records."""
from datetime import datetime, timedelta, timezone

import pytest

from flocks.db_backend import backend_choice
from flocks.task.models import TaskExecution, TaskScheduler, TaskStatus, TaskPriority
from flocks.task.store import TaskStore
from flocks.task.presentation import list_task_center_executions, task_center_dashboard


@pytest.fixture(autouse=True)
async def isolated_sqlite(tmp_path, monkeypatch):
    if backend_choice().backend != 'sqlite':
        yield
        return
    from flocks.storage.storage import Storage
    from flocks.config.config import Config
    await TaskStore.close()
    monkeypatch.setenv('FLOCKS_DATA_DIR', str(tmp_path/'data'))
    Config._global_config = Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    await Storage.init()
    yield
    await TaskStore.close()
    Storage._db_path = None
    Storage._initialized = False


async def execution(*, monitor=False, legacy=False, status=TaskStatus.FAILED,
                    title='安全运营监测同名普通任务', age=0, priority=TaskPriority.NORMAL):
    scheduler = TaskScheduler(title=title)
    await TaskStore.create_scheduler(scheduler)
    when = datetime.now(timezone.utc) - timedelta(days=age)
    row = TaskExecution(scheduler_id=scheduler.id, title=title, status=status,
        priority=priority, started_at=when, completed_at=when, queued_at=when,
        error='synthetic original failure',
        execution_input_snapshot={'context': {'monitoring': {'scope': 'fixture'}}} if monitor else {},
        session_id='ses_monitor_legacy_fixture' if legacy else None)
    await TaskStore.create_execution(row)
    return row


@pytest.mark.asyncio
async def test_existing_monitor_failures_are_hidden_but_stored_details_and_history_remain():
    normal = await execution()
    monitor = await execution(monitor=True)
    legacy = await execution(legacy=True)
    completed = await execution(monitor=True, status=TaskStatus.COMPLETED)
    running = await execution(monitor=True, status=TaskStatus.RUNNING)
    items, total = await list_task_center_executions(status=TaskStatus.FAILED)
    assert total == 1 and [x.id for x in items] == [normal.id]
    items, total = await list_task_center_executions()
    assert total == 3 and {x.id for x in items} == {normal.id, completed.id, running.id}
    assert (await TaskStore.list_executions(status=TaskStatus.FAILED))[1] == 3
    for row in [monitor, legacy]:
        stored = await TaskStore.get_execution(row.id)
        assert stored.status == TaskStatus.FAILED and stored.error == row.error
        assert (await TaskStore.list_scheduler_executions(row.scheduler_id))[1] == 1


@pytest.mark.asyncio
async def test_filtering_precedes_pagination_with_many_monitor_failures():
    for _ in range(25):
        await execution(monitor=True)
    normal = [await execution() for _ in range(3)]
    first, total = await list_task_center_executions(status=TaskStatus.FAILED, limit=2)
    second, total2 = await list_task_center_executions(status=TaskStatus.FAILED, offset=2, limit=2)
    assert total == total2 == 3 and len(first) == 2 and len(second) == 1
    assert {x.id for x in first + second} == {x.id for x in normal}


@pytest.mark.asyncio
async def test_filters_keep_literal_title_priority_scheduler_and_time_boundaries():
    target = await execution(title='literal %_ task', priority=TaskPriority.HIGH)
    await execution(title='literal %_ task', monitor=True, priority=TaskPriority.HIGH)
    await execution(title='literal AB task', priority=TaskPriority.HIGH)
    await execution(title='literal %_ task', age=2)
    items, total = await list_task_center_executions(title='%_', priority=TaskPriority.HIGH,
        started_after=target.started_at, started_before=target.started_at, sort_by='started_at', sort_order='asc')
    assert total == 1 and items[0].id == target.id
    assert (await list_task_center_executions(scheduler_id=target.scheduler_id))[1] == 1


@pytest.mark.asyncio
async def test_weekly_count_only_excludes_failed_monitors_and_keeps_other_counts():
    await execution()
    await execution(monitor=True)
    await execution(legacy=True)
    await execution(age=9)
    await execution(monitor=True, status=TaskStatus.COMPLETED)
    before = await TaskStore.dashboard_counts()
    after = await task_center_dashboard(before)
    assert after['failed_week'] == 1 and before['failed_week'] == 3
    assert {k:v for k,v in after.items() if k != 'failed_week'} == {k:v for k,v in before.items() if k != 'failed_week'}


@pytest.mark.asyncio
async def test_api_projection_is_explicit_and_original_detail_remains_available():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from flocks.server.routes.task_entities import router
    app = FastAPI()
    app.include_router(router, prefix='/api')
    normal = await execution()
    monitor = await execution(monitor=True)
    async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
        original = (await client.get('/api/task-executions?status=failed')).json()
        visible = (await client.get('/api/task-executions?status=failed&excludeMonitoringFailures=true')).json()
        assert original['total'] == 2 and visible['total'] == 1
        assert visible['items'][0]['id'] == normal.id
        detail = (await client.get('/api/task-executions/' + monitor.id)).json()
        assert detail['status'] == 'failed' and detail['error'] == monitor.error
        counts = (await client.get('/api/task-system/dashboard?excludeMonitoringFailures=true')).json()
        assert counts['failed_week'] == 1
