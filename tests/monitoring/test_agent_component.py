"""Suite-owned agent lifecycle, migration and shared dependency protection."""
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks.hub import installer, local
from flocks.monitoring import agent_component as component, lifecycle
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import rows, write
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionTriggerType, SchedulerStatus, TaskStatus
from flocks.task.store import TaskStore


@pytest.fixture
def device(monkeypatch):
    from flocks.monitoring import capabilities
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['fixture-xdr'], 'sangfor_xdr_incidents', None)))
    monkeypatch.setattr(capabilities, 'discover', AsyncMock(return_value=([], [])))


async def test_suite_installs_owned_agent_and_uninstalls_payload_but_keeps_business_records(device):
    await installer.install_plugin('component', COMPONENT_ID)
    record = local.get_record('agent', component.AGENT_ID)
    assert record.installedBy == f'component:{COMPONENT_ID}'
    assert Path(record.installPath, 'agent.yaml').is_file()
    assert (await component.resolve()).name == component.AGENT_ID
    assert component.diagnostic_state() == {'installed': True, 'enabled': True, 'version': '1.0.0',
                                            'required_contract': 1, 'definition_valid': True}
    # No built-in definition can mask a component update.
    from flocks.agent.agent_factory import scan_and_load, _BUILTIN_AGENTS_DIR
    assert not (_BUILTIN_AGENTS_DIR / 'security_monitor/agent.yaml').exists()
    assert component.AGENT_ID in scan_and_load(dirs=[Path(record.installPath).parent])
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    await write('INSERT INTO monitor_investigations(owner,project,event_key,event,updated_at) VALUES(?,?,?,?,?)',
                ('owner', entry['project'], 'original-event', '{}', '2026-09-25'))
    await installer.uninstall_plugin('component', COMPONENT_ID)
    assert local.get_record('agent', component.AGENT_ID) is None
    assert not Path(record.installPath).exists()
    assert len(await rows('SELECT * FROM monitor_investigations')) == 1
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    await installer.install_plugin('component', COMPONENT_ID)
    reinstalled = (await rows('SELECT * FROM monitor_installations'))[0]
    assert (reinstalled['project'], reinstalled['scheduler_id']) == (entry['project'], entry['scheduler_id'])
    assert len(await rows('SELECT * FROM monitor_investigations')) == 1


async def test_old_install_without_agent_pauses_and_recovers_only_after_suite_update(device):
    await installer.install_plugin('component', COMPONENT_ID)
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    agent_record = local.get_record('agent', component.AGENT_ID)
    # Mimic the earlier release, whose suite had no child and role was built-in.
    shutil.rmtree(agent_record.installPath)
    local.remove_installed_record('agent', component.AGENT_ID)
    await lifecycle.reconcile()
    current = (await rows('SELECT * FROM monitor_installations'))[0]
    assert current['installed'] and not current['ready'] and '更新' in current['reason']
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    with pytest.raises(ValueError, match='未安装'):
        await lifecycle.start_monitoring('owner')
    await installer.install_plugin('component', COMPONENT_ID)
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.DISABLED
    await lifecycle.start_monitoring('owner')
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.ACTIVE


async def test_removing_agent_cancels_pending_round_and_does_not_fall_back_to_cached_role(device, monkeypatch):
    await installer.install_plugin('component', COMPONENT_ID)
    await lifecycle.pause_monitoring('owner')
    await lifecycle.start_monitoring('owner')
    execution = (await rows("SELECT * FROM task_executions WHERE status='queued'"))[0]
    await installer.uninstall_plugin('agent', component.AGENT_ID)
    assert (await TaskStore.get_execution(execution['id'])).status == TaskStatus.CANCELLED
    assert not (await rows('SELECT ready FROM monitor_installations'))[0]['ready']
    from flocks.monitoring import investigation
    monkeypatch.setattr(investigation.Agent, 'get', AsyncMock(side_effect=AssertionError('stale role must not be used')))
    with pytest.raises(ValueError, match='未安装'):
        await component.resolve()
    with pytest.raises(investigation.ContractError, match='未安装'):
        await investigation.choose(component.AGENT_ID, {})


