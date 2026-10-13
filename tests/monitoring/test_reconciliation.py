"""Incremental-list omission requires an exact-ID read, never guessed closure."""
import copy
import json
from types import SimpleNamespace

import pytest

from flocks.monitoring import disposition as d, event_list, followup, reconciliation
from flocks.monitoring.adapter import ContractError, normalize
from flocks.monitoring.models import COMPONENT_ID, MonitoringPolicy
from flocks.monitoring.store import encode, rows, write
from test_event_list import DAY, install, observe


class Recorder:
    async def call(self, title, params, operation):
        return (await operation('offline-message'))[0]


@pytest.fixture
async def history(tmp_path, monkeypatch):
    await install()
    raw = {'uuId': 'incident-history', 'name': '历史安全事件', 'hostIp': '192.0.2.10',
           'startTime': 1791766800, 'endTime': 1791767400, 'incidentSeverity': 3,
           'dealStatus': 0, 'whiteStatus': '未加白', 'alertIds': ['alert-one']}
    event = normalize('a', raw)
    await observe(event)
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('next-round', 'next-execution', 'owner', 'project', COMPONENT_ID, DAY,
                 'next-session', 'next-message', DAY+'T14:00:00Z', 'running'))
    policy = MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['a'], device_names={'a': '股份 XDR'})
    state = SimpleNamespace(raw=raw, event=event, policy=policy, calls=[], response=None, error=None)
    class Adapter:
        async def call(self, device, params, message_id):
            state.calls.append((device, copy.deepcopy(params)))
            assert params['action'] == 'list', 'reconciliation must never mutate XDR'
            if state.error:
                raise state.error
            return copy.deepcopy(state.response if state.response is not None else {'data': {'item': [state.raw], 'total': 1}})
    state.adapter = Adapter()
    async def operation(adapter, event, params, text):
        return await adapter.call(event['device'], params, 'offline-message')
    # Keep the real exact-ID reader/response validation; omit only chat writes.
    monkeypatch.setattr(d, 'operation', operation)
    return state


async def check(case, *, current_keys=None, limit=12):
    return await reconciliation.recheck_existing(case.policy, case.adapter, Recorder(), 'next-round', current_keys or set(), limit=limit)


@pytest.mark.parametrize('terminal', [40, 60])
async def test_missing_incremental_event_closes_only_after_exact_terminal_readback(history, terminal):
    history.raw['dealStatus'] = terminal
    events, checked, summary = await check(history)
    assert summary['checked'] == summary['external_closed'] == 1 and not summary['errors']
    assert not checked and len(events) == 1
    item = (await followup.snapshot(history.policy))[history.event['key']]
    assert item['state'] == 'external_closed' and item['closed'] and item['external']
    assert item['observed_status'] == terminal
    assert events[0]['assessment']['ended'] and events[0]['assessment']['external']
    assert events[0]['assessment']['reason']  # runtime.analyze requires a complete assessment
    params = history.calls[0][1]
    assert params['uuids'] == [history.event['id']]
    assert params['start_time'] == 0 and params['deal_statuses'] == params['white_status'] == []
    assert params['time_field'] == 'endTime'
    assert len(await rows('SELECT * FROM monitor_observations')) == 2
    assert not await rows('SELECT * FROM monitor_dispositions')


