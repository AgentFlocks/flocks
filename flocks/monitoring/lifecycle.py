"""Hub lifecycle integration. Merely shipping the package never enables it."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from flocks.auth.context import get_current_auth_user
from flocks.hub import local
from flocks.project.project import Project, ProjectPathConflictError
from flocks.task.manager import TaskManager
from flocks.task.models import SchedulerStatus
from flocks.task.plugin_models import TaskSpec
from flocks.task.plugin_sync import upsert_task_specs
from flocks.task.store import TaskStore
from flocks.workspace.manager import WorkspaceManager
from .models import COMPONENT_ID, MonitoringPolicy, MonitoringDeclaration
from .store import rows, write, encode
from .adapter import discover

_lock = asyncio.Lock()
CORE_CAPABILITIES = {'monitor.daily.v1', 'monitor.readonly.v1', 'monitor.native-messages.v1', 'monitor.confirmed-disposition.v1', 'monitor.automatic-status.v1'}


def validate_manifest(manifest, package=None):
    required = set((manifest.model_extra or {}).get('requiredCoreCapabilities', []))
    if not required or not required <= CORE_CAPABILITIES:
        raise ValueError('Monitoring component requires unsupported core capabilities')
    import os
    if int(os.environ.get('WEB_CONCURRENCY', '1')) != 1:
        raise ValueError('安全运营监测阶段一仅支持单后端执行进程')
    if package is not None:
        MonitoringDeclaration.model_validate_json((Path(package) / 'monitoring.json').read_text())


async def install(manifest):
    record = local.get_record('component', COMPONENT_ID)
    if not record:
        raise ValueError('监测套件尚未安装')
    package = Path(record.installPath)
    validate_manifest(manifest, package)
    declaration = MonitoringDeclaration.model_validate_json((package / 'monitoring.json').read_text())
    user = get_current_auth_user()
    if user is None:
        raise ValueError('安装监测套件需要已登录的拥有者')
    async with _lock:
        old = await rows('SELECT * FROM monitor_installations WHERE owner=? AND scope=?', (user.id, COMPONENT_ID))
        directory = await preflight_install()
        projects = await Project.list(owner_id=user.id)
        names = {project.name.casefold() for project in projects}
        name, suffix = '安全运营监测', 1
        while name.casefold() in names:
            suffix += 1
            name = f'安全运营监测 ({suffix})'
        try:
            # Project.create checks canonical allowed roots before mkdir.
            project = await Project.create(owner_id=user.id, name=name, worktree=str(directory))
        except ProjectPathConflictError as exc:
            project = exc.project
        directory = Path(project.worktree)
        devices, tool, reason = await discover()
        policy = MonitoringPolicy(owner=user.id, project=project.id, directory=str(directory),
                                  devices=devices, tool=tool or 'sangfor_xdr_incidents')
        key = f'monitor:{user.id}:{project.id}:{COMPONENT_ID}'
        existing = await TaskStore.get_scheduler_by_dedup_key(key)
        was_disabled = bool(old and old[0]['installed'] and old[0]['ready'] and existing
                            and existing.status != SchedulerStatus.ACTIVE)
        if existing:
            await TaskManager.disable_scheduler(existing.id)
            for execution in await TaskStore.list_active_executions_for_scheduler(existing.id):
                await TaskManager.cancel_execution(execution.id)
        await upsert_task_specs([TaskSpec(
            dedup_key=key, title='安全运营监测', description='每十分钟筛选与分析；启用自动标记后按证据写回状态并回查',
            cron=declaration.cron, enabled=False, timezone=policy.timezone,
            context={'monitoring': policy.model_dump()}, tags=['monitoring', COMPONENT_ID],
        )])
        scheduler = await TaskStore.get_scheduler_by_dedup_key(key)
        if scheduler is None:
            raise RuntimeError('监测调度注册失败')
        # Installation gate is committed before activation; a crash leaves a
        # disabled definition which startup reconciliation can safely resume.
        await write('INSERT INTO monitor_installations(owner,scope,project,policy,installed,ready,reason,scheduler_id,activation_pending) VALUES(?,?,?,?,1,?,?,?,?) ON CONFLICT(owner,scope) DO UPDATE SET project=excluded.project,policy=excluded.policy,installed=1,ready=excluded.ready,reason=excluded.reason,scheduler_id=excluded.scheduler_id,activation_pending=excluded.activation_pending',
                    (user.id, COMPONENT_ID, project.id, encode(policy.model_dump()), int(not reason), reason, scheduler.id, int(not reason and not was_disabled)))
        if not reason and not was_disabled:
            scheduler.status = SchedulerStatus.ACTIVE
            await TaskStore.update_scheduler(scheduler)
            await write('UPDATE monitor_installations SET activation_pending=0 WHERE owner=? AND scope=?', (user.id, COMPONENT_ID))


async def preflight_install():
    """Check the target before replacing a package; never create directories here."""
    user = get_current_auth_user()
    if user is None:
        raise ValueError('安装监测套件需要已登录的拥有者')
    old = await rows('SELECT policy FROM monitor_installations WHERE owner=? AND scope=?', (user.id, COMPONENT_ID))
    if old:
        # Keep the original project identity and history on reinstall. A changed
        # workspace configuration is not authorization to migrate an old project.
        directory = Path(MonitoringPolicy.model_validate_json(old[0]['policy']).directory)
    else:
        digest = hashlib.sha256(user.id.encode()).hexdigest()[:16]
        directory = WorkspaceManager.get_instance().get_workspace_dir() / 'monitoring' / digest
    try:
        directory = directory.expanduser().resolve()
        roots = Project.allowed_roots()
        if not roots or not any(directory == root or directory.is_relative_to(root) for root in roots):
            raise ValueError('监测项目目录不在允许范围内，请检查 FLOCKS_WORKSPACE_DIR 与 FLOCKS_PROJECT_ROOTS 的配置及软链接真实位置')
        ancestor = directory
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if not ancestor.is_dir() or not os.access(ancestor, os.R_OK | os.W_OK | os.X_OK):
            raise ValueError('监测项目目录不可创建或写入，请检查工作区目录权限')
        if directory.exists():
            Project.validate_worktree(str(directory))
    except (OSError, RuntimeError) as exc:
        raise ValueError('监测项目目录无法解析，请检查工作区路径及软链接') from exc
    return directory


async def uninstall():
    async with _lock:
        for entry in await rows('SELECT * FROM monitor_installations WHERE scope=?', (COMPONENT_ID,)):
            await write('UPDATE monitor_installations SET installed=0,ready=0,activation_pending=0,reason=? WHERE owner=? AND scope=?',
                        ('已卸载', entry['owner'], COMPONENT_ID))
            await write('UPDATE monitor_auto_settings SET enabled=0 WHERE owner=? AND scope=?', (entry['owner'], COMPONENT_ID))
            if entry['scheduler_id']:
                await TaskManager.disable_scheduler(entry['scheduler_id'])
                # Disable and cancel every queued/running execution; cancellation
                # completion is awaited by the monitor dispatch wrapper.
                active = await TaskStore.list_active_executions_for_scheduler(entry['scheduler_id'])
                for execution in active:
                    await TaskManager.cancel_execution(execution.id)


async def _pause_installed():
    for entry in await rows('SELECT scheduler_id FROM monitor_installations WHERE scope=? AND installed=1', (COMPONENT_ID,)):
        await write('UPDATE monitor_installations SET activation_pending=0 WHERE scheduler_id=?', (entry['scheduler_id'],))
        if entry['scheduler_id']:
            await TaskManager.disable_scheduler(entry['scheduler_id'])
            for execution in await TaskStore.list_active_executions_for_scheduler(entry['scheduler_id']):
                await TaskManager.cancel_execution(execution.id)


async def _owned_installation(owner):
    entries = await rows('SELECT * FROM monitor_installations WHERE owner=? AND scope=? AND installed=1', (owner, COMPONENT_ID))
    if not entries:
        raise FileNotFoundError('请先在添加场景中安装安全运营监测')
    entry = entries[0]
    scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
    if scheduler is None:
        raise ValueError('监测任务缺失，请重新安装场景')
    policy = MonitoringPolicy.model_validate_json(entry['policy'])
    if policy.owner != owner:
        raise ValueError('监测任务归属不一致，请重新安装场景')
    return entry, scheduler, policy


async def _pause_monitor(entry):
    await write('UPDATE monitor_installations SET activation_pending=0 WHERE owner=? AND scope=?', (entry['owner'], COMPONENT_ID))
    await TaskManager.disable_scheduler(entry['scheduler_id'])
    for execution in await TaskStore.list_active_executions_for_scheduler(entry['scheduler_id']):
        await TaskManager.cancel_execution(execution.id)


async def start_monitoring(owner):
    """Recheck local capabilities and arm the next slot; never probe a device here."""
    async with _lock:
        record = local.get_record('component', COMPONENT_ID)
        if not record:
            raise FileNotFoundError('请先在添加场景中安装安全运营监测')
        if not record.enabled:
            raise ValueError('场景已停用，请先在添加场景中启用')
        entry, scheduler, policy = await _owned_installation(owner)
        if entry['ready'] and scheduler.status == SchedulerStatus.ACTIVE:
            return  # Retries cannot reset the schedule or duplicate work.
        await _pause_monitor(entry)
        await write('UPDATE monitor_installations SET ready=0,reason=? WHERE owner=? AND scope=?', ('接入检查尚未完成，请重试启动', owner, COMPONENT_ID))
        from flocks.hub.catalog import load_manifest
        validate_manifest(load_manifest('component', COMPONENT_ID), Path(record.installPath))
        devices, tool, reason = await discover()
        if not Path(policy.directory).is_dir():
            reason = '监测工作目录不可用，请恢复目录或重新安装场景'
        if reason:
            await write('UPDATE monitor_installations SET reason=? WHERE owner=? AND scope=?', (reason, owner, COMPONENT_ID))
            raise ValueError(reason)
        policy.devices = devices
        policy.tool = tool
        scheduler.status = SchedulerStatus.DISABLED
        scheduler.context = {**scheduler.context, 'monitoring': policy.model_dump()}
        await TaskStore.update_scheduler(scheduler)
        await write('UPDATE monitor_installations SET policy=?,ready=1,reason=NULL,activation_pending=1 WHERE owner=? AND scope=?', (encode(policy.model_dump()), owner, COMPONENT_ID))
        await TaskManager.enable_scheduler(scheduler.id)
        await write('UPDATE monitor_installations SET activation_pending=0 WHERE owner=? AND scope=?', (owner, COMPONENT_ID))


async def pause_monitoring(owner):
    async with _lock:
        entry, _, _ = await _owned_installation(owner)
        await _pause_monitor(entry)


async def set_scene_enabled(enabled):
    """Scene visibility is separate from the monitoring task's readiness/pause."""
    async with _lock:
        record = local.get_record('component', COMPONENT_ID)
        if not record:
            raise FileNotFoundError('请先安装安全运营监测场景')
        record.enabled = enabled
        local.save_installed_record(record)
        if not enabled:
            await _pause_installed()
        # Showing a scene must not silently resume a user-paused background task.


