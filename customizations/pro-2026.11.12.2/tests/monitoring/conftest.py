from pathlib import Path
import pytest
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.store import TaskStore
from flocks.task.manager import TaskManager
from flocks.session.message import Message
from flocks.session.session import Session
from flocks.auth.context import AuthUser, set_current_auth_user, reset_current_auth_user
from flocks.workspace.manager import WorkspaceManager

@pytest.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch, postgres_test_schema):
    # These are monitor unit tests: transport/model hooks are supplied by each
    # test. A database-only test core does not supervise the commercial ingress
    # and audit services. Keep the real backend entry point and Go DB gate.
    import importlib.metadata
    entries = importlib.metadata.entry_points
    def unit_entries(*args, **kwargs):
        if not kwargs and not args:
            return importlib.metadata.EntryPoints(e for e in entries() if e.group not in {'flocks.plugins','flocks.plugins.critical'})
        if kwargs.get('group') in {'flocks.plugins', 'flocks.plugins.critical'}:
            return importlib.metadata.EntryPoints(())
        return entries(*args, **kwargs)
    monkeypatch.setattr(importlib.metadata, 'entry_points', unit_entries)
    from flocks.hooks.pipeline import HookPipeline
    from flocks.plugin.loader import PluginLoader
    HookPipeline.reset()
    PluginLoader.clear_runtime_critical_entrypoint_failure()
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    monkeypatch.chdir(Path(__file__).resolve().parents[2] / '.work/flocks')
    monkeypatch.setenv('FLOCKS_WORKSPACE_DIR', str(tmp_path / '.flocks/workspace'))
    monkeypatch.setenv('FLOCKS_LOG_DIR', str(tmp_path / 'logs'))
    monkeypatch.delenv('FLOCKS_PROJECT_ROOTS', raising=False)
    monkeypatch.setattr(WorkspaceManager, '_instance', None)
    monkeypatch.setenv('FLOCKS_HUB_ROOT', str(Path(__file__).resolve().parents[2] / '.work/flocks/.flocks/flockshub'))
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    await TaskStore.close()
    TaskManager._instance = None
    # Each test owns a new event loop and isolated databases; discard loop-bound locks too.
    from flocks.monitoring import disposition, automatic, reports
    monkeypatch.setattr(disposition, '_locks', {})
    monkeypatch.setattr(automatic, '_config_locks', {})
    monkeypatch.setattr(reports, '_locks', {})
    token = set_current_auth_user(AuthUser(id='owner', username='owner', role='admin'))
    await Storage.init()
    await TaskStore.init()
    # Normal server startup initializes the device subsystem before discovery.
    await (await TaskStore.raw_db()).business('pgstore.device.initialize')
    # Core's round unit tests call run() directly. Acquire the same real Pro
    # claim that the production TaskExecutor owns around that call.
    import sys
    from flocks.monitoring import runtime
    original_run = runtime.run
    original_dispatch = runtime.dispatch
    lookup_execution = TaskStore.get_execution
    async def direct_claim(execution, *, new_round=False):
        current = await lookup_execution(execution.id)
        if new_round and current is not None and current.is_terminal:
            from flocks.task.models import ExecutionTriggerType
            scheduler = await TaskStore.get_scheduler(execution.scheduler_id)
            fresh = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
            execution.__dict__.update(fresh.__dict__)
        await TaskStore.ensure_queued_execution_ref(execution.id)
        claimed = await TaskStore.claim_next_queue_execution()
        assert claimed and claimed[0].id == execution.id
        # Tests may adjust the pending snapshot before dispatch; retain those
        # supplied business inputs while taking lifecycle identity from Go.
        execution.status = claimed[0].status
        execution.started_at = claimed[0].started_at
        execution.updated_at = claimed[0].updated_at

    async def leased_run(execution, *args, **kwargs):
        from flocks.task import lease as leases
        current_lease = leases._current_lease.get()
        if current_lease is not None and current_lease.execution_id == execution.id:
            return await original_run(execution, *args, **kwargs)
        from flocks.task.lease import ExecutionLease
        from flocks.task.models import TaskStatus
        from datetime import datetime, timezone
        await direct_claim(execution, new_round=True)
        async with ExecutionLease(execution.id) as lease:
            assert lease.acquired
            execution.owner_node_id, execution.owner_generation = lease.node_id, lease.generation
            try:
                result = await original_run(execution, *args, **kwargs)
            except Exception:
                execution.status=TaskStatus.FAILED
                execution.completed_at=datetime.now(timezone.utc)
                await lease.finish_execution(execution)
                raise
            execution.status = TaskStatus.COMPLETED
            execution.completed_at = datetime.now(timezone.utc)
            await lease.finish_execution(execution)
            return result
    async def leased_dispatch(execution, *args, **kwargs):
        from flocks.task.models import TaskExecution
        from flocks.task import lease as leases
        current_lease = leases._current_lease.get()
        if not isinstance(execution, TaskExecution) or current_lease is not None and current_lease.execution_id == execution.id:
            return await original_dispatch(execution, *args, **kwargs)
        current = await lookup_execution(execution.id)
        if current is None or current.is_terminal:
            # Private start-gate unit tests deliberately present cancelled or
            # deleted rows. Do not enqueue or claim them on their behalf.
            return await original_dispatch(execution, *args, **kwargs)
        from flocks.task.lease import ExecutionLease
        await direct_claim(execution)
        async with ExecutionLease(execution.id) as lease:
            assert lease.acquired
            execution.owner_node_id, execution.owner_generation=lease.node_id,lease.generation
            try:
                result=await original_dispatch(execution, *args, **kwargs)
                current=await lookup_execution(execution.id)
                if current is not None and current.is_terminal:
                    return current
                written=await lease.finish_execution(result)
                return result if written is not None else await lookup_execution(execution.id)
            except leases.ExecutionOwnershipError:
                current=await lookup_execution(execution.id)
                if current is not None and current.is_terminal:
                    return current
                raise
    for name, module in tuple(sys.modules.items()):
        if name.startswith('tests.monitoring.') and getattr(module, 'run', None) is original_run:
            monkeypatch.setattr(module, 'run', leased_run)
        if name.startswith('tests.monitoring.') and getattr(module, 'dispatch', None) is original_dispatch:
            monkeypatch.setattr(module, 'dispatch', leased_dispatch)
    monkeypatch.setattr(runtime, 'run', leased_run)
    monkeypatch.setattr(runtime, 'dispatch', leased_dispatch)
    yield
    await TaskManager.stop()
    await TaskStore.close()
    reset_current_auth_user(token)
    Storage._initialized = False
    Storage._db_path = None
