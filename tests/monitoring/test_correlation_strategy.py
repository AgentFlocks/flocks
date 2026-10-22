"""Explicit correlation scopes get bounded queries and durable explanations."""
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import investigation as i, capabilities as c
from flocks.monitoring.adapter import ContractError
from flocks.monitoring.store import rows
from tests.monitoring.test_investigation_details import detail_case, finish

query_capability = c.query

def related_catalog(case, monkeypatch):
    original = list(c.discover.return_value[0])
    catalog = original + [
        c.Capability('cap-tdp', 'tdp', 'tdp_log_search', 'tdp', '本环境 TDP', 'unknown'),
        c.Capability('cap-sig', 'sig', 'onesig_strategy_api_query', 'sig', '本环境 SIG', 'unknown'),
        c.Capability('cap-other', 'other', 'tdp_log_search__other', 'tdp', '其他环境 TDP', 'unknown'),
    ]
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=(catalog, [])))
    original_query = case.call.side_effect

    async def query(policy, session, message, capability, target, entity, **options):
        if entity == 'related':
            return {'data': {'items': [], 'total': 0}}, (
                {'action': 'search', 'time_from': 100, 'time_to': 201}
                if capability.kind == 'tdp' else {'action': 'asset_list', 'body': {'search': target['host']}})
        return await original_query(policy, session, message, capability, target, entity, **options)

    case.call.side_effect = query
    return catalog


async def test_bound_devices_run_before_optional_xdr_packets_and_finish(detail_case, monkeypatch):
    case = detail_case
    related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = {'xdr': ['tdp', 'sig']}
    model = AsyncMock(side_effect=finish)
    monkeypatch.setattr(i, 'choose', model)
    await i.investigate(case.policy, case.event, case.recorder)
    assert [call.args[5] for call in case.call.await_args_list] == [
        'proof', 'alerts', 'related', 'related', 'alert_proof', 'alert_proof']
    assert [cap['device'] for cap in model.await_args.args[1]['capabilities']] == ['xdr', 'xdr', 'tdp', 'sig']
    saved = (await rows('SELECT evidence,result FROM monitor_investigations'))[0]
    proofs = json.loads(saved['evidence'])
    assert [p['product'] for p in proofs if p['kind'] == 'related'] == ['tdp', 'sig']
    assert all(p['result_meta']['explicit_empty'] for p in proofs if p['kind'] == 'related')
    assert json.loads(saved['result'])['correlation_plan'] == []
    assert [r['status'] for r in case.event['story']['correlation_results']] == ['no_match', 'no_match']


async def test_budget_defers_unfinished_binding_and_resumes_without_duplicate_queries(detail_case, monkeypatch):
    case = detail_case
    related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = {'xdr': ['tdp', 'sig']}
    case.policy.investigation_calls = 3
    model = AsyncMock(side_effect=finish)
    monkeypatch.setattr(i, 'choose', model)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'deferred'
    assert not model.called
    assert case.call.await_count == 3
    saved = json.loads((await rows('SELECT result FROM monitor_investigations'))[0]['result'])
    assert len(saved['correlation_plan']) == 1
    plan = saved['correlation_plan'][0]
    assert plan['device'] == 'sig' and plan['event_revision'] == i.fingerprint(case.event)
    assert '预算' in plan['reason']
    monkeypatch.setattr(i, 'now', lambda: clock)
    clock = i.parse_time((await rows('SELECT next_retry_at FROM monitor_investigations'))[0]['next_retry_at']) + timedelta(seconds=1)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert [call.args[3].device for call in case.call.await_args_list if call.args[5] == 'related'] == ['tdp', 'sig']
    assert case.call.await_count == 6 and model.await_count == 1


@pytest.mark.parametrize('bindings,expected_status', [({}, 'not_queried'), ({'xdr': []}, 'skipped'),
    ({'xdr': ['missing']}, 'unavailable')])
