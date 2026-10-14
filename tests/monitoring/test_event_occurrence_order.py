"""Event occurrence drives the list; observation timestamps are audit data only."""
from datetime import datetime

import pytest

from flocks.monitoring import event_list, followup
from flocks.monitoring.investigation import fingerprint
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import encode, rows, write
from test_event_list import DAY, incident, install, investigate, listing, observe


def occurred(identifier, stamp, **updates):
    seconds = int(datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp())
    return incident(identifier, startTime=seconds, endTime=seconds, **updates)


async def test_global_order_and_pagination_ignore_observation_recency():
    await install()
    newest = incident('newest-occurrence', hour=6)
    oldest = incident('oldest-occurrence', hour=1)
    await observe(newest, day='2026-10-10')
    await observe(oldest, day='2026-10-13')
    await observe(oldest, day='2026-10-14', suffix='recent-refresh')

    first = await listing(group=False, limit=1)
    second = await listing(group=False, limit=1, offset=1)

    assert first['items'][0]['members'][0]['id'] == newest['id']
    assert second['items'][0]['members'][0]['id'] == oldest['id']
    assert first['has_more'] and not second['has_more']
    assert first['metrics']['events_in_range'] == 2
    assert second['items'][0]['last_seen_at'].startswith('2026-10-14')  # Audit compatibility only.


@pytest.mark.parametrize('latest_verdict,status', [('benign', 0), ('unknown', 0), ('risk', 40)])
async def test_group_headline_uses_latest_occurrence_not_highest_risk_or_observation(latest_verdict, status):
    await install()
    old = incident('old-risk', hour=1)
    latest = incident('latest', hour=6, dealStatus=status)
    for item, verdict, day in [(latest, latest_verdict, '2026-10-11'), (old, 'risk', '2026-10-13')]:
        await observe(item, day=day)
        evidence = [{'id': 'evidence-1', 'event': item['id'], 'device': item['device'],
                     'event_revision': fingerprint(item), 'kind': 'alert_proof', 'success': True,
                     'facts': {'requestBody': 'UNION SELECT value FROM users'}}]
        await investigate(item, evidence=evidence, result={'verdict': verdict, 'reason': '对应原始请求的研判',
            'confidence': 'high', 'risk_confirmed': verdict == 'risk', 'blocking_gaps': [],
            'evidence_ids': ['evidence-1']})

    item = (await listing())['items'][0]
    assert [member['id'] for member in item['members']] == ['latest', 'old-risk']
    assert item['primary_event_key'] == latest['key']
    assert item['assessment_status'] == item['members'][0]['assessment']['status']
    assert item['assessment_label'] == item['members'][0]['assessment']['label']
    assert item['risk'] == item['members'][0]['risk'] == latest_verdict
    assert item['handling'] == ('closed' if status == 40 else 'open')
    assert item['assessment_counts']['risk_open'] == 1
    assert sum(item['assessment_counts'].values()) == 2
    assert sum(item['closure_counts'].values()) == 2


async def test_date_belongs_to_latest_source_occurrence_not_first_or_observation_date():
    await install()
    item = occurred('spans-days', '2026-10-11T01:00:00Z')
    item['endTime'] = int(datetime.fromisoformat('2026-10-12T02:00:00+00:00').timestamp())
    await observe(item, day='2026-10-14')
    selected = await listing(include_backlog=False)
    previous = await event_list.list_events('owner', COMPONENT_ID,
        start_date='2026-10-11', end_date='2026-10-11', include_backlog=False)
    observed_day = await event_list.list_events('owner', COMPONENT_ID,
        start_date='2026-10-14', end_date='2026-10-14', include_backlog=False)
    assert selected['metrics']['events_in_range'] == 1
    assert previous['total'] == observed_day['total'] == 0
    member = selected['items'][0]['members'][0]
    assert member['occurrence_at'].startswith('2026-10-12')
    assert member['event_at'].startswith('2026-10-11')


