from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import json

import pytest
from fastapi import HTTPException

from flocks.hub import installer
from flocks.monitoring import lifecycle
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import rows
from flocks.server.routes import security_monitoring as api
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionTriggerType, SchedulerStatus, TaskStatus
from flocks.task.store import TaskStore

OWNER = SimpleNamespace(id='owner')


async def install_unready(monkeypatch):
    discovery = AsyncMock(return_value=([], None, '未配置 XDR'))
    monkeypatch.setattr(lifecycle, 'discover', discovery)
    await installer.install_plugin('component', COMPONENT_ID)
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    return discovery, entry


@pytest.mark.asyncio
async def test_start_rechecks_new_connection_without_reinstall_and_pause_cancels_queue(monkeypatch):
    discovery, entry = await install_unready(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await api.start(user=OWNER)
    assert error.value.status_code == 409 and error.value.detail == '未配置 XDR'
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED

    discovery.return_value = (['configured-xdr'], 'sangfor_xdr_incidents', None)
    results = await asyncio.gather(api.start(user=OWNER), api.start(user=OWNER))
    assert all(result['installation']['ready'] and result['installation']['status'] == 'active' for result in results)
    assert results[0]['scheduledNextRun'] and results[0]['scheduledNextRun'] == results[1]['scheduledNextRun']
    assert results[0]['metrics']['started'] == 0  # Start arms a slot; it does not query a device.
    assert len(await rows('SELECT * FROM task_schedulers')) == 1
    assert len(await rows('SELECT * FROM monitor_installations')) == 1
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    saved = (await rows('SELECT * FROM monitor_installations'))[0]
    assert scheduler.context['monitoring'] == json.loads(saved['policy'])
    assert scheduler.context['monitoring']['devices'] == ['configured-xdr']
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=True)
    paused = await api.pause(user=OWNER)
    assert paused['installation']['status'] == 'disabled' and paused['scheduledNextRun'] is None
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.CANCELLED
    await lifecycle.reconcile()
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED
    assert (await api.start(user=OWNER))['installation']['status'] == 'active'


@pytest.mark.asyncio
async def test_controls_are_owner_scoped_and_cannot_enable_a_disabled_scene(monkeypatch):
    _, entry = await install_unready(monkeypatch)
    for action in (api.start, api.pause):
        with pytest.raises(HTTPException) as error:
            await action(user=SimpleNamespace(id='another-owner'))
        assert error.value.status_code == 404
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    await lifecycle.set_scene_enabled(False)
    with pytest.raises(HTTPException) as error:
        await api.start(user=OWNER)
    assert error.value.status_code == 409 and '场景已停用' in error.value.detail


@pytest.mark.asyncio
async def test_start_failure_stays_paused_and_does_not_expose_internal_error(monkeypatch):
    discovery, entry = await install_unready(monkeypatch)
    discovery.side_effect = RuntimeError('sensitive fixture detail')
    with pytest.raises(HTTPException) as error:
        await api.start(user=OWNER)
    assert error.value.status_code == 500
    assert 'sensitive' not in error.value.detail
    saved = (await rows('SELECT * FROM monitor_installations'))[0]
    assert not saved['ready'] and not saved['activation_pending']
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED


@pytest.mark.asyncio
async def test_resume_rechecks_capabilities_instead_of_trusting_old_ready_state(monkeypatch):
    discovery, entry = await install_unready(monkeypatch)
    discovery.return_value = (['configured-xdr'], 'sangfor_xdr_incidents', None)
    await api.start(user=OWNER)
    await api.pause(user=OWNER)
    discovery.return_value = ([], None, '存在多个可用 XDR 接入')
    with pytest.raises(HTTPException) as error:
        await api.start(user=OWNER)
    assert error.value.status_code == 409
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    assert not (await rows('SELECT ready FROM monitor_installations'))[0]['ready']