async def test_unqueried_disabled_and_unavailable_remain_visible(detail_case, monkeypatch, bindings, expected_status):
    case = detail_case
    related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = bindings
    monkeypatch.setattr(i, 'choose', AsyncMock(side_effect=finish))
    await i.investigate(case.policy, case.event, case.recorder)
    assert all(call.args[5] != 'related' for call in case.call.await_args_list)
    result = json.loads((await rows('SELECT result FROM monitor_investigations'))[0]['result'])
    assert {row['status'] for row in result['correlation_plan']} == {expected_status}
    assert {row['status'] for row in case.event['story']['correlation_results']} == {expected_status}


async def test_model_gate_keeps_source_proof_and_leaves_related_queries_pending(detail_case, monkeypatch):
    case = detail_case
    related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = {'xdr': ['tdp']}
    monkeypatch.setattr(i, 'preflight_model', AsyncMock(side_effect=i.InvestigationError('模型暂不可用', 'dependency')))
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'system_wait'
    assert case.call.await_count == 1 and case.call.await_args.args[5] == 'proof'
    row = case.event['story']['correlation_results'][0]
    assert row['product'] == 'tdp' and row['status'] == 'not_queried'


async def test_failure_is_a_query_result_and_does_not_become_empty_match(detail_case, monkeypatch):
    case = detail_case
    related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = {'xdr': ['tdp', 'sig']}
    normal = case.call.side_effect
    async def failing(*args, **kwargs):
        if args[3].kind == 'tdp':
            raise ContractError('设备查询失败')
        return await normal(*args, **kwargs)
    case.call.side_effect = failing
    monkeypatch.setattr(i, 'choose', AsyncMock(side_effect=finish))
    await i.investigate(case.policy, case.event, case.recorder)
    assert [row['status'] for row in case.event['story']['correlation_results']] == ['failed', 'no_match']


async def test_direct_query_cannot_bypass_explicit_binding(detail_case, monkeypatch):
    case = detail_case
    catalog = related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = {'xdr': ['sig']}
    monkeypatch.setattr(c, 'assert_active', AsyncMock())
    execute = AsyncMock()
    monkeypatch.setattr(c.ToolRegistry, 'execute', execute)
    # Call the real executor rather than the fixture's query stub.
    with pytest.raises(ContractError, match='明确绑定'):
        await query_capability(case.policy, 'session', 'message', catalog[2], case.event, 'related')
    assert not execute.called


async def test_binding_change_reopens_cached_ready_result_and_reuses_xdr(detail_case, monkeypatch):
    case = detail_case
    related_catalog(case, monkeypatch)
    monkeypatch.setattr(i, 'choose', AsyncMock(side_effect=finish))
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.call.await_count == 4
    case.policy.correlation_bindings = {'xdr': ['tdp']}
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.call.await_count == 5
    assert case.call.await_args.args[3].device == 'tdp'
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.call.await_count == 5


@pytest.mark.parametrize('new_binding', [['sig'], []])
async def test_binding_change_excludes_revoked_cached_related_evidence(detail_case, monkeypatch, new_binding):
    case = detail_case
    related_catalog(case, monkeypatch)
    case.policy.correlation_bindings = {'xdr': ['tdp']}
    model = AsyncMock(side_effect=finish)
    monkeypatch.setattr(i, 'choose', model)
    await i.investigate(case.policy, case.event, case.recorder)
    assert any(p['device'] == 'tdp' for p in model.await_args.args[1]['evidence'])
    case.policy.correlation_bindings = {'xdr': new_binding}
    await i.investigate(case.policy, case.event, case.recorder)
    assert not any(p['device'] == 'tdp' for p in model.await_args.args[1]['evidence'])
    assert not any(row['device'] == 'tdp' for row in case.event['story']['correlation_results'])
    versions = await rows('SELECT evidence FROM monitor_investigation_versions')
    assert any(any(p['device'] == 'tdp' for p in json.loads(row['evidence'])) for row in versions)