async def reconcile():
    record = local.get_record('component', COMPONENT_ID)
    if not record:
        await uninstall()
        return
    if not record.enabled:
        await _pause_installed()
        return
    # Never scan bundled source for runnable tasks. Recheck installed records.
    for entry in await rows('SELECT * FROM monitor_installations WHERE installed=1'):
        if not entry['ready']:
            continue  # missing capability remains explicitly not ready
        policy = MonitoringPolicy.model_validate_json(entry['policy'])
        if entry['activation_pending']:
            scheduler = await TaskStore.get_scheduler(entry['scheduler_id'])
            if scheduler:
                await TaskManager.enable_scheduler(scheduler.id)
                await write('UPDATE monitor_installations SET activation_pending=0 WHERE owner=? AND scope=?', (policy.owner, policy.scope))
        if not Path(policy.directory).is_dir():
            await TaskManager.disable_scheduler(entry['scheduler_id'])
            await write('UPDATE monitor_installations SET ready=0,reason=? WHERE owner=? AND scope=?',
                        ('专用项目路径不可用', policy.owner, policy.scope))


async def compensate_install_failure(was_installed):
    """Fail closed for the current owner; keep other owners' installations intact."""
    user = get_current_auth_user()
    if user is None:
        return
    entries = await rows('SELECT scheduler_id FROM monitor_installations WHERE owner=? AND scope=?', (user.id, COMPONENT_ID))
    for entry in entries:
        await write('UPDATE monitor_installations SET installed=?,ready=0,activation_pending=0,reason=? WHERE owner=? AND scope=?',
                    (int(was_installed), '安装未完成，请重新安装', user.id, COMPONENT_ID))
        if entry['scheduler_id']:
            await TaskManager.disable_scheduler(entry['scheduler_id'])
            for execution in await TaskStore.list_active_executions_for_scheduler(entry['scheduler_id']):
                await TaskManager.cancel_execution(execution.id)