async def test_updating_shared_agent_preserves_ownership_and_does_not_remove_it_with_suite(device):
    standalone = await installer.install_plugin('agent', component.AGENT_ID)
    local.save_installed_record(standalone.model_copy(update={'version': '0.9.0'}))
    await installer.install_plugin('component', COMPONENT_ID)
    updated = local.get_record('agent', component.AGENT_ID)
    assert updated.version == '1.0.0' and updated.installedBy is None
    await installer.uninstall_plugin('component', COMPONENT_ID)
    assert local.get_record('agent', component.AGENT_ID) is not None
    assert (await component.resolve()).name == component.AGENT_ID


async def test_failed_parent_install_rolls_back_new_agent(device, monkeypatch):
    monkeypatch.setattr(lifecycle, 'install', AsyncMock(side_effect=RuntimeError('synthetic registration failure')))
    with pytest.raises(RuntimeError, match='synthetic registration failure'):
        await installer.install_plugin('component', COMPONENT_ID)
    assert local.get_record('component', COMPONENT_ID) is None
    assert local.get_record('agent', component.AGENT_ID) is None
    assert not local.install_dir('agent', component.AGENT_ID).exists()


async def test_agent_payload_is_live_and_incompatible_contract_blocks_before_queries(device, monkeypatch):
    await installer.install_plugin('component', COMPONENT_ID)
    record = local.get_record('agent', component.AGENT_ID)
    prompt = Path(record.installPath, 'prompt.md')
    prompt.write_text('测试：使用套件更新后的方法。')
    assert (await component.resolve()).prompt == prompt.read_text()
    yaml = Path(record.installPath, 'agent.yaml')
    yaml.write_text(yaml.read_text().replace('monitorInvestigationContract: 1', 'monitorInvestigationContract: 99'))
    await lifecycle.reconcile()
    assert '不兼容' in (await rows('SELECT reason FROM monitor_installations'))[0]['reason']
    assert component.diagnostic_state()['definition_valid'] is False
    with pytest.raises(ValueError, match='不兼容'):
        await lifecycle.start_monitoring('owner')


async def test_agent_update_drains_active_round_before_replacing_payload(device, monkeypatch):
    await installer.install_plugin('component', COMPONENT_ID)
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=True)
    copy = installer._copy_package
    def check_drained(src, dst, **kwargs):
        assert drained['done']
        return copy(src, dst, **kwargs)
    original = lifecycle.pause_for_agent_change
    drained = {'done': False}
    async def pause(**kwargs):
        await original(**kwargs)
        assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED
        assert (await TaskStore.get_execution(execution.id)).status == TaskStatus.CANCELLED
        drained['done'] = True
    monkeypatch.setattr(lifecycle, 'pause_for_agent_change', pause)
    monkeypatch.setattr(installer, '_copy_package', check_drained)
    await installer.update_plugin('agent', component.AGENT_ID)
    assert drained['done'] and (await component.resolve()).name == component.AGENT_ID
    assert (await TaskStore.get_scheduler(scheduler.id)).status == SchedulerStatus.DISABLED


async def test_user_disabled_agent_is_not_resurrected_by_suite(device, monkeypatch):
    from flocks.config.config import Config, AgentConfig
    await installer.install_plugin('component', COMPONENT_ID)
    cfg = (await Config.get()).model_copy(update={'agent': {component.AGENT_ID: AgentConfig(disable=True)}})
    monkeypatch.setattr(Config, 'get', AsyncMock(return_value=cfg))
    with pytest.raises(ValueError, match='配置停用'):
        await component.resolve()
    await lifecycle.reconcile()
    assert not (await rows('SELECT ready FROM monitor_installations'))[0]['ready']


async def test_installed_prompt_cannot_escape_package_root(device, tmp_path):
    await installer.install_plugin('component', COMPONENT_ID)
    record = local.get_record('agent', component.AGENT_ID)
    outside = tmp_path / 'outside-prompt.txt'
    outside.write_text('private fixture contents')
    prompt = Path(record.installPath, 'prompt.md')
    prompt.unlink()
    prompt.symlink_to(outside)
    with pytest.raises(ValueError, match='组件损坏'):
        await component.resolve()
    assert not component.diagnostic_state()['definition_valid']
