"""Device-specific monitoring targets, saved atomically with scheduler policy."""
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from . import adapter, mailflow
from .models import COMPONENT_ID
from .store import connection, encode, rows


class DeviceTargetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # The device name comes from device access; clients only send the stable ID.
    device_id: str = Field(min_length=1, max_length=200)
    responsible_name: str = Field(min_length=1, max_length=80)
    recipient_email: str = Field(min_length=1, max_length=254)

    @field_validator('recipient_email')
    @classmethod
    def email(cls, value):
        value = mailflow.MailSettingsRequest.email(value)
        if not value:
            raise ValueError('请填写责任人邮箱')
        return value

    @field_validator('responsible_name')
    @classmethod
    def name(cls, value):
        value = value.strip()
        if not value or any(ch in value for ch in '\r\n'):
            raise ValueError('请填写责任人名称')
        return value


class ConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    targets: list[DeviceTargetRequest] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_devices(self):
        if len({target.device_id for target in self.targets}) != len(self.targets):
            raise ValueError('同一设备只能配置一次')
        return self


async def selected_targets(policy):
    return await rows('SELECT * FROM monitor_device_targets WHERE owner=? AND scope=? AND project=? ORDER BY device',
                      (policy.owner, policy.scope, policy.project))


async def resolve_targets(policy):
    """Re-resolve saved IDs at start/reinstall, never enroll a new device implicitly."""
    targets = await selected_targets(policy)
    catalog = {device['id']: device for device in await adapter.device_catalog()}
    if not targets:
        return '请在监测配置中选择 XDR 设备并填写责任人邮箱'
    unavailable = [catalog.get(target['device'], {}).get('name') or target['device_name'] for target in targets
                   if not catalog.get(target['device'], {}).get('available')]
    if unavailable:
        return '监测设备不可用，请检查设备接入：' + '、'.join(unavailable)
    policy.devices = [target['device'] for target in targets]
    policy.device_tools = {device: catalog[device]['tool'] for device in policy.devices}
    policy.device_names = {device: catalog[device]['name'] for device in policy.devices}
    policy.tool = policy.device_tools[policy.devices[0]]
    return None


async def snapshot(owner):
    from .lifecycle import _owned_installation
    from flocks.task.models import SchedulerStatus
    from flocks.task.store import TaskStore
    _, scheduler, policy = await _owned_installation(owner)
    config = await mailflow.settings(owner)
    catalog = await adapter.device_catalog()
    by_id = {device['id']: device for device in catalog}
    targets = await selected_targets(policy) if policy.targets_configured else []
    if not policy.targets_configured and len(policy.devices) == 1:
        targets = [{'device': policy.devices[0], 'device_name': policy.device_names.get(policy.devices[0], policy.devices[0]),
                    'recipient': config['recipient'], 'responsible_name': config['responsible_name']}]
    return {
        'enabled': bool(config['enabled']),
        'running': scheduler.status == SchedulerStatus.ACTIVE or bool(await TaskStore.list_active_executions_for_scheduler(scheduler.id)),
        'targets': [{'device_id': target['device'], 'device_name': by_id.get(target['device'], {}).get('name', target['device_name']),
                     'responsible_name': target['responsible_name'], 'recipient_email': target['recipient'],
                     'available': bool(by_id.get(target['device'], {}).get('available')),
                     'reason': by_id.get(target['device'], {}).get('reason', '设备已删除或不再是 XDR 接入')}
                    for target in targets],
        'devices': [{key: device[key] for key in ('id', 'name', 'available', 'reason')} for device in catalog],
        'sender_verification_required': mailflow.transport.REQUIRE_AUTHENTICATED_FEEDBACK,
    }


