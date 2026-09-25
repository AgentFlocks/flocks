import json
from pathlib import Path
import pytest
from unittest.mock import AsyncMock

from flocks.monitoring import runtime, summaries
from flocks.monitoring.adapter import ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.reports import snapshot
from flocks.monitoring.store import rows
from flocks.project.project import Project
from flocks.session.message import Message
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionTriggerType


def test_entity_explanations_are_bounded_and_exclude_sensitive_fields():
    value = {'data': {'item': [{'id': f'file-{i}', 'fileName': '<script>' + 'x' * 400,
                              'threatLevel': 3, 'commandLine': 'PRIVATE_COMMAND', 'token': 'PRIVATE_TOKEN'} for i in range(200)]}}
    data, summary = summaries.operation_summary({'id': 'sample', 'name': '样本'}, {'action': 'get_entities', 'entity_type': 'file'}, value)
    assert data['count'] == 200 and 'file-0' in summary.details
    assert '威胁判定：恶意' in summary.details and '控制成功证据：未确认' in summary.details
    assert 'PRIVATE_COMMAND' not in summary.details and 'PRIVATE_TOKEN' not in summary.details
    assert len(summary.details) <= summaries.MAX_DETAILS + 150


async def execute(tmp_path, monkeypatch, mode):
    directory = tmp_path / '.flocks/workspace/summaries'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='summary-fixture', worktree=str(directory))
    policy = MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['fixture'], investigation_calls=24)
    count = 0 if mode == 'empty' else 105 if mode in ('pages', 'late_failure') else 2
    items = [{'uuId': f'event-{i}', 'name': f'事件 {i}', 'riskLevel': 1 if i % 2 == 0 else 9, 'hostIp': '192.0.2.1', 'type': '检测'} for i in range(count)]
    if items:
        items[0].pop('name')
        items[1]['name'] = '<img src=x> [事件](https://invalid.example)\n第二行'
    calls, notifications = [], []
    async def publish(kind, value):
        notifications.append((kind, value))
    monkeypatch.setattr(runtime, 'publish', publish)
    class Adapter:
        def __init__(self, policy, session): self.policy = policy
        async def call(self, device, params, message):
            calls.append(params)
            if params['action'] == 'list':
                if mode == 'query_failure' or (mode == 'late_failure' and params['page_num'] == 2):
                    raise ContractError('设备查询失败或权限不足；请检查接入状态')
                offset = (params['page_num'] - 1) * 100
                return {'data': {'list': items[offset:offset+100], 'total': count}}
            if mode == 'partial' and params['uuid'] == 'event-0':
                raise ContractError('关联实体响应缺失')
            return {'data': {'list': [{'hostId': 'host-a', 'ip': '192.0.2.1'}] if params['uuid'] == 'event-0' else []}}
    from flocks.monitoring import capabilities as c, investigation as i
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([c.Capability('fixture-cap', 'fixture', policy.tool, 'xdr', 'Fixture XDR', 'unknown')], [])))
    monkeypatch.setattr(i.Agent, 'list', AsyncMock(return_value=[]))
    async def choose(agent, data):
        if not data['evidence']:
            return i.Choice(action='query', capability='fixture-cap', reason='核对原主机', entity='host')
        return i.Choice(action='finish', verdict='unknown', evidence_ids=['evidence-1'], reason='查询证据已保存，仍需负责人核对')
    async def query(policy, session, message, capability, event, entity):
        params = {'action': 'get_entities', 'uuid': event['id'], 'entity_type': entity}
        return await Adapter(policy, session).call(capability.device, params, message), params
    monkeypatch.setattr(i, 'choose', choose)
    monkeypatch.setattr(c, 'query', query)
    scheduler = await TaskManager.create_scheduler(title='summary fixture', context={'monitoring': policy.model_dump()})
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    await runtime.run(execution, policy, Adapter)
    attempt = (await rows('SELECT * FROM monitor_attempts'))[0]
    steps = await rows('SELECT * FROM monitor_steps ORDER BY started_at')
    messages = await Message.list_with_parts(attempt['session_id'])
    # Drop only in-memory caches, then load the actual persisted session.
    Message._messages_cache.pop(attempt['session_id'], None)
    Message._parts_cache.pop(attempt['session_id'], None)
    reloaded = await Message.list_with_parts(attempt['session_id'])
    assert [m.model_dump() for m in reloaded] == [m.model_dump() for m in messages]
    texts = []
    for step in steps:
        message = next(m for m in reloaded if m.info.id == step['message_id'])
        parts = message.parts
        tool_index = next(i for i,p in enumerate(parts) if p.id == step['part_id'])
        text = parts[tool_index+1]
        assert text.type == 'text' and text.metadata['monitoringSummary'] is True
        assert text.metadata['toolPartID'] == step['part_id']
        assert len(text.metadata['details']) <= summaries.MAX_DETAILS + 150
        texts.append(text)
        completed = next(i for i,(kind,v) in enumerate(notifications) if kind == 'message.part.updated' and v['part']['id'] == step['part_id'] and v['part'].get('state',{}).get('status') in ('completed','error'))
        announced = next(i for i,(kind,v) in enumerate(notifications) if kind == 'message.part.updated' and v['part']['id'] == text.id)
        finished = next(i for i,(kind,v) in enumerate(notifications) if kind == 'message.updated' and v['info']['id'] == step['message_id'] and v['info'].get('finish'))
        assert completed < announced < finished
    return attempt, steps, texts, calls


