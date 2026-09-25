"""Exercise real Hub installation under deployed workspace/path policies."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.hub import installer, local
from flocks.monitoring import lifecycle
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.reports import export_report
from flocks.monitoring.store import rows, write
from flocks.project.project import Project
from flocks.task.models import SchedulerStatus
from flocks.task.store import TaskStore
from flocks.workspace.manager import WorkspaceManager


def linked_workspace(tmp_path, monkeypatch):
    home, target = tmp_path / 'home', tmp_path / 'data-volume'
    home.mkdir()
    target.mkdir()
    (home / '.flocks').symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
    monkeypatch.setenv('FLOCKS_ROOT', str(home / '.flocks'))
    monkeypatch.setenv('FLOCKS_WORKSPACE_DIR', str(home / '.flocks/workspace'))
    monkeypatch.setattr(WorkspaceManager, '_instance', None)
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['fixture-xdr'], 'sangfor_xdr_incidents', None)))
    return home, target


async def installation():
    entry = (await rows('SELECT * FROM monitor_installations'))[0]
    return entry, json.loads(entry['policy'])


@pytest.mark.asyncio
async def test_symlinked_user_root_installs_and_reinstalls_without_duplicate_projects(tmp_path, monkeypatch):
    home, target = linked_workspace(tmp_path, monkeypatch)
    assert not (target / 'workspace').exists()
    await installer.install_plugin('component', COMPONENT_ID)
    entry, policy = await installation()
    assert Path(policy['directory']).is_relative_to(target / 'workspace/monitoring')
    assert Path(policy['directory']).is_dir()
    assert (await TaskStore.get_scheduler(entry['scheduler_id'])).status == SchedulerStatus.ACTIVE
    # An old policy can retain the lexical symlink path; reinstall canonicalizes
    # it without switching the existing project or its scheduler identity.
    policy['directory'] = str(home / '.flocks/workspace/monitoring' / Path(policy['directory']).name)
    await write('UPDATE monitor_installations SET policy=?', (json.dumps(policy),))
    await installer.install_plugin('component', COMPONENT_ID)
    again, current = await installation()
    assert (again['project'], again['scheduler_id']) == (entry['project'], entry['scheduler_id'])
    assert len(await Project.list(owner_id='owner')) == 1
    assert current['directory'] == str(Path(policy['directory']).resolve())
    # Trust is scoped to workspace, not the whole symlink target/user data root.
    forbidden = target / 'private/project'
    with pytest.raises(ValueError, match='outside the allowed roots'):
        await Project.create(owner_id='owner', name='Forbidden', worktree=str(forbidden))
    assert not forbidden.exists()


@pytest.mark.asyncio
async def test_configured_workspace_used_for_project_and_report(tmp_path, monkeypatch):
    home, _ = linked_workspace(tmp_path, monkeypatch)
    workspace = tmp_path / 'configured-workspace'
    monkeypatch.setenv('FLOCKS_WORKSPACE_DIR', str(workspace))
    await installer.install_plugin('component', COMPONENT_ID)
    _, policy = await installation()
    assert Path(policy['directory']).is_relative_to(workspace / 'monitoring')
    await write("INSERT INTO monitor_reports(owner,scope,business_date,status) VALUES(?,?,?,'pending')", ('owner', COMPONENT_ID, '2026-01-01'))
    await export_report('owner', COMPONENT_ID, '2026-01-01')
    assert len(list((workspace / 'outputs/2026-01-01').glob('host-security-monitor-*.md'))) == 2
    assert not (home / '.flocks/workspace/outputs').exists()


@pytest.mark.asyncio
async def test_explicit_restriction_rejected_before_first_package_mutation(tmp_path, monkeypatch):
    home, target = linked_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv('FLOCKS_PROJECT_ROOTS', str(home))
    copy = Mock(wraps=installer._copy_package)
    monkeypatch.setattr(installer, '_copy_package', copy)
    with pytest.raises(ValueError, match='FLOCKS_PROJECT_ROOTS'):
        await installer.install_plugin('component', COMPONENT_ID)
    copy.assert_not_called()
    assert local.get_record('component', COMPONENT_ID) is None
    assert not await rows('SELECT * FROM monitor_installations')
    assert not await rows('SELECT * FROM task_schedulers')
    assert not (target / 'workspace').exists()
    lifecycle.discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_allowed_workspace_accepts_symlink(tmp_path, monkeypatch):
    _, target = linked_workspace(tmp_path, monkeypatch)
    workspace = target / 'workspace'
    workspace.mkdir()
    monkeypatch.setenv('FLOCKS_PROJECT_ROOTS', str(workspace))
    await installer.install_plugin('component', COMPONENT_ID)
    _, policy = await installation()
    assert Path(policy['directory']).is_relative_to(workspace)


@pytest.mark.asyncio
async def test_monitoring_child_symlink_cannot_escape_workspace(tmp_path, monkeypatch):
    _, target = linked_workspace(tmp_path, monkeypatch)
    workspace = target / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'monitoring').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='不在允许范围内'):
        await installer.install_plugin('component', COMPONENT_ID)
    assert not list(outside.iterdir())
    assert local.get_record('component', COMPONENT_ID) is None


@pytest.mark.asyncio
async def test_preflight_failure_keeps_existing_package_and_active_monitor(tmp_path, monkeypatch):
    home, _ = linked_workspace(tmp_path, monkeypatch)
    record = await installer.install_plugin('component', COMPONENT_ID)
    before, _ = await installation()
    manifest = Path(record.installPath) / 'monitoring.json'
    content = manifest.read_bytes()
    monkeypatch.setenv('FLOCKS_PROJECT_ROOTS', str(home))
    backup = Mock(wraps=installer.create_backup)
    monkeypatch.setattr(installer, 'create_backup', backup)
    with pytest.raises(ValueError, match='FLOCKS_PROJECT_ROOTS'):
        await installer.install_plugin('component', COMPONENT_ID)
    backup.assert_not_called()
    assert manifest.read_bytes() == content
    assert local.get_record('component', COMPONENT_ID) == record
    after, _ = await installation()
    assert after == before
    assert (await TaskStore.get_scheduler(before['scheduler_id'])).status == SchedulerStatus.ACTIVE


@pytest.mark.asyncio
async def test_existing_same_named_user_project_does_not_block_install(tmp_path, monkeypatch):
    _, target = linked_workspace(tmp_path, monkeypatch)
    ordinary = await Project.create(owner_id='owner', name='安全运营监测', worktree=str(target / 'workspace/user-project'))
    await installer.install_plugin('component', COMPONENT_ID)
    entry, policy = await installation()
    assert entry['project'] != ordinary.id
    assert policy['directory'] != ordinary.worktree
    assert len(await Project.list(owner_id='owner')) == 2
    await installer.install_plugin('component', COMPONENT_ID)
    assert len(await Project.list(owner_id='owner')) == 2


@pytest.mark.asyncio
async def test_workspace_setting_change_keeps_existing_project(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=([], None, '未配置 XDR')))
    await installer.install_plugin('component', COMPONENT_ID)
    before, policy = await installation()
    changed = tmp_path / 'new-workspace'
    monkeypatch.setenv('FLOCKS_WORKSPACE_DIR', str(changed))
    monkeypatch.setattr(WorkspaceManager, '_instance', None)
    await installer.install_plugin('component', COMPONENT_ID)
    after, current = await installation()
    assert (after['project'], after['scheduler_id'], current['directory']) == (before['project'], before['scheduler_id'], policy['directory'])
    assert not changed.exists()


@pytest.mark.asyncio
async def test_monitor_cleanup_error_does_not_prevent_package_rollback(tmp_path, monkeypatch):
    linked_workspace(tmp_path, monkeypatch)
    record = await installer.install_plugin('component', COMPONENT_ID)
    declaration = Path(record.installPath) / 'monitoring.json'
    original = declaration.read_bytes()

    async def fail_after_replacement():
        declaration.write_text('failed replacement')
        raise RuntimeError('injected registration failure')

    monkeypatch.setattr(lifecycle, 'discover', fail_after_replacement)
    monkeypatch.setattr(lifecycle, 'compensate_install_failure', AsyncMock(side_effect=RuntimeError('injected cleanup failure')))
    with pytest.raises(RuntimeError, match='监测登记清理未完成'):
        await installer.install_plugin('component', COMPONENT_ID)
    assert declaration.read_bytes() == original
    assert local.get_record('component', COMPONENT_ID) == record
