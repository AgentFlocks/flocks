"""Add feature hooks to Pro's original classes without replacing its provider."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps

_enabled = ContextVar('zw_scheduler_enabled', default=True)


def monitoring(execution):
    return bool(execution.execution_input_snapshot.get('context', {}).get('monitoring'))


def bind(module):
    name = module.__name__
    if not name.startswith('flockspro.runtime.') or module.__dict__.get('_zw_bound'):
        return
    module._zw_bound = True
    if name == 'flockspro.runtime.workflow.store':
        def error_fields(exc):
            fields = {'error_type':type(exc).__name__}
            code = getattr(exc, 'sqlstate', None)
            if isinstance(code, str) and len(code) == 5:
                fields['sqlstate'] = code
            return fields
        module.sqlite_error_fields = error_fields
    if name == 'flockspro.runtime.task.store':
        cls = module.TaskStore
        create = cls.create_scheduler.__func__
        @classmethod
        async def create_scheduler(cls, scheduler):
            if not _enabled.get():
                from flocks.task.models import SchedulerStatus
                scheduler.status = SchedulerStatus.DISABLED
            return await create(cls, scheduler)
        cls.create_scheduler = create_scheduler

        enqueue = cls.enqueue_schedule_occurrence.__func__
        @classmethod
        async def enqueue_schedule_occurrence(cls, snapshot, execution, *, next_run):
            if snapshot.context.get('monitoring'):
                from datetime import datetime, timezone
                from flocks.monitoring.scheduling import admit_slots
                await admit_slots(snapshot, datetime.now(timezone.utc))
                return None
            return await enqueue(cls, snapshot, execution, next_run=next_run)
        cls.enqueue_schedule_occurrence = enqueue_schedule_occurrence

        @classmethod
        async def claim_next_queue_execution(cls, *, exclude_ids=None):
            from flocks.enhancements.monitor_db import connection
            async with connection() as db:
                busy = await db.execute("SELECT q.id FROM task_executions q JOIN task_schedulers s ON s.id=q.scheduler_id WHERE q.status='queued' AND json_type(s.context,'$.monitoring')='object' AND EXISTS(SELECT 1 FROM task_executions r JOIN task_execution_queue_refs rq ON rq.execution_id=r.id WHERE r.scheduler_id=q.scheduler_id AND r.status='running' AND rq.status='running')")
                excluded = list(set(exclude_ids or ()) | {row['id'] for row in await busy.fetchall()})
                result = await cls._call('claim_next_queue_execution', {'ids':excluded}, transaction=db.native_transaction)
                return (cls._row_to_execution(result['execution']), cls._row_to_queue_ref(result['queue_ref'])) if result is not None else None
        cls.claim_next_queue_execution = claim_next_queue_execution

    elif name == 'flockspro.runtime.task.executor':
        cls = module.TaskExecutor
        create = cls._create_task_session.__func__
        run = cls._run_agent_session.__func__
        @classmethod
        async def create_session(cls, execution, scheduler):
            if monitoring(execution):
                return None  # The monitor validates its saved owner/policy first.
            return await create(cls, execution, scheduler)
        @classmethod
        async def run_session(cls, execution, session_id):
            if not monitoring(execution):
                return await run(cls, execution, session_id)
            from flocks.monitoring.runtime import dispatch
            from flocks.task.store import TaskStore
            from flocks.task.models import TaskStatus
            scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
            result = await dispatch(execution, scheduler)
            if result.status == TaskStatus.CANCELLED:
                raise module.TaskExecutionStopped(result.error or '监测已停止')
            if result.status == TaskStatus.FAILED:
                raise RuntimeError(result.error or '监测执行失败')
            return result.result_summary or ''
        cls._create_task_session = create_session
        cls._run_agent_session = run_session

    elif name == 'flockspro.runtime.task.manager':
        cls = module.TaskManager
        create = cls.create_scheduler.__func__
        start = cls.start.__func__
        cancel = cls._cancel_execution_runtime.__func__
        retry = cls._process_retry_queue
        @classmethod
        async def create_scheduler(cls, *, enabled=True, **kwargs):
            token = _enabled.set(enabled)
            try:
                return await create(cls, **kwargs)
            finally:
                _enabled.reset(token)
        @classmethod
        async def start_manager(cls, **kwargs):
            if cls._instance and cls._instance._running:
                return await start(cls, **kwargs)
            from flocks.enhancements.monitor_db import initialize
            await initialize()
            from flocks.monitoring.lifecycle import reconcile
            from flocks.monitoring.recovery import recover
            await reconcile()
            mgr = await start(cls, **kwargs)
            try:
                await recover()
            except Exception as exc:
                module.log.warn('monitor.recovery.failed', {'error_type':type(exc).__name__})
            return mgr
        @classmethod
        async def cancel_runtime(cls, execution, **kwargs):
            # Native cancellation verifies the requested generation before any
            # background session is interrupted. Keep that ordering.
            await cancel(cls, execution, **kwargs)
            if monitoring(execution):
                from flocks.monitoring.runtime import cancel as stop_monitor
                await stop_monitor(execution.id)
        async def process_retry_queue(self):
            from flocks.monitoring.recovery import recover
            await recover()
            return await retry(self)
        cls.create_scheduler = create_scheduler
        cls.start = start_manager
        cls._cancel_execution_runtime = cancel_runtime
        cls._process_retry_queue = process_retry_queue

    elif name == 'flockspro.runtime.session.session':
        cls = module.Session
        @classmethod
        async def ensure_daily_session(cls, *, session_id, project_id, directory,
                                       owner, scope, business_date, read_only_tools):
            from flocks.enhancements.monitor_db import connection
            async with connection() as db:
                # A deterministic id and the cross-node lock protect both the
                # check and native Session.create; no existing row is replaced.
                existing = await cls.get(project_id, session_id)
                if existing is not None:
                    if (existing.owner_user_id != owner or existing.project_id != project_id
                            or (existing.metadata or {}).get('monitorScope') != scope
                            or existing.status != 'active'):
                        raise ValueError('Daily monitoring session ownership or lifecycle mismatch')
                    return existing
                return await cls.create(id=session_id, project_id=project_id, directory=directory,
                    title=f'安全运营监测 · {business_date}', agent='rex', category='user',
                    owner_user_id=owner, owner_username=owner,
                    metadata={'monitorScope':scope,'businessDate':business_date,
                              'interactionMode':'unattended','readOnlyTools':read_only_tools})
        cls.ensure_daily_session = ensure_daily_session
        for method in ('delete', 'archive'):
            original = getattr(cls, method).__func__
            def guarded(original):
                @classmethod
                @wraps(original)
                async def wrapper(cls, project_id, session_id, *args, **kwargs):
                    if session_id.startswith('ses_monitor_'):
                        from flocks.monitoring.sessions import assert_history_mutable
                        await assert_history_mutable(session_id)
                    return await original(cls, project_id, session_id, *args, **kwargs)
                return wrapper
            setattr(cls, method, guarded(original))
