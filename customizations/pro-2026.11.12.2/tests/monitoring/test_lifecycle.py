import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from flocks.hub import installer, local
from flocks.hub.catalog import load_manifest
from flocks.monitoring import lifecycle
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import rows
from flocks.task.manager import TaskManager
from flocks.task.store import TaskStore
from flocks.task.models import SchedulerStatus
from flocks.task.plugin_models import TaskSpec
from flocks.task.plugin_sync import upsert_task_specs

@pytest.mark.asyncio
async def test_real_hub_install_disable_reinstall_uninstall_restart(monkeypatch):
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['fixture-xdr'], 'sangfor_xdr_incidents', None)))
    record = await installer.install_plugin('component', COMPONENT_ID)
    assert Path(record.installPath).is_dir()
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    assert scheduler.status == SchedulerStatus.ACTIVE
    next_run = scheduler.trigger.next_run
    await installer.install_plugin('component', COMPONENT_ID)
    assert len(await rows('SELECT * FROM monitor_installations')) == 1
    assert len(await rows('SELECT * FROM task_schedulers')) == 1
    assert (await TaskStore.get_scheduler(scheduler.id)).trigger.next_run == next_run
    await TaskManager.disable_scheduler(scheduler.id)
    await installer.install_plugin('component', COMPONENT_ID)
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED
    await installer.uninstall_plugin('component', COMPONENT_ID)
    await lifecycle.reconcile()
    assert local.get_record('component', COMPONENT_ID) is None
    assert (await rows('SELECT installed FROM monitor_installations'))[0]['installed'] == 0
    await installer.install_plugin('component', COMPONENT_ID)
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.ACTIVE

@pytest.mark.asyncio
async def test_missing_capability_installed_not_ready(monkeypatch):
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=([], None, '缺少事件接口')))
    await installer.install_plugin('component', COMPONENT_ID)
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    assert entry['installed'] and not entry['ready']
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED

@pytest.mark.asyncio
async def test_invalid_core_and_multiple_workers_rejected_before_install(monkeypatch):
    manifest = load_manifest('component', COMPONENT_ID).model_copy(deep=True)
    manifest.requiredCoreCapabilities = ['future.unknown']
    with pytest.raises(ValueError): lifecycle.validate_manifest(manifest)
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    with pytest.raises(ValueError): await installer.install_plugin('component', COMPONENT_ID)
    assert not await rows('SELECT * FROM task_schedulers')

@pytest.mark.asyncio
async def test_task_spec_preserves_pause_and_next_run():
    spec = TaskSpec(dedup_key='fixture', title='fixture', cron='*/10 * * * *', enabled=False)
    await upsert_task_specs([spec])
    scheduler = await TaskStore.get_scheduler_by_dedup_key('fixture')
    assert scheduler.status == SchedulerStatus.DISABLED
    next_run = scheduler.trigger.next_run
    spec.enabled = True
    await upsert_task_specs([spec])
    scheduler = await TaskStore.get_scheduler(scheduler.id)
    assert scheduler.status == SchedulerStatus.DISABLED and scheduler.trigger.next_run == next_run

@pytest.mark.asyncio
async def test_failed_reinstall_restores_package_and_fails_closed(monkeypatch):
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['fixture-xdr'], 'sangfor_xdr_incidents', None)))
    original = await installer.install_plugin('component', COMPONENT_ID)
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(side_effect=RuntimeError('fixture interrupted install')))
    with pytest.raises(RuntimeError): await installer.install_plugin('component', COMPONENT_ID)
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    assert entry['installed'] and not entry['ready'] and not entry['activation_pending']
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    assert local.get_record('component', COMPONENT_ID).version == original.version
    assert Path(original.installPath, 'monitoring.json').is_file()
    await lifecycle.reconcile()
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED

@pytest.mark.asyncio
async def test_pending_activation_recovers_after_restart(monkeypatch):
    from flocks.monitoring.store import write
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['fixture-xdr'], 'sangfor_xdr_incidents', None)))
    await installer.install_plugin('component', COMPONENT_ID)
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    await TaskManager.disable_scheduler(entry['scheduler_id'])
    await write('UPDATE monitor_installations SET activation_pending=1')
    await lifecycle.reconcile()
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.ACTIVE
    assert (await rows('SELECT activation_pending FROM monitor_installations'))[0]['activation_pending'] == 0

@pytest.mark.asyncio
async def test_failed_startup_releases_executor_lock(monkeypatch):
    from filelock import FileLock
    monkeypatch.setattr(lifecycle,'reconcile',AsyncMock(side_effect=RuntimeError('fixture startup failure')))
    with pytest.raises(RuntimeError): await TaskManager.start()
    lock=FileLock(str(TaskStore.get_db_path())+'.executor.lock')
    lock.acquire(timeout=0)
    lock.release()


@pytest.mark.asyncio
async def test_native_scene_install_pause_restart_and_reenable(monkeypatch):
    from flocks.server.routes.hub import _load_scene_suites, set_native_scene_enabled, SceneEnabledRequest
    from flocks.monitoring.runtime import dispatch
    from flocks.task.models import ExecutionTriggerType
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['fixture-xdr'], 'sangfor_xdr_incidents', None)))
    await installer.install_plugin('component', COMPONENT_ID)
    scene = next(s for s in _load_scene_suites() if s.id == COMPONENT_ID)
    assert scene.workspaceKind == 'native' and scene.workspaceEnabled
    assert scene.workspaceEntryRoute == '/suites/host-security-monitor/session'
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)

    await set_native_scene_enabled(COMPONENT_ID, SceneEnabledRequest(enabled=False))
    await lifecycle.reconcile()
    assert (await rows('SELECT installed FROM monitor_installations'))[0]['installed'] == 1
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED
    assert not next(s for s in _load_scene_suites() if s.id == COMPONENT_ID).workspaceEnabled
    # Even a stale execution with its old active scheduler cannot start.
    with pytest.raises(PermissionError):
        await dispatch(execution, scheduler)
    await set_native_scene_enabled(COMPONENT_ID, SceneEnabledRequest(enabled=True))
    assert local.get_record('component', COMPONENT_ID).enabled
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED
    await installer.uninstall_plugin('component', COMPONENT_ID)
    assert next(s for s in _load_scene_suites() if s.id == COMPONENT_ID).workspaceEnabled is None


@pytest.mark.asyncio
async def test_unready_scene_can_open_without_starting_monitoring(monkeypatch):
    from flocks.server.routes.hub import _load_scene_suites
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=([], None, '未接入 XDR')))
    await installer.install_plugin('component', COMPONENT_ID)
    await lifecycle.set_scene_enabled(True)
    assert next(s for s in _load_scene_suites() if s.id == COMPONENT_ID).workspaceEnabled
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    assert not entry['ready']
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED


@pytest.mark.asyncio
async def test_native_scene_enable_requires_installed_package():
    from fastapi import HTTPException
    from flocks.server.routes.hub import set_native_scene_enabled, SceneEnabledRequest
    for scene_id in (COMPONENT_ID, 'unknown'):
        with pytest.raises(HTTPException) as error:
            await set_native_scene_enabled(scene_id, SceneEnabledRequest(enabled=True))
        assert error.value.status_code == 404
