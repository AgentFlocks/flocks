import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_imported_monitor_sequence_gets_generator_without_rewriting_history():
    from flocks.enhancements import monitor_db
    from flocks.task.store import TaskStore
    from flocks.monitoring.store import rows, write
    await monitor_db.initialize()
    handle = await TaskStore.raw_db()
    async with handle.transaction() as db:
        await db.execute('ALTER TABLE monitor_attempts ALTER COLUMN sequence DROP DEFAULT')
    await write('INSERT INTO monitor_attempts(sequence,id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (41,'legacy','fixture','owner','project','fixture','2026-11-12','session','message','2026-11-12T00:00:00Z','completed'))
    before = (await rows('SELECT * FROM monitor_attempts WHERE id=?', ('legacy',)))[0]
    monitor_db._initialized.clear()
    await monitor_db.initialize()
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('next','fixture','owner','project','fixture','2026-11-12','session','message','2026-11-12T00:00:00Z','completed'))
    assert (await rows('SELECT * FROM monitor_attempts WHERE id=?', ('legacy',)))[0] == before
    assert (await rows('SELECT sequence FROM monitor_attempts WHERE id=?', ('next',)))[0]['sequence'] == 42
    monitor_db._initialized.clear()
    await monitor_db.initialize()
    assert len(await rows('SELECT * FROM monitor_attempts')) == 2


@pytest.mark.asyncio
async def test_monitor_uses_go_postgres_and_atomic_rollback(tmp_path, monkeypatch):
    from flocks.task.store import TaskStore
    from flocks.monitoring.store import connection, rows, write
    legacy = tmp_path / 'never-created-tasks.db'
    monkeypatch.setattr(TaskStore, 'get_db_path', lambda: legacy)
    await write("INSERT INTO monitor_slots VALUES(?,?,?,?)", ('s','2026-11-12','e','queued'))
    assert (await rows('SELECT * FROM monitor_slots'))[0]['execution_id'] == 'e'
    with pytest.raises(RuntimeError):
        async with connection() as db:
            await db.execute("UPDATE monitor_slots SET status='cancelled'")
            raise RuntimeError('rollback')
    assert (await rows('SELECT status FROM monitor_slots'))[0]['status'] == 'queued'
    assert not legacy.exists()


@pytest.mark.asyncio
async def test_json_and_datetime_queries():
    from flocks.monitoring.store import rows
    result = (await rows("SELECT json_extract(?, '$.success')=1 AS ok,json_type(?,'$.risk') AS kind,json_object('risk',json_extract(?,'$.risk'),'yes',json('true')) AS data,julianday(?) AS jd",
        (json.dumps({'success':True}),'{"risk":3}','{"risk":3}','2026-11-12T00:00:00+00:00')))[0]
    assert result['ok'] is True
    assert result['kind'] == 'integer'
    assert json.loads(result['data']) == {'risk':3,'yes':True}
    assert result['jd'] == 2461356.5
    assert (await rows("SELECT value FROM json_each(?) WHERE type='object'", ('[{"x":1}]',)))[0]['value'] == '{"x": 1}'


@pytest.mark.asyncio
async def test_disabled_scheduler_never_becomes_active():
    from flocks.task.manager import TaskManager
    from flocks.task.models import SchedulerMode, TaskTrigger, SchedulerStatus
    scheduler = await TaskManager.create_scheduler(title='disabled monitor', mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron='*/10 * * * *'), enabled=False)
    assert scheduler.status == SchedulerStatus.DISABLED


@pytest.mark.asyncio
async def test_readonly_sync_polling_and_mail_checkpoint():
    from flocks.enhancements.monitor_db import initialize, sync_connection
    from flocks.monitoring.store import write
    await initialize()
    def query():
        with sync_connection() as db:
            assert db.execute("SELECT 1 FROM sqlite_master WHERE name='monitor_slots'").fetchone()[0] == 1
            assert 'id' in {r['name'] for r in db.execute('PRAGMA table_info(monitor_attempts)').fetchall()}
    await asyncio.to_thread(query)
    await write('INSERT OR IGNORE INTO monitor_slots VALUES(?,?,?,?)', ('s','day','e','queued'))
    await write('INSERT OR IGNORE INTO monitor_slots VALUES(?,?,?,?)', ('s','day','e2','queued'))
    from flocks.monitoring.store import rows
    assert len(await rows('SELECT * FROM monitor_slots')) == 1


@pytest.mark.asyncio
async def test_native_dispatch_keeps_lease_and_finishes_once(monkeypatch):
    from flocks.task.models import TaskScheduler,TaskExecution,TaskStatus
    from flocks.task.store import TaskStore
    from flocks.task.executor import TaskExecutor
    scheduler=TaskScheduler(title='native leased monitor',context={'monitoring':{'fixture':True}})
    await TaskStore.create_scheduler(scheduler)
    execution=TaskExecution(scheduler_id=scheduler.id,title='one run',status=TaskStatus.RUNNING,
                            execution_input_snapshot={'context':scheduler.context})
    await TaskStore.create_execution(execution)
    calls=[]
    async def run(execution,scheduler):
        from flocks.task.lease import require_current_execution
        require_current_execution()
        calls.append(execution.id)
        assert execution.owner_node_id and execution.owner_generation>0
        execution.status=TaskStatus.COMPLETED
        execution.result_summary='fixture completed'
        return execution
    monkeypatch.setattr('flocks.monitoring.runtime.dispatch',run)
    result=await TaskExecutor.dispatch(execution,scheduler)
    assert result.status==TaskStatus.COMPLETED
    stored=await TaskStore.get_execution(execution.id)
    assert stored.status==TaskStatus.COMPLETED and stored.result_summary=='fixture completed'
    from flocks.task.lease import ExecutionOwnershipError
    with pytest.raises(ExecutionOwnershipError):
        await TaskExecutor.dispatch(execution,scheduler)
    assert calls==[execution.id]
