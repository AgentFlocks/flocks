"""A real round persists actionable failure context without mailbox attribution."""
import json
from unittest.mock import AsyncMock

from flocks.monitoring import capabilities, diagnostics as diag, investigation, runtime
from flocks.monitoring.query_time import QueryTimeError
from flocks.monitoring.store import encode, rows, write
from test_round_endings import context  # noqa: F401


async def test_query_time_failure_identifies_event_source_and_keeps_incident_open(context, monkeypatch):
    context.policy.device_names['xdr'] = '测试 XDR'
    await write('UPDATE monitor_installations SET policy=?', (encode(context.policy.model_dump()),))
    original_query = capabilities.query
    catalog = [capabilities.Capability('fixture-cap', 'xdr', context.policy.tool, 'xdr', '测试 XDR', 'unknown'),
               capabilities.Capability('tdp-cap', 'tdp', 'tdp_log_search', 'tdp', '测试 TDP', 'unknown')]
    monkeypatch.setattr(capabilities, 'discover', AsyncMock(return_value=(catalog, [])))

    async def query(policy, session, message, capability, event, entity, **options):
        if capability.kind == 'tdp':
            raise QueryTimeError('原事件调查时间窗口缺少结束时间', code='missing_value', field='end', source='TDP 关联查询')
        return await original_query(policy, session, message, capability, event, entity)

    monkeypatch.setattr(capabilities, 'query', query)
    monkeypatch.setattr(investigation, 'choose', AsyncMock(return_value=investigation.Choice(
        action='query', capability='tdp-cap', entity='related', reason='核对网络侧行为')))
    outcome = await runtime.run(context.execution, context.policy, context.adapter)
    assert outcome.action == 'error'
    attempt = (await rows('SELECT * FROM monitor_attempts'))[0]
    result = json.loads(attempt['result'])
    failure = result['investigation_failures'][0]
    assert failure['event_id'] == 'event-1'
    assert failure['device_name'] == '测试 XDR' and failure['event_name'] == 'Fixture incident'
    assert failure['failure_stage'] == 'query_time' and failure['error_kind'] == 'query_time'
    assert '缺少结束时间' in failure['reason']
    assert '测试 XDR' in attempt['error'] and 'Fixture incident' in attempt['error']
    assert '检查模型' not in attempt['error']
    assert result['investigation_incomplete'] == 1
    assert not await rows('SELECT * FROM monitor_dispositions')
    assert not await rows('SELECT * FROM monitor_mail_notices')
    state = (await rows('SELECT * FROM monitor_event_followup'))[0]
    assert state['watch_count'] == 0 and state['state'] == 'watching'


async def test_diagnostic_budget_keeps_investigation_cause_after_history_reads(monkeypatch):
    captured = []
    monkeypatch.setattr(diag._sink, 'submit', lambda fields: captured.append(diag.safe_record(fields)))
    async with diag.trace_scope('owner', 'scope', 'execution'):
        for _ in range(diag.MAX_EVENTS):
            diag.event('adapter.result', success=True)
        for _ in range(diag.MAX_ERRORS):
            diag.event('adapter.failure', failure=True, reason='tool_failed')
        diag.event('investigation.model', failure=True, failure_stage='model',
                   error_type='InternalServerError', model_failure='timeout', http_status=504)
        diag.event('investigation.failure', failure=True, failure_stage='query_time',
                   error_kind='query_time', error_code='missing_value', time_field='end',
                   time_value_type='NoneType', raw_value='PRIVATE', exception_message='PRIVATE')
        for _ in range(diag.MAX_INVESTIGATION_SIGNALS * 2):
            diag.event('investigation.recovery', investigation_state='system_wait')
        diag.result('failed')
    failure = next(row for row in captured if row['event'] == 'investigation.failure')
    assert failure['error_kind'] == 'query_time' and failure['time_value_type'] == 'NoneType'
    assert any(row.get('http_status') == 504 for row in captured)
    assert captured[-1]['event'] == 'trace.end' and captured[-1]['suppressed'] > 0
    assert len(captured) <= diag.MAX_EVENTS + diag.MAX_ERRORS + diag.MAX_INVESTIGATION_SIGNALS + 2
    assert 'PRIVATE' not in json.dumps(captured)
