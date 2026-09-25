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
    assert results[0]['metrics']['started'] == 0  # Queued is not yet an actual started attempt.
    queued = await rows("SELECT * FROM task_executions WHERE status='queued'")
    assert len(queued) == 1  # Concurrent Start requests admit exactly one immediate round.
    assert queued[0]['trigger_type'] == ExecutionTriggerType.RUN_ONCE.value
    assert json.loads(queued[0]['execution_input_snapshot'])['monitoringStart'] == 'immediate'
    assert len(await rows("SELECT * FROM task_execution_queue_refs WHERE status='queued'")) == 1
    assert len(await rows('SELECT * FROM task_schedulers')) == 1
    assert len(await rows('SELECT * FROM monitor_installations')) == 1
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    saved = (await rows('SELECT * FROM monitor_installations'))[0]
    assert scheduler.context['monitoring'] == json.loads(saved['policy'])
    assert scheduler.context['monitoring']['devices'] == ['configured-xdr']
    execution = await TaskStore.get_execution(queued[0]['id'])
    paused = await api.pause(user=OWNER)
    assert paused['installation']['status'] == 'disabled' and paused['scheduledNextRun'] is None
    assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.CANCELLED
    await lifecycle.reconcile()
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED
    assert (await api.start(user=OWNER))['installation']['status'] == 'active'
    assert len(await rows("SELECT * FROM task_executions WHERE status='queued'")) == 1
    assert len(await rows('SELECT * FROM task_executions')) == 2


@pytest.mark.parametrize('minute,second,next_minute', [(34, 56, 40), (40, 0, 50)])
async def test_immediate_start_and_same_cron_tick_do_not_duplicate(monkeypatch, minute, second, next_minute):
    from datetime import datetime, timezone
    from flocks.monitoring.scheduling import admit_slots, start_immediately
    from flocks.task.queue import TaskQueue
    discovery, entry = await install_unready(monkeypatch)
    discovery.return_value = (['configured-xdr'], 'sangfor_xdr_incidents', None)
    stamp = datetime(2026, 9, 25, 5, minute, second, tzinfo=timezone.utc)
    async def start(scheduler):
        await start_immediately(scheduler, stamp)
    monkeypatch.setattr(lifecycle, 'start_immediately', start)
    stale = await TaskStore.get_scheduler(entry['scheduler_id'])
    stale.trigger.next_run = stamp
    await asyncio.gather(api.start(user=OWNER), admit_slots(stale, stamp))
    await admit_slots(stale, stamp)
    scheduler = await TaskStore.get_scheduler(stale.id)
    assert scheduler.trigger.next_run == stamp.replace(minute=next_minute, second=0)
    assert len(await rows('SELECT * FROM task_executions')) == 1
    first = await TaskQueue().dequeue()
    assert first and first.trigger_type == ExecutionTriggerType.RUN_ONCE
    # The following normal slot remains scheduled and keeps serial execution.
    await admit_slots(scheduler, scheduler.trigger.next_run)
    assert len(await rows('SELECT * FROM task_executions')) == 2
    assert len(await rows('SELECT * FROM monitor_slots')) == 1
    assert await TaskQueue().dequeue() is None


async def test_failed_immediate_admission_rolls_back_and_recovers_once(monkeypatch):
    from flocks.monitoring import scheduling
    discovery, entry = await install_unready(monkeypatch)
    discovery.return_value = (['configured-xdr'], 'sangfor_xdr_incidents', None)
    original = scheduling.persist_execution
    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError('synthetic failure after queue insert')
    monkeypatch.setattr(scheduling, 'persist_execution', interrupted)
    with pytest.raises(HTTPException) as error:
        await api.start(user=OWNER)
    assert error.value.status_code == 500
    assert not await rows('SELECT * FROM task_executions')
    assert not await rows('SELECT * FROM task_execution_queue_refs')
    assert (await rows('SELECT activation_pending FROM monitor_installations'))[0]['activation_pending'] == scheduling.IMMEDIATE_START_PENDING
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    monkeypatch.setattr(scheduling, 'persist_execution', original)
    await lifecycle.reconcile()
    await lifecycle.reconcile()
    await api.start(user=OWNER)
    assert len(await rows('SELECT * FROM task_executions')) == 1
    assert len(await rows('SELECT * FROM task_execution_queue_refs')) == 1
    assert (await rows('SELECT activation_pending FROM monitor_installations'))[0]['activation_pending'] == 0


async def test_pause_cancels_pending_start_intent_before_recovery(monkeypatch):
    discovery, entry = await install_unready(monkeypatch)
    discovery.return_value = (['configured-xdr'], 'sangfor_xdr_incidents', None)
    original = lifecycle.start_immediately
    monkeypatch.setattr(lifecycle, 'start_immediately', AsyncMock(side_effect=RuntimeError('interrupted')))
    with pytest.raises(HTTPException):
        await api.start(user=OWNER)
    await api.pause(user=OWNER)
    monkeypatch.setattr(lifecycle, 'start_immediately', original)
    await lifecycle.reconcile()
    assert not await rows('SELECT * FROM task_executions')
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED


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


async def test_engine_switch_requires_pause_preserves_records_and_updates_both_policies(monkeypatch):
    discovery, entry = await install_unready(monkeypatch)
    discovery.return_value = (['configured-xdr'], 'sangfor_xdr_incidents', None)
    await api.start(user=OWNER)
    with pytest.raises(HTTPException) as error:
        await api.investigation_engine(api.InvestigationEngineRequest(engine='rules'), user=OWNER)
    assert error.value.status_code == 409
    await api.pause(user=OWNER)
    reply = await api.investigation_engine(api.InvestigationEngineRequest(engine='rules'), user=OWNER)
    assert reply['investigationEngine'] == 'rules' and reply['roundTimeoutSeconds'] == 480
    installation = (await rows('SELECT * FROM monitor_installations'))[0]
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    assert scheduler.context['monitoring'] == json.loads(installation['policy'])
    with pytest.raises(HTTPException) as error:
        await api.investigation_engine(api.InvestigationEngineRequest(engine='agent-v1'), user=SimpleNamespace(id='other'))
    assert error.value.status_code == 404


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
