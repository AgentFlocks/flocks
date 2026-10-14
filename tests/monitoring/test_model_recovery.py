"""Model outage tests use synthetic providers, devices and isolated databases."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import capabilities as c, investigation as i, model_recovery as recovery
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import connection, rows


class GatewayTimeout(RuntimeError):
    status_code = 504


class Unauthorized(RuntimeError):
    status_code = 401


class Recorder:
    session_id = 'synthetic-monitor-session'

    async def call(self, name, params, operation):
        return (await operation('synthetic-message'))[0]


@pytest.fixture
async def model_case(tmp_path, monkeypatch):
    from flocks.hub.installer import install_plugin
    await install_plugin('agent', 'security-monitor')
    model = {'provider_id': 'synthetic-provider', 'model_id': 'synthetic-model'}
    monkeypatch.setattr(i.Config, 'resolve_default_llm', AsyncMock(side_effect=lambda: dict(model)))
    monkeypatch.setattr(i.Provider, 'apply_config', AsyncMock())
    chat = AsyncMock(side_effect=GatewayTimeout('private provider response must not be exposed'))
    monkeypatch.setattr(i.Provider, 'get', lambda _: SimpleNamespace(chat=chat))
    policy = MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['xdr'])
    catalog = [c.Capability('cap-1', 'xdr', policy.tool, 'xdr', 'Synthetic XDR', 'unknown')]
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=(catalog, [])))
    monkeypatch.setattr(i.Agent, 'list', AsyncMock(return_value=[]))
    query = AsyncMock(return_value=({'data': {'item': [{'hostIp': '192.0.2.1'}]}}, {}))
    monkeypatch.setattr(c, 'query', query)
    clock = [i.now()]
    monkeypatch.setattr(i, 'now', lambda: clock[0])
    def event(identifier, device='xdr'):
        return {'key': f'{device}:incident:{identifier}', 'id': identifier, 'device': device,
            'name': 'Synthetic event', 'host': '192.0.2.1', 'endTime': 100, 'dealStatus': 0,
            'basics': {'name': 'Synthetic event'}, 'development_sample': False,
            'investigationWindow': {'start': 1, 'end': 101}}
    async def enqueue(item, target_policy=policy):
        async with connection() as db:
            await i.enqueue(db, target_policy, item, clock[0].isoformat())
    return SimpleNamespace(policy=policy, model=model, chat=chat, query=query,
        catalog=catalog, clock=clock, event=event, enqueue=enqueue, recorder=Recorder())


async def test_gateway_failure_stops_same_model_before_next_case_prime(model_case):
    case = model_case
    first, second = case.event('first'), case.event('second')
    await case.enqueue(first)
    await case.enqueue(second)
    budget = i.Budget()
    await i.investigate(case.policy, first, case.recorder, budget)
    await i.investigate(case.policy, second, case.recorder, budget)
    assert case.chat.await_count == budget.models == 1
    assert case.query.await_count == budget.calls == 1
    assert first['investigation']['state'] == 'system_wait'
    assert first['investigation']['attempted_this_round']
    assert first['investigation']['http_status'] == 504
    assert first['investigation']['failure_stage'] == 'model'
    assert '网关超时' in first['investigation']['reason']
    assert 'private' not in json.dumps(first)
    assert second['investigation']['state'] == 'deferred'
    assert not second['investigation']['attempted_this_round']
    saved = await rows('SELECT event_key,attempts,failure_count,retry_exhausted,evidence FROM monitor_investigations ORDER BY event_key')
    assert saved[0]['attempts'] == saved[0]['failure_count'] == 1
    assert len(json.loads(saved[0]['evidence'])) == 1
    assert saved[1]['attempts'] == saved[1]['failure_count'] == 0
    assert not saved[0]['retry_exhausted'] and not saved[1]['retry_exhausted']
    assert not await rows('SELECT * FROM monitor_mail_notices')
    assert not await rows('SELECT * FROM monitor_dispositions')


async def test_cooldown_survives_new_round_and_success_releases_all_cases(model_case):
    case = model_case
    first = case.event('first')
    await case.enqueue(first)
    await i.investigate(case.policy, first, case.recorder, i.Budget())
    # A new Budget models a new round/process: the cooldown remains in SQLite.
    second = case.event('second')
    await case.enqueue(second)
    await i.investigate(case.policy, second, case.recorder, i.Budget())
    assert case.chat.await_count == case.query.await_count == 1
    case.clock[0] += timedelta(minutes=11)
    case.chat.side_effect = None
    case.chat.return_value = SimpleNamespace(finish_reason='stop', tool_calls=[], content=i.Choice(
        action='finish', verdict='unknown', reason='需要更多事实', evidence_ids=['evidence-1']).model_dump_json())
    await i.investigate(case.policy, first, case.recorder, i.Budget())
    assert first['investigation']['state'] == 'ready'
    assert case.chat.await_count == 2 and case.query.await_count == 1
    assert not await rows('SELECT * FROM monitor_model_recovery')
    await i.investigate(case.policy, second, case.recorder, i.Budget())
    assert second['investigation']['state'] == 'ready'
    assert case.chat.await_count == 3 and case.query.await_count == 2


async def test_transient_outage_does_not_exhaust_incident_after_three_rounds(model_case):
    case = model_case
    event = case.event('first')
    await case.enqueue(event)
    for delay in [10, 20, 40, 60, 60]:
        await i.investigate(case.policy, event, case.recorder, i.Budget())
        outcome = event['investigation']
        assert outcome['error_kind'] == 'model_transient'
        assert not outcome['retry_exhausted'] and outcome['verdict'] == 'unknown'
        assert i.parse_time(outcome['next_retry_at']) == case.clock[0] + timedelta(minutes=delay)
        case.clock[0] += timedelta(minutes=delay, seconds=1)
    assert case.chat.await_count == 5
    assert (await rows('SELECT failure_count FROM monitor_investigations'))[0]['failure_count'] == 5


async def test_model_scope_isolated_by_owner_project_device_provider_and_model(model_case):
    case = model_case
    key = ('owner', 'project', 'xdr', 'synthetic-provider', 'synthetic-model')
    await recovery.failed(key, 'timeout', case.clock[0])
    assert await recovery.blocked(key, case.clock[0])
    for index in range(5):
        isolated = list(key)
        isolated[index] += '-other'
        assert await recovery.blocked(tuple(isolated), case.clock[0]) is None
    # Switching the configured model can immediately investigate a new case.
    case.model['model_id'] = 'repaired-model'
    event = case.event('other-model')
    await case.enqueue(event)
    await i.investigate(case.policy, event, case.recorder)
    assert case.chat.await_count == 1
    assert event['investigation']['model_id'] == 'repaired-model'


async def test_recovery_probe_is_single_flight_and_cancel_releases_lease(model_case):
    case = model_case
    key = ('owner', 'project', 'xdr', 'synthetic-provider', 'synthetic-model')
    await recovery.failed(key, 'timeout', case.clock[0])
    case.clock[0] += timedelta(minutes=11)
    gates = await asyncio.gather(*(recovery.acquire(key, case.clock[0]) for _ in range(2)))
    assert sum(gate is None for gate in gates) == 1
    assert await recovery.blocked(key, case.clock[0])
    await recovery.release(key)
    assert await recovery.blocked(key, case.clock[0]) is None
    # Cancellation at the actual provider preserves the incident and health.
    case.chat.side_effect = asyncio.CancelledError
    event = case.event('cancelled')
    await case.enqueue(event)
    with pytest.raises(asyncio.CancelledError):
        await i.investigate(case.policy, event, case.recorder)
    assert event['investigation']['state'] == 'deferred'
    health = (await rows('SELECT * FROM monitor_model_recovery'))[0]
    assert health['failures'] == 1 and health['probe_until'] is None


async def test_authentication_error_is_not_timeout_or_indefinitely_retried(model_case):
    case = model_case
    case.chat.side_effect = Unauthorized('private secret')
    event = case.event('auth')
    await case.enqueue(event)
    await i.investigate(case.policy, event, case.recorder)
    outcome = event['investigation']
    assert outcome['error_kind'] == 'dependency'
    assert outcome['model_failure'] == 'authentication' and outcome['http_status'] == 401
    assert '认证失败' in outcome['reason'] and '超时' not in outcome['reason']
    assert not await rows('SELECT * FROM monitor_model_recovery')


async def test_unexpected_query_bug_is_reported_as_query_not_model_failure(model_case, monkeypatch):
    case = model_case
    records = []
    monkeypatch.setattr(i.diag, 'event', lambda name, **fields: records.append({'event': name, **fields}))
    async def failing_query(*args, **kwargs):
        raise TypeError('private value must not appear')
    case.query.side_effect = failing_query
    event = case.event('query-bug')
    await case.enqueue(event)
    await i.investigate(case.policy, event, case.recorder)
    outcome = event['investigation']
    assert outcome['failure_stage'] == 'query' and outcome['error_type'] == 'TypeError'
    assert outcome['failure_source'] == 'Synthetic XDR'
    assert '程序错误' in outcome['reason'] and '模型' not in outcome['reason']
    assert 'private' not in json.dumps(event)
    assert case.chat.await_count == 0
    proof = json.loads((await rows('SELECT evidence FROM monitor_investigations'))[0]['evidence'])[0]
    assert not proof['success'] and proof['error_type'] == 'TypeError'
    diagnostic = next(record for record in records if record['event'] == 'investigation.query')
    assert diagnostic['error_type'] == 'TypeError'
    assert diagnostic['error_frames']  # Original safe source frames survive the wrapper.
    assert 'private' not in json.dumps(records)


async def test_same_round_fault_stays_blocked_even_if_cooldown_elapses(model_case):
    case = model_case
    first, second = case.event('first'), case.event('second')
    await case.enqueue(first)
    await case.enqueue(second)
    budget = i.Budget()
    await i.investigate(case.policy, first, case.recorder, budget)
    case.clock[0] += timedelta(minutes=11)
    await i.investigate(case.policy, second, case.recorder, budget)
    assert case.chat.await_count == 1 and not second['investigation']['attempted_this_round']


async def test_switching_model_releases_old_case_deadline_without_erasing_old_model_health(model_case):
    case = model_case
    event = case.event('first')
    await case.enqueue(event)
    await i.investigate(case.policy, event, case.recorder)
    prior_deadline = event['investigation']['next_retry_at']
    case.model['model_id'] = 'replacement-model'
    case.chat.side_effect = None
    case.chat.return_value = SimpleNamespace(finish_reason='stop', tool_calls=[], content=i.Choice(
        action='finish', verdict='unknown', reason='需要更多事实', evidence_ids=['evidence-1']).model_dump_json())
    await i.investigate(case.policy, event, case.recorder)
    assert event['investigation']['state'] == 'ready'
    assert case.chat.await_count == 2 and case.query.await_count == 1
    old_health = (await rows('SELECT * FROM monitor_model_recovery'))[0]
    assert old_health['model'] == 'synthetic-model' and old_health['next_retry_at'] == prior_deadline


async def test_invalid_persisted_window_reports_time_validation_without_model_request(model_case):
    case = model_case
    event = case.event('bad-window')
    event['investigationWindow'] = {'start': None, 'end': 101}
    await case.enqueue(event)
    await i.investigate(case.policy, event, case.recorder)
    outcome = event['investigation']
    assert outcome['failure_stage'] == 'query_time' and outcome['error_code'] == 'missing_value'
    assert outcome['retry_exhausted'] and outcome['state'] == 'system_wait'
    assert '查询时间参数无效' in outcome['next_step']
    assert case.chat.await_count == case.query.await_count == 0
    await i.investigate(case.policy, event, case.recorder)
    assert not event['investigation']['attempted_this_round']
    assert case.chat.await_count == case.query.await_count == 0


async def test_crashed_probe_lease_expires_and_does_not_permanently_block(model_case):
    case = model_case
    key = ('owner', 'project', 'xdr', 'synthetic-provider', 'synthetic-model')
    await recovery.failed(key, 'timeout', case.clock[0])
    case.clock[0] += timedelta(minutes=11)
    assert await recovery.acquire(key, case.clock[0]) is None
    assert await recovery.acquire(key, case.clock[0])
    case.clock[0] += timedelta(seconds=recovery.PROBE_LEASE_SECONDS + 1)
    assert await recovery.acquire(key, case.clock[0]) is None
