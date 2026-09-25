"""Atomic slot admission into the existing TaskManager queue."""
from datetime import datetime, timedelta, timezone
from flocks.task.models import TaskExecution, TaskStatus, ExecutionTriggerType
from flocks.task.scheduler import TaskScheduler as SchedulerLoop
from flocks.task.store import TaskStore
from .store import connection, encode

IMMEDIATE_START_PENDING = 2  # 1 is the existing installation activation intent.


async def persist_execution(db, execution, now, *, enqueue=True):
    """Persist execution and queue reference in the caller's transaction."""
    columns = ('id,scheduler_id,title,description,priority,source,trigger_type,status,'
               'delivery_status,queued_at,started_at,completed_at,duration_ms,session_id,'
               'result_summary,error,execution_input_snapshot,workspace_directory,retry,'
               'execution_mode,agent_name,workflow_id,created_at,updated_at')
    row = TaskStore._execution_to_row(execution)
    await db.execute(f'INSERT INTO task_executions ({columns}) VALUES ({",".join("?" for _ in row)})', row)
    if enqueue:
        await db.execute('INSERT INTO task_execution_queue_refs(id,execution_id,status,created_at) VALUES(?,?,?,?)',
                         ('monitor-' + execution.id, execution.id, 'queued', now.isoformat()))


async def start_immediately(scheduler, now=None):
    """Consume one durable Start intent, atomically activating and queueing work."""
    now = now or datetime.now(timezone.utc)
    following = SchedulerLoop._compute_next(scheduler.trigger, after=now)
    if not following:
        raise ValueError('Cannot calculate monitoring schedule')
    async with connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT 1 FROM monitor_installations WHERE scheduler_id=? AND installed=1 '
                               'AND ready=1 AND activation_pending=?', (scheduler.id, IMMEDIATE_START_PENDING))
        if not await cur.fetchone():
            return  # A retry/recovery cannot consume the same intent twice.
        execution = TaskExecution(
            scheduler_id=scheduler.id, title=scheduler.title, description=scheduler.description,
            priority=scheduler.priority, source=scheduler.source.model_copy(deep=True),
            trigger_type=ExecutionTriggerType.RUN_ONCE, status=TaskStatus.QUEUED, queued_at=now,
            retry=scheduler.retry.model_copy(deep=True), execution_mode=scheduler.execution_mode,
            agent_name=scheduler.agent_name, workspace_directory=scheduler.workspace_directory,
            execution_input_snapshot={'context': scheduler.context, 'monitoringStart': 'immediate'},
        )
        await persist_execution(db, execution, now)
        trigger = scheduler.trigger.model_copy(update={'next_run': following})
        await db.execute("UPDATE task_schedulers SET status='active',trigger=?,updated_at=? WHERE id=?",
                         (encode(trigger.model_dump(mode='json')), now.isoformat(), scheduler.id))
        await db.execute('UPDATE monitor_installations SET activation_pending=0 WHERE scheduler_id=?', (scheduler.id,))


async def admit_slots(scheduler, now):
    # Online delays are queued. After downtime, retain missed slots as cancelled
    # records and query one recent slot with the persisted cursor for catch-up.
    slot = scheduler.trigger.next_run
    if not slot:
        return
    async with connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute('SELECT status, trigger FROM task_schedulers WHERE id=?', (scheduler.id,))
        current = await cur.fetchone()
        if not current or current['status'] != 'active':
            return
        import json
        trigger = json.loads(current['trigger'])
        raw_slot = trigger.get('next_run') or trigger.get('nextRun')
        if not raw_slot:
            return
        slot = datetime.fromisoformat(raw_slot)
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=timezone.utc)
        # Bound work per tick; next_run persists the remaining backlog.
        for _ in range(144):
            if slot > now:
                break
            following = SchedulerLoop._compute_next(scheduler.trigger, after=slot)
            if not following:
                raise ValueError('Cannot calculate monitoring schedule')
            missed = now - slot > timedelta(hours=1)
            execution = TaskExecution(
                scheduler_id=scheduler.id, title=scheduler.title,
                description=scheduler.description, priority=scheduler.priority,
                source=scheduler.source.model_copy(deep=True),
                trigger_type=ExecutionTriggerType.SCHEDULED,
                status=TaskStatus.CANCELLED if missed else TaskStatus.QUEUED,
                queued_at=now, completed_at=now if missed else None,
                error='停机缺失时隙；后续轮次增量追赶' if missed else None,
                retry=scheduler.retry.model_copy(deep=True),
                execution_mode=scheduler.execution_mode, agent_name=scheduler.agent_name,
                workspace_directory=scheduler.workspace_directory,
                execution_input_snapshot={'context': scheduler.context, 'scheduledFor': slot.isoformat()},
            )
            cur = await db.execute('INSERT OR IGNORE INTO monitor_slots VALUES(?,?,?,?)',
                                  (scheduler.id, slot.isoformat(), execution.id, 'missed' if missed else 'queued'))
            if cur.rowcount:
                await persist_execution(db, execution, now, enqueue=not missed)
            slot = following
        scheduler.trigger.next_run = slot
        await db.execute('UPDATE task_schedulers SET trigger=?,updated_at=? WHERE id=?',
                         (encode(scheduler.trigger.model_dump(mode='json')), now.isoformat(), scheduler.id))