@pytest.mark.parametrize('cause', ['empty', 'wrong_id', 'ambiguous', 'exception', 'missing_status', 'unknown_status', 'boolean_status'])
async def test_empty_wrong_or_failed_exact_query_cannot_mean_external_disposition(history, cause):
    raw = copy.deepcopy(history.raw)
    if cause == 'empty':
        history.response = {'data': {'item': [], 'total': 0}}
    elif cause == 'wrong_id':
        raw.update(uuId='incident-other', dealStatus=40)
        history.response = {'data': {'item': [raw], 'total': 1}}
    elif cause == 'ambiguous':
        history.response = {'data': {'item': [raw, raw], 'total': 2}}
    elif cause == 'exception':
        history.error = ContractError('synthetic unavailable')
    else:
        raw['dealStatus'] = None if cause == 'missing_status' else -999 if cause == 'unknown_status' else True
        history.response = {'data': {'item': [raw], 'total': 1}}
    events, checked, summary = await check(history)
    assert summary['checked'] == 1 and summary['external_closed'] == 0
    saved = await followup.snapshot(history.policy)
    assert not any(item['closed'] for item in saved.values())
    assert not any(event.get('assessment', {}).get('ended') for event in events)
    original = (await rows('SELECT data FROM monitor_observations WHERE attempt_id!=?', ('next-round',)))[0]
    assert json.loads(original['data'])['key'] == history.event['key']
    assert not await rows('SELECT * FROM monitor_dispositions')
    if cause in {'empty', 'wrong_id', 'ambiguous', 'exception'}:
        assert summary['errors'] and not events and not checked


@pytest.mark.parametrize('field,value', [('endTime', 1791767500), ('hostIp', '192.0.2.20'),
                                         ('name', '新的活动名称'), ('alertIds', ['alert-new'])])
@pytest.mark.parametrize('terminal', [40, 60])
async def test_terminal_status_on_new_activity_does_not_archive_the_prior_version(history, field, value, terminal):
    await followup.observe(history.policy, history.event, 'prior-round', query_complete=False)
    history.raw.update({field: value, 'dealStatus': terminal})
    events, checked, summary = await check(history)
    assert summary['external_closed'] == 0 and not checked
    state = (await followup.snapshot(history.policy))[history.event['key']]
    assert not state['closed'] and state['state'] == 'watching'
    assert len(events) == 1 and events[0]['source_terminal_unmatched']
    detail = await event_list.detail('owner', COMPONENT_ID, history.event['key'])
    # An unrelated newer source terminal state is recorded without claiming
    # that the old investigation or its pending work was completed.
    assert not detail['event']['assessment'].get('ended', False)
    assert detail['event']['handling'] == 'open'
    later = await event_list.list_events('owner', COMPONENT_ID, start_date='2026-10-13', end_date='2026-10-13', include_backlog=True)
    assert later['metrics']['events'] == 1
    assert later['items'][0]['members'][0]['key'] == history.event['key']
    assert later['items'][0]['members'][0]['handling'] == 'open'


async def test_currently_queried_event_is_not_rechecked_as_missing(history):
    events, checked, summary = await check(history, current_keys={history.event['key']})
    assert not events and not checked and summary['checked'] == 0
    assert not history.calls
    assert len(await rows('SELECT * FROM monitor_observations')) == 1


async def test_open_history_is_preserved_and_scheduled_for_investigation(history):
    events, checked, summary = await check(history)
    assert len(events) == 1 and checked == {history.event['key']}
    assert summary['external_closed'] == 0
    saved = await rows('SELECT * FROM monitor_investigations')
    assert len(saved) == 1 and saved[0]['event_key'] == history.event['key']
    assert len(await rows('SELECT * FROM monitor_observations')) == 2
    listing = await event_list.list_events('owner', COMPONENT_ID, start_date=DAY, end_date=DAY)
    assert listing['metrics']['events'] == 1


async def test_other_device_owner_project_and_scope_are_not_rechecked(history):
    for suffix, owner, project, scope, device in [
        ('device', 'owner', 'project', COMPONENT_ID, 'b'),
        ('owner', 'another-owner', 'project', COMPONENT_ID, 'a'),
        ('project', 'owner', 'old-project', COMPONENT_ID, 'a'),
        ('scope', 'owner', 'project', 'other-scope', 'a'),
    ]:
        event = normalize(device, {**history.raw, 'uuId': 'incident-'+suffix})
        await observe(event, owner=owner, project=project, scope=scope)
    await check(history)
    assert [params['uuids'] for _, params in history.calls] == [[history.event['id']]]
    assert len(await rows('SELECT * FROM monitor_observations')) == 6


