"""Actual Hub → discovery → queue → BackgroundManager → device ToolRegistry → facts."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from flocks.auth.context import set_current_auth_user, reset_current_auth_user
from flocks.auth.service import AuthService
from flocks.config.config_writer import ConfigWriter
from flocks.hub.installer import install_plugin
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.scheduling import admit_slots
from flocks.monitoring.store import rows
from flocks.task.executor import TaskExecutor
from flocks.task.store import TaskStore
from flocks.task.queue import TaskQueue
from flocks.tool.registry import ToolRegistry, Tool, ToolInfo, ToolResult, ToolParameter, ParameterType
from flocks.tool.device.store import insert_device
from flocks.monitoring.reports import snapshot

@pytest.mark.asyncio
async def test_full_installed_path_no_direct_ready_or_result_seeding(monkeypatch):
    user = await AuthService.bootstrap_admin('monitor-test', 'Synthetic-test-password-2026!')
    token = set_current_auth_user(user.to_auth_user())
    calls = []
    async def handler(ctx, action, **kwargs):
        calls.append(action)
        assert action in {'list', 'get_entities'}
        if action == 'list':
            return ToolResult(success=True, output={'code': 0, 'data': {'list': [{'uuId': 'fixture-event', 'riskLevel': 2, 'name': '隔离测试数据'}], 'total': 1}})
        return ToolResult(success=True, output={'code': 0, 'data': {'list': []}})
    parameters = [ToolParameter(name=k, type=ParameterType.STRING if k in {'action','uuid','entity_type'} else ParameterType.INTEGER, required=k == 'action')
                  for k in ('action','start_time','end_time','page_num','page_size','uuid','entity_type')]
    tool = Tool(info=ToolInfo(name='sangfor_xdr_incidents', description='Isolated synthetic device', source='device', provider='sangfor_xdr_v2_2', parameters=parameters), handler=handler)
    monkeypatch.setattr(ToolRegistry, '_tools', {tool.info.name: tool})
    monkeypatch.setattr(ToolRegistry, '_initialized', True)
    ConfigWriter.set_api_service('sangfor_xdr_v2_2', {'enabled': True})
    await insert_device(device_id='fixture-xdr', group_id='default-room', name='Synthetic XDR', storage_key='sangfor_xdr_v2_2', service_id='sangfor_xdr', enabled=True, verify_ssl=True, db_fields={'host':'192.0.2.1', 'auth_code':'fixture-never-used'})
    try:
        await install_plugin('component', COMPONENT_ID)
        installed = (await rows('SELECT * FROM monitor_installations'))[0]
        assert installed['ready'] == 1, installed['reason']
        scheduler = await TaskStore.get_scheduler(installed['scheduler_id'])
        stamp = datetime.now(timezone.utc)
        scheduler.trigger.next_run = stamp
        await TaskStore.update_scheduler(scheduler)
        await admit_slots(scheduler, stamp)
        execution = await TaskQueue().dequeue()
        assert execution
        result = await TaskExecutor.dispatch(execution, scheduler)
        assert result.status.value == 'completed', result.error
        attempts = await rows('SELECT * FROM monitor_attempts')
        assert len(attempts) == 1 and attempts[0]['status'] == 'completed'
        data = await snapshot(user.id, COMPONENT_ID, attempts[0]['business_date'])
        assert data['metrics']['events'] == 1 and data['report']['status'] == 'updated'
        assert calls == ['list', 'get_entities']
    finally:
        reset_current_auth_user(token)
