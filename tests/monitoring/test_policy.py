import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
import pytest
from flocks.session.interaction_policy import unattended_scope, monitoring_read_scope
from flocks.tool.registry import ToolRegistry, Tool, ToolInfo, ToolContext, ToolResult, ToolParameter, ParameterType
from flocks.task.background import BackgroundManager, BackgroundTask
from flocks.permission.next import PermissionNext
from flocks.server.routes.question import list_question_requests
from flocks.tool.question_handler import api_question_handler

@pytest.mark.asyncio
@pytest.mark.parametrize('action,status', [('queued','queued'), ('error','error'), ('stop','completed')])
async def test_background_loop_result_semantics(action, status, monkeypatch):
    manager = BackgroundManager()
    monkeypatch.setattr(manager, '_run_session_with_watchdog', AsyncMock(return_value=SimpleNamespace(action=action, error='fixture failure', last_message=None)))
    task = BackgroundTask(id='test', status='pending', description='test', prompt='', agent='rex')
    await manager._run_existing_session(task, 'session')
    assert task.status == status
    if action == 'queued': assert task.completed_at is None

@pytest.mark.asyncio
async def test_direct_and_inherited_questions_and_approvals_create_nothing(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr('flocks.server.routes.event.publish_event', publish)
    with unattended_scope():
        async def child():
            with pytest.raises(PermissionError): await api_question_handler('fixture', [{'question': 'proceed?'}])
            with pytest.raises(PermissionError): await PermissionNext.ask('fixture', 'device', ['*'], [])
        await asyncio.create_task(child())
    assert list_question_requests('fixture') == []
    assert not PermissionNext._pending
    assert not publish.called

@pytest.mark.asyncio
async def test_device_action_boundary_counts_handlers_including_child(monkeypatch):
    calls = []
    async def handler(ctx, action):
        calls.append(action)
        return ToolResult(success=True, output={'data': {'list': [], 'total': 0}})
    tool = Tool(info=ToolInfo(name='fixture_xdr', description='fixture', source='device', provider='fixture',
                             parameters=[ToolParameter(name='action', type=ParameterType.STRING, required=True)]), handler=handler)
    monkeypatch.setattr(ToolRegistry, '_tools', {'fixture_xdr': tool})
    monkeypatch.setattr(ToolRegistry, '_initialized', True)
    monkeypatch.setattr(ToolRegistry, '_sync_configured_enabled_states', lambda: None)
    monkeypatch.setattr(ToolRegistry, '_resolve_device_target', AsyncMock(return_value=('device-1', None)))
    monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', AsyncMock(return_value=True))
    @asynccontextmanager
    async def credentials(device): yield True
    monkeypatch.setattr('flocks.tool.credential_context.activate_device_credentials', credentials)
    ctx = ToolContext(session_id='fixture', message_id='fixture')
    with unattended_scope(), monitoring_read_scope('fixture_xdr', ['device-1']):
        allowed = await ToolRegistry.execute('fixture_xdr', ctx, action='list', device_id='device-1')
        assert allowed.success, allowed.error
        for action in ['update_status', 'unisolate', 'isolate', 'delete', 'create', 'toggle_status']:
            result = await ToolRegistry.execute('fixture_xdr', ctx, action=action, device_id='device-1')
            assert not result.success
        async def child():
            with monitoring_read_scope('fixture_xdr', ['device-2']):
                return await ToolRegistry.execute('fixture_xdr', ctx, action='update_status', device_id='device-2')
        assert not (await asyncio.create_task(child())).success
        tool.info.requires_confirmation = True
        assert not (await ToolRegistry.execute('fixture_xdr', ctx, action='list', device_id='device-1')).success
    tool.info.requires_confirmation = False
    from flocks.hooks.pipeline import HookPipeline
    async def patched(_):
        return SimpleNamespace(output={'decision': {'validated_input_patch': {'action': 'update_status'}}}, execution_stop_requested=False)
    monkeypatch.setattr(HookPipeline, 'run_tool_before', patched)
    with unattended_scope(), monitoring_read_scope('fixture_xdr', ['device-1']):
        assert not (await ToolRegistry.execute('fixture_xdr', ctx, action='list', device_id='device-1')).success
    assert calls == ['list']
    assert not PermissionNext._pending and not list_question_requests('fixture')

@pytest.mark.asyncio
async def test_ordinary_permission_still_waits_for_human_reply():
    from flocks.session.interaction_policy import require_interactive
    await require_interactive('ordinary-session')
    task = asyncio.create_task(PermissionNext.ask('ordinary-session', 'bash', ['*'], [], request_id='ordinary-permission'))
    try:
        async with asyncio.timeout(2):
            while 'ordinary-permission' not in PermissionNext._pending:
                await asyncio.sleep(0)
        assert not task.done()
        await PermissionNext.reply('ordinary-permission', 'once', session_id='ordinary-session')
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done(): task.cancel()

@pytest.mark.asyncio
async def test_ordinary_tasks_create_one_session_per_execution(tmp_path):
    from flocks.task.manager import TaskManager
    from flocks.task.executor import TaskExecutor
    from flocks.task.models import ExecutionTriggerType
    scheduler = await TaskManager.create_scheduler(title='ordinary task', workspace_directory=str(tmp_path))
    sessions = []
    for _ in range(2):
        execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
        sessions.append(await TaskExecutor._create_task_session(execution, scheduler))
    assert len(set(sessions)) == 2

@pytest.mark.asyncio
async def test_timeout_awaits_background_cancellation_before_scope_release(monkeypatch, tmp_path):
    from flocks.hub import local
    monkeypatch.setattr(local, 'get_record', lambda *_: SimpleNamespace(enabled=True))
    from flocks.monitoring import runtime
    # This test owns only dispatch cancellation ordering. Agent loading is
    # covered by the installed-component tests, not a global execution bypass.
    monkeypatch.setattr('flocks.monitoring.agent_component.resolve', AsyncMock())
    from flocks.monitoring.models import MonitoringPolicy
    from flocks.monitoring.store import write, encode
    from flocks.task.manager import TaskManager
    from flocks.task.models import ExecutionTriggerType
    from flocks.auth.context import AuthUser
    policy = MonitoringPolicy(owner='owner',project='fixture',directory=str(tmp_path),devices=['fixture'],timeout_seconds=1)
    scheduler = await TaskManager.create_scheduler(title='timeout',context={'monitoring':policy.model_dump()})
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready,scheduler_id) VALUES(?,?,?,?,1,?)', ('owner',policy.scope,policy.project,encode(policy.model_dump()),scheduler.id))
    execution = await TaskManager.create_execution_from_scheduler(scheduler,trigger_type=ExecutionTriggerType.RUN_ONCE,enqueue=False)
    cancelled = asyncio.Event()
    entered = asyncio.Event()
    async def hanging():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally: cancelled.set()
    handle=asyncio.create_task(hanging())
    await entered.wait()
    manager=SimpleNamespace(run_existing_session=AsyncMock(return_value=SimpleNamespace(id='background')),wait_for=AsyncMock(return_value=None),cancel=lambda _:handle.cancel(),_task_handles={'background':handle})
    monkeypatch.setattr(runtime,'get_background_manager',lambda:manager)
    monkeypatch.setattr(runtime.AuthService,'get_user_by_id',AsyncMock(return_value=SimpleNamespace(status='active',to_auth_user=lambda:AuthUser(id='owner',username='owner',role='admin'))))
    result=await runtime.dispatch(execution,scheduler)
    assert result.status.value=='failed' and cancelled.is_set() and handle.done()
    assert execution.id not in runtime._running