@pytest.mark.parametrize('mode', ['empty', 'rich', 'pages', 'partial', 'query_failure', 'late_failure'])
async def test_persisted_step_results_and_order(tmp_path, monkeypatch, mode):
    attempt, steps, texts, calls = await execute(tmp_path, monkeypatch, mode)
    combined = '\n'.join(text.text for text in texts)
    messages = await Message.list_with_parts(attempt['session_id'])
    ending = '\n'.join(part.text for part in messages[-1].parts if part.type == 'text')
    marker = next(part for part in messages[-1].parts if part.type == 'text')
    assert marker.metadata['monitoringRoundEnd'] is True
    assert marker.metadata['roundStatus'] == attempt['status']
    assert marker.metadata['roundId'] == attempt['id']
    assert marker.metadata['nextStep'] == attempt['next_step']
    assert messages[-1].info.id == attempt['end_message_id']
    assert '本轮' in ending and '邮件跟进未启用' in ending
    if mode == 'empty':
        assert '当前时间范围和筛选条件下没有可分析事件' in ending
    if mode in ('query_failure', 'late_failure'):
        assert '不能据此说没有告警' in ending
    if mode == 'empty':
        assert '本轮 XDR 安全事件查询到 0 条事件' in combined
        assert len(calls) == 1 and len(texts) == 2
        assert '读取 0 条事件' in texts[-1].text
    elif mode in ('query_failure', 'late_failure'):
        assert attempt['status'] == 'failed'
        assert not await rows('SELECT * FROM monitor_observations')
        assert not await rows('SELECT * FROM monitor_cursors')
        assert '关联分析已跳过' in combined
        assert '上次查询进度保持不变' in combined
        assert '本轮 XDR 安全事件查询到 0 条事件' not in combined
        assert any(step['status'] == 'failed' for step in steps)
        if mode == 'late_failure':
            assert '分页尚未结束' in texts[0].text
    else:
        count = 105 if mode == 'pages' else 2
        assert f'本轮 XDR 安全事件查询到 {count} 条事件' in combined
        assert combined.count('本轮 XDR 安全事件查询到') == 1
        assert '名称未提供' in texts[0].metadata['details'] and 'event-0' in texts[0].metadata['details']
        assert '<img src=x>' in texts[0].metadata['details']  # rendered as plain text, not Markdown/HTML
        assert f'涉及 {count} 条事件' in texts[-1].text
        assert len(calls) == min(count, 20) + (2 if mode == 'pages' else 1)
        assert any(step['tool'] == '调查取证：Fixture XDR' for step in steps)
        assert '原事件' in combined
        result = json.loads(attempt['result'])
        assert f"风险 {result['risk']} 条、待判定 {result['unknown']} 条" in texts[-1].text
        if mode == 'partial':
            assert attempt['status'] == 'failed'
            assert '关联实体响应缺失' in combined
            assert any(step['tool'] == '智能体调查结果' and step['status'] == 'failed' for step in steps)
        else:
            assert attempt['status'] == 'completed'
        if mode == 'pages':
            assert '第 1 页查询到 100 条' in texts[0].text and '分页尚未结束' in texts[0].text
            assert '第 2 页查询到 5 条' in texts[1].text
            assert 'event-104' in texts[1].metadata['details']
            assert result['analyzed'] == 20 and result['deferred'] == 85
    assert '回查' in texts[-1].text
    assert '已闭环' not in texts[-1].text


def test_bounded_summary_details_and_unknown_hosts():
    hosts = [{'name': 'x' * 10000, 'ip': '192.0.2.1'} for _ in range(1000)] + [{}]
    summary = summaries.hosts_summary({'id': 'event', 'name': 'test'}, hosts)
    assert '1001 条' in summary.text and '1 条未提供主机标识' in summary.text
    assert '已截短' in summary.details and '还有 ' in summary.details
    assert len(summary.details) <= summaries.MAX_DETAILS + 150
    assert '\u202e' not in summaries.label('name\u202eevil')


@pytest.mark.parametrize('fields,expected', [
    ({'incidentThreatClass':'恶意软件','incidentThreatType':'木马','type':'legacy'}, '恶意软件 / 木马'),
    ({'incidentThreatType':'木马'}, '木马'),
    ({'incidentThreatClass':'<img src=x>\n分类'}, '<img src=x> 分类'),
    ({'incidentThreatClass':{},'type':'legacy'}, 'legacy'),
    ({}, '类型未提供'),
])
def test_native_incident_classification_in_step_summary(fields, expected):
    summary = summaries.page_summary(1, [{'uuId':'id',**fields}], 1, True)
    assert f'类型：{expected}；' in summary.details