async def test_per_round_limit_preserves_unchecked_history(history):
    for number in range(4):
        await observe(normalize('a', {**history.raw, 'uuId': f'incident-{number}'}))
    history.error = ContractError('synthetic unavailable')
    events, checked, summary = await check(history, limit=2)
    assert not events and not checked and summary['checked'] == 2
    assert len(history.calls) == 2
    assert len(await rows('SELECT * FROM monitor_observations')) == 5
    assert len(await rows('SELECT * FROM monitor_event_followup')) == 2


async def test_failed_exact_query_rotates_so_next_history_can_progress(history):
    next_event = normalize('a', {**history.raw, 'uuId': 'incident-second'})
    await observe(next_event)
    history.error = ContractError('synthetic unavailable')
    await check(history, limit=1)
    await check(history, limit=1)
    assert [params['uuids'] for _, params in history.calls] == [[history.event['id']], [next_event['id']]]


from test_round_endings import context


async def test_every_newly_queried_event_is_listed_even_when_investigation_budget_is_exhausted(context):
    from flocks.monitoring import runtime
    context.policy.investigation_calls = 1
    await write('UPDATE monitor_installations SET policy=? WHERE owner=? AND scope=?', (encode(context.policy.model_dump()), context.policy.owner, context.policy.scope))
    context.events[:] = [dict(context.events[0], uuId=f'new-event-{number:03d}') for number in range(40)]
    await runtime.run(context.execution, context.policy, context.adapter)
    attempt = (await rows('SELECT * FROM monitor_attempts ORDER BY sequence DESC LIMIT 1'))[0]
    observations = await rows('SELECT * FROM monitor_observations WHERE attempt_id=?', (attempt['id'],))
    assert len(observations) == 40
    assert {json.loads(row['data'])['id'] for row in observations} == {raw['uuId'] for raw in context.events}
    cases = await rows('SELECT * FROM monitor_investigations')
    assert len(cases) == 40 and sum(case['state'] == 'pending' for case in cases) >= 39
    listed = await event_list.list_events('owner', COMPONENT_ID, start_date=attempt['business_date'], end_date=attempt['business_date'])
    assert listed['metrics']['events'] == 40
    assert all(member['assessment']['verdict'] == 'unknown' for group in listed['items'] for member in group['members'])
    assert not await rows('SELECT * FROM monitor_mail_notices')
    assert not await rows('SELECT * FROM monitor_dispositions')


async def test_one_device_failure_does_not_drop_events_already_queried_from_another(context):
    from flocks.monitoring import runtime
    context.policy.devices = ['xdr', 'unavailable']
    await write('UPDATE monitor_installations SET policy=? WHERE owner=? AND scope=?', (encode(context.policy.model_dump()), context.policy.owner, context.policy.scope))
    class Adapter(context.adapter):
        async def call(self, device, params, message):
            if device == 'unavailable':
                raise ContractError('synthetic second device unavailable')
            return await super().call(device, params, message)
    await runtime.run(context.execution, context.policy, Adapter)
    attempt = (await rows('SELECT * FROM monitor_attempts ORDER BY sequence DESC LIMIT 1'))[0]
    observations = await rows('SELECT data FROM monitor_observations WHERE attempt_id=?', (attempt['id'],))
    assert len(observations) == 1 and json.loads(observations[0]['data'])['device'] == 'xdr'
    assert attempt['status'] == 'failed'
    listed = await event_list.list_events('owner', COMPONENT_ID, start_date=attempt['business_date'], end_date=attempt['business_date'])
    assert listed['metrics']['events'] == 1


@pytest.mark.parametrize('terminal', [40, 60])
async def test_source_reopening_same_activity_releases_a_previous_external_end(history, terminal):
    history.raw['dealStatus'] = terminal
    await check(history)
    assert (await followup.snapshot(history.policy))[history.event['key']]['closed']
    history.raw['dealStatus'] = 0
    reopened = normalize('a', history.raw)
    state = await followup.observe(history.policy, reopened, 'reopened-round', query_complete=True)
    assert not state['closed']
    assert state['state'] == 'watching'
    assert not state['external']
