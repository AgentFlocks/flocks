"""Only complete device snapshots end local monitoring; absence never writes XDR."""
import json
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import followup, investigation, reconciliation, runtime
from flocks.monitoring.adapter import ContractError, normalize
from flocks.monitoring.store import connection, encode, rows, write
from test_event_list import observe
from test_round_endings import context  # noqa: F401


async def previous(context, identifier='previous', device='xdr', **updates):
    event = normalize(device, {'uuId': identifier, 'name': '历史事件', 'hostIp': '192.0.2.99',
                              'dealStatus': 0, 'incidentSeverity': 3, 'whiteStatus': '未加白', **updates})
    event['investigationWindow'] = {'start': 1, 'end': 101}
    await observe(event, project=context.policy.project, suffix=identifier)
    async with connection() as db:
        await investigation.enqueue(db, context.policy, event, investigation.now().isoformat())
    return event


async def test_complete_empty_snapshot_ends_all_prior_states_without_investigation_or_write(context, monkeypatch):
    events = [await previous(context, identifier=f'previous-{n}') for n in range(16)]
    await write("UPDATE monitor_investigations SET state='system_wait',retry_exhausted=1,error_kind='system'")
    context.events.clear()
    worker = AsyncMock(wraps=investigation.investigate)
    monkeypatch.setattr(investigation, 'investigate', worker)
    result = await runtime.run(context.execution, context.policy, context.adapter)
    assert result.action == 'stop' and worker.await_count == 0
    assert not await investigation.pending(context.policy)
    states = await followup.snapshot(context.policy)
    assert len(states) == 16
    assert all(state['state'] == 'monitoring_ended' and state['closed'] and not state['xdr_closed'] for state in states.values())
    assert {event['key'] for event in events} == set(states)
    facts = json.loads((await rows('SELECT result FROM monitor_attempts ORDER BY sequence DESC LIMIT 1'))[0]['result'])
    assert facts['followup']['reconciliation']['monitoring_ended'] == 16
    assert not await rows('SELECT * FROM monitor_dispositions')
    assert not await rows('SELECT * FROM monitor_mail_notices')


async def test_failed_snapshot_cannot_end_prior_event(context, monkeypatch):
    old = await previous(context)
    monkeypatch.setattr(investigation, 'pending', AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime, 'query_device', AsyncMock(side_effect=ContractError('设备查询失败')))
    await runtime.run(context.execution, context.policy, context.adapter)
    state = (await followup.snapshot(context.policy)).get(old['key'])
    assert not state or state['state'] != 'monitoring_ended'


async def test_partial_pagination_keeps_absent_event_open_but_actual_return_reopens_local_end(context, monkeypatch):
    old = await previous(context)
    returned = await previous(context, identifier='returned')
    await followup.end_missing(context.policy, returned, 'old-complete-snapshot')
    monkeypatch.setattr(investigation, 'pending', AsyncMock(return_value=[]))
    async def incomplete(adapter, device, start, end, recorder, *, on_page):
        await on_page([returned], False)
        raise ContractError('后续分页失败')
    monkeypatch.setattr(runtime, 'query_device', incomplete)
    await runtime.run(context.execution, context.policy, context.adapter)
    states = await followup.snapshot(context.policy)
    assert states[returned['key']]['state'] != 'monitoring_ended'
    assert old['key'] not in states or states[old['key']]['state'] != 'monitoring_ended'


async def test_membership_completion_is_scoped_to_successful_device(context):
    context.policy.devices.append('other-xdr')
    one = await previous(context)
    two = await previous(context, identifier='other', device='other-xdr')
    ended = await reconciliation.finish_missing(context.policy, set(), {'xdr'}, 'complete-xdr')
    assert ended == {one['key']}
    states = await followup.snapshot(context.policy)
    assert two['key'] not in states


async def test_full_snapshot_keeps_source_present_event_regardless_old_cursor(context):
    await write('INSERT INTO monitor_cursors VALUES(?,?,?,?)',
                (context.policy.owner, context.policy.scope, 'xdr', int(runtime.now().timestamp())-1))
    seen = []
    base = context.adapter
    class Capture(base):
        async def call(self, device, params, message):
            seen.append(params)
            return await super().call(device, params, message)
    await runtime.run(context.execution, context.policy, Capture)
    assert seen and all(item['end_time']-item['start_time'] == 86400 for item in seen)
    assert not any(state['state'] == 'monitoring_ended' for state in (await followup.snapshot(context.policy)).values())


async def test_missing_snapshot_cancels_old_ignore_before_any_xdr_write(context):
    event = await previous(context)
    await followup.observe(context.policy, event, 'previous-round', query_complete=False)
    await write("UPDATE monitor_event_followup SET state='queued',target_status=60,queued_round='previous-round'")
    context.events.clear()
    await runtime.run(context.execution, context.policy, context.adapter)
    state = (await followup.snapshot(context.policy))[event['key']]
    assert state['state'] == 'monitoring_ended' and state['target_status'] is None
    assert not await rows('SELECT * FROM monitor_dispositions')


async def test_new_suite_requires_source_membership_core_contract():
    from flocks.monitoring.lifecycle import CORE_CAPABILITIES
    from pathlib import Path
    manifest = json.loads((Path(__file__).parents[2]/'.flocks/flockshub/plugins/components/host-security-monitor/manifest.json').read_text())
    assert 'monitor.source-membership.v1' in CORE_CAPABILITIES
    assert 'monitor.source-membership.v1' in manifest['requiredCoreCapabilities']