async def configure(owner, body):
    from . import lifecycle
    from .disposition import stamp
    from flocks.config.config import Config
    from flocks.session.interaction_policy import require_interactive
    from flocks.task.models import SchedulerStatus
    from flocks.task.store import TaskStore
    from flocks.tool.registry import ToolRegistry
    await require_interactive()
    # Same order as lifecycle then mail paths; save cannot race Start or a send.
    async with lifecycle._lock, mailflow.lock(owner):
        _, scheduler, policy = await lifecycle._owned_installation(owner)
        if scheduler.status == SchedulerStatus.ACTIVE or await TaskStore.list_active_executions_for_scheduler(scheduler.id):
            raise ValueError('请先暂停监测，再保存设备与责任人配置')
        catalog = {device['id']: device for device in await adapter.device_catalog()}
        for target in body.targets:
            device = catalog.get(target.device_id)
            if not device or not device['available']:
                raise ValueError('所选 XDR 设备不可用：' + (device['name'] + '；' + str(device['reason']) if device else target.device_id))
        current = await mailflow.settings(owner)
        existing = {target['device']: target for target in await selected_targets(policy)}
        mailbox = current['mailbox']
        if body.enabled:
            if not await Config.resolve_default_llm():
                raise ValueError('请先配置自然语言解读模型')
            for target in body.targets:
                _, cfg = mailflow.transport.transport(target.recipient_email)
                mailbox = mailflow.transport.mailbox_key(cfg)
                tool = ToolRegistry.get(catalog[target.device_id]['tool'])
                if tool is None or not {'uuids', 'deal_status', 'deal_comment'} <= tool.info.get_schema().to_json_schema().get('properties', {}).keys():
                    raise ValueError(catalog[target.device_id]['name'] + '：XDR 工具缺少状态标记字段')
        timestamp = stamp()
        records = []
        for target in body.targets:
            previous = existing.get(target.device_id)
            # Preserve one-device mail history on the first explicit save.
            if not policy.targets_configured and policy.devices == [target.device_id]:
                previous = current
            same = previous and previous['recipient'] == target.recipient_email and previous['mailbox'] == mailbox and previous['project'] == policy.project
            revision = previous['revision'] if same else str(uuid4())
            records.append((owner, COMPONENT_ID, policy.project, target.device_id, catalog[target.device_id]['name'],
                            target.recipient_email, target.responsible_name, revision, mailbox, timestamp))
        policy.targets_configured = True
        policy.devices = [target.device_id for target in body.targets]
        policy.device_tools = {device: catalog[device]['tool'] for device in policy.devices}
        policy.device_names = {device: catalog[device]['name'] for device in policy.devices}
        policy.tool = policy.device_tools[policy.devices[0]]
        context = {**scheduler.context, 'monitoring': policy.model_dump()}
        global_revision = current['revision'] if current['project'] == policy.project and current['mailbox'] == mailbox else str(uuid4())
        async with connection() as db:
            await db.execute('BEGIN IMMEDIATE')
            for target in body.targets:
                conflict = await db.execute('SELECT 1 FROM monitor_mail_settings WHERE mailbox=? AND recipient=? AND owner!=? '
                    'UNION SELECT 1 FROM monitor_device_targets WHERE mailbox=? AND recipient=? AND owner!=? '
                    'UNION SELECT 1 FROM monitor_mail_notices WHERE mailbox=? AND recipient=? AND owner!=? LIMIT 1',
                    (mailbox, target.recipient_email, owner) * 3)
                if mailbox and await conflict.fetchone():
                    raise ValueError('该邮箱已关联其他拥有者的监测，请使用独立责任人邮箱')
            await db.execute('DELETE FROM monitor_device_targets WHERE owner=? AND scope=? AND project=?', (owner, COMPONENT_ID, policy.project))
            await db.executemany('INSERT INTO monitor_device_targets VALUES(?,?,?,?,?,?,?,?,?,?)', records)
            # Keep the shared channel switch/cursor, with no global recipient fallback.
            await db.execute('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,scope) DO UPDATE SET '
                'project=excluded.project,recipient=excluded.recipient,responsible_name=excluded.responsible_name,enabled=excluded.enabled,'
                'revision=excluded.revision,mailbox=excluded.mailbox,updated_at=excluded.updated_at',
                (owner, COMPONENT_ID, policy.project, '', '', int(body.enabled), global_revision, mailbox, timestamp))
            await db.execute('UPDATE monitor_installations SET policy=?,activation_pending=0 WHERE owner=? AND scope=?',
                             (encode(policy.model_dump()), owner, COMPONENT_ID))
            await db.execute('UPDATE task_schedulers SET context=? WHERE id=?', (encode(context), scheduler.id))
            await db.execute('UPDATE monitor_auto_settings SET enabled=0 WHERE owner=? AND scope=?', (owner, COMPONENT_ID))
        from .sessions import sync_daily_read_scope
        await sync_daily_read_scope(policy)
    return await snapshot(owner)