async def test_old_source_event_observed_today_is_backlog_without_changing_its_date():
    await install()
    old = occurred('old-source', '2026-10-11T01:00:00Z')
    await observe(old, day=DAY)
    included = await listing()
    assert included['metrics']['events_in_range'] == 0
    assert included['metrics']['backlog_events'] == 1
    assert not included['items'][0]['members'][0]['in_period']
    assert (await listing(include_backlog=False))['total'] == 0
    yesterday = await event_list.list_events('owner', COMPONENT_ID,
        start_date='2026-10-11', end_date='2026-10-11', include_backlog=False)
    assert yesterday['metrics']['events_in_range'] == 1


async def test_date_boundaries_use_business_timezone():
    await install()
    for name, stamp in [('before', '2026-10-11T15:59:59Z'), ('start', '2026-10-11T16:00:00Z'),
                        ('last', '2026-10-12T15:59:59Z'), ('after', '2026-10-12T16:00:00Z')]:
        await observe(occurred(name, stamp))
    result = await listing(include_backlog=False, group=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['last', 'start']


async def test_business_date_boundary_handles_daylight_saving_without_fixed_offset():
    await install()
    await write('UPDATE monitor_installations SET policy=?',
                (encode({'timezone': 'America/New_York'}),))
    for name, stamp in [('before', '2026-03-08T04:59:59Z'), ('start', '2026-03-08T05:00:00Z'),
                        ('last', '2026-03-09T03:59:59Z'), ('after', '2026-03-09T04:00:00Z')]:
        await observe(occurred(name, stamp))
    result = await event_list.list_events('owner', COMPONENT_ID,
        start_date='2026-03-08', end_date='2026-03-08', include_backlog=False, group=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['last', 'start']


@pytest.mark.parametrize('times', [{'startTime': None, 'endTime': None}, {'startTime': 0, 'endTime': 0},
                                    {'startTime': 'not-a-time', 'endTime': []}])
async def test_unknown_occurrence_is_visible_at_end_and_never_uses_observed_time(times):
    await install()
    known = incident('known', hour=1)
    unknown = incident('unknown', **times)
    await observe(known, day='2026-10-10')
    await observe(unknown, day='2026-10-14')
    result = await listing(group=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['known', 'unknown']
    item = result['items'][1]['members'][0]
    assert item['occurrence_time_unknown'] and item['occurrence_at'] is None
    assert item['event_at'] is None and item['event_end_at'] is None
    assert not item['in_period'] and result['metrics']['unknown_time_events'] == 1
    assert (await listing(group=False, include_backlog=False))['total'] == 1


async def test_missing_latest_time_uses_source_first_occurrence():
    await install()
    item = incident('first-only', endTime=None)
    await observe(item, day='2026-10-14')
    result = await listing(include_backlog=False)
    member = result['items'][0]['members'][0]
    assert member['occurrence_at'] == member['event_at']
    assert not member['occurrence_time_unknown']


async def test_candidate_limit_keeps_latest_source_events_instead_of_latest_observations(monkeypatch):
    await install()
    for name, hour, day in [('newest', 6, '2026-10-10'), ('middle', 4, '2026-10-11'),
                             ('oldest', 1, '2026-10-13')]:
        await observe(incident(name, hour=hour), day=day)
    await observe(incident('unknown', startTime=None, endTime=None), day='2026-10-14')
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 2)
    result = await listing(group=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['newest', 'middle']
    assert result['truncated'] and '发生时间最近' in result['warnings'][0]


async def test_locally_ended_backlog_does_not_consume_candidate_limit(monkeypatch):
    await install()
    old = occurred('open', '2026-10-10T01:00:00Z')
    ended = occurred('ended', '2026-10-11T01:00:00Z')
    for item in (old, ended):
        await observe(item)
    await write('INSERT INTO monitor_event_followup(owner,scope,project,device,event_key,policy_revision,event_version,event,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('owner', COMPONENT_ID, 'project', ended['device'], ended['key'], 'synthetic-policy',
                 followup.event_version(ended), encode(ended), 'monitoring_ended', DAY+'T01:00:00Z', DAY+'T01:00:00Z'))
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 1)
    result = await listing(group=False)
    assert result['items'][0]['members'][0]['id'] == 'open'
    assert not result['truncated']
    assert not await rows('SELECT * FROM monitor_dispositions')
