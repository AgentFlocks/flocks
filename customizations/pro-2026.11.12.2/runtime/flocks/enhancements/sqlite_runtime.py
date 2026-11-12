"""Monitoring hooks for the managed release's original SQLite classes.

The PostgreSQL provider and its execution leases remain owned by Pro. These
hooks add monitoring entry points without copying/replacing either task engine.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
from pathlib import Path
import sys

_TARGETS = {'flocks.task.store', 'flocks.task.manager', 'flocks.task.executor',
            'flocks.task.scheduler', 'flocks.session.session'}
_runtime_lock = None


def _has_monitoring(function, module):
    return module in function.__code__.co_consts


def bind(module):
    if module.__dict__.get('_zw_sqlite_bound'):
        return
    module._zw_sqlite_bound = True
    name = module.__name__
    from .pro_runtime import _enabled, monitoring
    if name == 'flocks.task.executor':
        cls = module.TaskExecutor
        original = cls.dispatch.__func__
        if _has_monitoring(original, 'flocks.monitoring.runtime'):
            return
        @classmethod
        async def dispatch(cls, execution, scheduler):
            if monitoring(execution):
                from flocks.monitoring.runtime import dispatch
                return await dispatch(execution, scheduler)
            return await original(cls, execution, scheduler)
        cls.dispatch = dispatch
    elif name == 'flocks.task.scheduler':
        cls = module.TaskScheduler
        original = cls._tick_scheduler
        if _has_monitoring(original, 'flocks.monitoring.scheduling'):
            return
        async def tick(self, scheduler, now):
            if scheduler.context.get('monitoring'):
                from flocks.monitoring.scheduling import admit_slots
                return await admit_slots(scheduler, now)
            return await original(self, scheduler, now)
        cls._tick_scheduler = tick
    elif name == 'flocks.task.store':
        cls = module.TaskStore
        create = cls.create_scheduler.__func__
        @classmethod
        async def create_scheduler(cls, scheduler):
            if not _enabled.get():
                from flocks.task.models import SchedulerStatus
                scheduler.status = SchedulerStatus.DISABLED
            return await create(cls, scheduler)
        cls.create_scheduler = create_scheduler
        claim = cls.claim_next_queue_execution.__func__
        if any(isinstance(value, str) and '$.monitoring' in value for value in claim.__code__.co_consts):
            return
        @classmethod
        async def claim_next(cls, *, exclude_ids=None):
            from flocks.monitoring.store import rows
            busy = await rows("SELECT q.id FROM task_executions q JOIN task_schedulers s ON s.id=q.scheduler_id "
                "WHERE q.status='queued' AND json_type(s.context,'$.monitoring')='object' "
                "AND EXISTS(SELECT 1 FROM task_executions r JOIN task_execution_queue_refs rq ON rq.execution_id=r.id "
                "WHERE r.scheduler_id=q.scheduler_id AND r.status='running' AND rq.status='running')")
            return await claim(cls, exclude_ids=list(set(exclude_ids or ()) | {row['id'] for row in busy}))
        cls.claim_next_queue_execution = claim_next
    elif name == 'flocks.task.manager':
        cls = module.TaskManager
        start, stop = cls.start.__func__, cls.stop.__func__
        if _has_monitoring(start, 'flocks.monitoring.lifecycle'):
            return
        create = cls.create_scheduler.__func__
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
            global _runtime_lock
            if cls._instance and cls._instance._running:
                return await start(cls, **kwargs)
            from filelock import FileLock
            from flocks.task.store import TaskStore
            from flocks.monitoring.store import _initialize_sqlite
            await TaskStore.init()
            lock = FileLock(str(TaskStore.get_db_path()) + '.executor.lock')
            lock.acquire(timeout=0)
            try:
                await _initialize_sqlite()
                from flocks.monitoring.lifecycle import reconcile
                from flocks.monitoring.recovery import recover
                await reconcile()
                await recover()
                manager = await start(cls, **kwargs)
            except BaseException:
                lock.release()
                raise
            _runtime_lock = lock
            return manager
        @classmethod
        async def stop_manager(cls):
            global _runtime_lock
            try:
                from flocks.monitoring.runtime import _running, cancel
                for execution_id in list(_running):
                    await cancel(execution_id)
                return await stop(cls)
            finally:
                if _runtime_lock is not None:
                    _runtime_lock.release()
                    _runtime_lock = None
        @classmethod
        async def cancel_runtime(cls, execution, **kwargs):
            await cancel(cls, execution, **kwargs)
            if monitoring(execution):
                from flocks.monitoring.runtime import cancel as stop_monitor
                await stop_monitor(execution.id)
        async def process_retry(self):
            from flocks.monitoring.reports import retry_exports
            from flocks.monitoring.recovery import recover
            await retry_exports()
            await recover()
            return await retry(self)
        cls.create_scheduler = create_scheduler
        cls.start, cls.stop = start_manager, stop_manager
        cls._cancel_execution_runtime = cancel_runtime
        cls._process_retry_queue = process_retry
    elif name == 'flocks.session.session':
        cls = module.Session
        if hasattr(cls, 'ensure_daily_session'):
            return
        @classmethod
        async def ensure_daily(cls, *, session_id, project_id, directory, owner,
                               scope, business_date, read_only_tools):
            from flocks.storage.storage import Storage
            async with cls.lifecycle_lock(session_id):
                existing = await Storage.get(f'session:{project_id}:{session_id}', module.SessionInfo)
                if existing is not None:
                    if (existing.owner_user_id != owner or existing.project_id != project_id
                            or (existing.metadata or {}).get('monitorScope') != scope or existing.status != 'active'):
                        raise ValueError('Daily monitoring session ownership or lifecycle mismatch')
                    return existing
                return await cls.create(id=session_id, project_id=project_id, directory=directory,
                    title=f'安全运营监测 · {business_date}', agent='rex', category='user',
                    owner_user_id=owner, owner_username=owner,
                    metadata={'monitorScope':scope, 'businessDate':business_date,
                              'interactionMode':'unattended', 'readOnlyTools':read_only_tools})
        cls.ensure_daily_session = ensure_daily
        for method in ('delete', 'archive'):
            original = getattr(cls, method).__func__
            def guard(original):
                @classmethod
                async def guarded(cls, project_id, session_id, *args, **kwargs):
                    if session_id.startswith('ses_monitor_'):
                        from flocks.monitoring.sessions import assert_history_mutable
                        await assert_history_mutable(session_id)
                    return await original(cls, project_id, session_id, *args, **kwargs)
                return guarded
            setattr(cls, method, guard(original))


class _Loader(importlib.abc.Loader):
    def __init__(self, delegate):
        self.delegate = delegate
    def create_module(self, spec):
        method = getattr(self.delegate, 'create_module', None)
        return method(spec) if method else None
    def exec_module(self, module):
        self.delegate.exec_module(module)
        bind(module)
    def __getattr__(self, name):
        return getattr(self.delegate, name)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _TARGETS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _Loader(spec.loader)
        return spec


def install():
    # Only the managed Pro layout needs the compatibility hooks. Enhanced
    # source checkouts already provide these entry points themselves.
    core = Path(__file__).resolve().parents[2]
    if not (core.parent / 'python/share/flockspro').is_dir():
        return
    if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
