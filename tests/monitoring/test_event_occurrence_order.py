"""First detection and latest monitor processing are distinct audit clocks."""
from datetime import datetime

import pytest

from flocks.monitoring import event_list, followup
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import encode, rows, write
from test_event_list import DAY, incident, install, listing, observe


async def test_global_order_and_pagination_follow_first_detection_not_source_activity_or_refresh():
    await install()
    older = incident('older-detection', hour=6)
    newer = incident('newer-detection', hour=1)
    await observe(older, stamp=DAY+'T01:00:00Z')
    await observe(newer, stamp=DAY+'T03:00:00Z')
    await observe(older, stamp=DAY+'T05:00:00Z', suffix='refresh')
    first = await listing(group=False, limit=1)
    second = await listing(group=False, limit=1, offset=1)
    assert first['items'][0]['members'][0]['id'] == newer['id']
    assert second['items'][0]['members'][0]['id'] == older['id']
    assert first['has_more'] and not second['has_more']
    assert second['items'][0]['last_processed_at'].startswith(DAY+'T05:00:00')
    ascending = await listing(group=False, sort_order='asc')
    assert [item['members'][0]['id'] for item in ascending['items']] == [older['id'], newer['id']]


@pytest.mark.parametrize('sort_order,expected', [('desc', ['earliest', 'latest', 'middle']), ('asc', ['middle', 'latest', 'earliest'])])
async def test_processing_sort_is_global_before_page_slice(sort_order, expected):
    await install()
    for name, hour, processed in [('earliest', 1, 6), ('middle', 2, 4), ('latest', 3, 5)]:
        item = incident(name)
        await observe(item, stamp=DAY+f'T{hour:02}:00:00Z')
        await observe(item, stamp=DAY+f'T{processed:02}:00:00Z', suffix='recheck')
    output = []
    for offset in range(3):
        page = await listing(group=False, limit=1, offset=offset, sort_by='last_processed_at', sort_order=sort_order)
        output.append(page['items'][0]['members'][0]['id'])
    assert output == expected


async def test_group_clocks_and_primary_use_latest_child_detection_while_span_uses_source_time():
    await install()
    older = incident('older-source', hour=1)
    latest = incident('latest-source', hour=6)
    await observe(latest, stamp=DAY+'T01:00:00Z')
    await observe(older, stamp=DAY+'T03:00:00Z')
    await observe(latest, stamp=DAY+'T06:00:00Z', suffix='refresh')
    item = (await listing())['items'][0]
    assert item['grouped'] and item['primary_event_key'] == older['key']
    assert item['first_seen_at'].startswith(DAY+'T03:00:00')
    assert item['last_processed_at'].startswith(DAY+'T06:00:00')
    assert [member['id'] for member in item['members']] == ['older-source', 'latest-source']
    sorted_by_processing = (await listing(sort_by='last_processed_at'))['items'][0]
    assert sorted_by_processing['primary_event_key'] == older['key']
    assert [member['id'] for member in sorted_by_processing['members']] == ['latest-source', 'older-source']
    # Display sorting does not relax the 24-hour source-activity merge limit.
    old_time = int(datetime.fromisoformat('2026-10-09T01:00:00+00:00').timestamp())
    await observe(incident('separate', startTime=old_time, endTime=old_time), stamp=DAY+'T04:00:00Z')
    assert (await listing())['total'] == 2


async def test_date_belongs_to_first_detection_and_refresh_cannot_reassign_it():
    await install()
    old_source = incident('old-source', startTime=1, endTime=1)
    await observe(old_source, day='2026-10-11')
    await observe(old_source, day=DAY, suffix='refresh')
    today = await listing(include_backlog=False)
    assert today['total'] == 0
    previous = await event_list.list_events('owner', COMPONENT_ID,
        start_date='2026-10-11', end_date='2026-10-11', include_backlog=False)
    assert previous['metrics']['events_in_range'] == 1
    assert (await listing())['metrics']['backlog_events'] == 1
    assert previous['items'][0]['last_processed_at'].startswith(DAY)


@pytest.mark.parametrize('zone,day,stamps', [
    ('Asia/Shanghai', DAY, ['2026-10-11T15:59:59Z', '2026-10-11T16:00:00Z', '2026-10-12T15:59:59Z', '2026-10-12T16:00:00Z']),
    ('America/New_York', '2026-03-08', ['2026-03-08T04:59:59Z', '2026-03-08T05:00:00Z', '2026-03-09T03:59:59Z', '2026-03-09T04:00:00Z']),
])
async def test_detection_date_boundaries_use_business_zone_including_daylight_saving(zone, day, stamps):
    await install()
    await write('UPDATE monitor_installations SET policy=?', (encode({'timezone': zone}),))
    for name, stamp in zip(['before', 'start', 'last', 'after'], stamps):
        await observe(incident(name), stamp=stamp)
    result = await event_list.list_events('owner', COMPONENT_ID,
        start_date=day, end_date=day, include_backlog=False, group=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['last', 'start']


@pytest.mark.parametrize('times', [{'startTime': None, 'endTime': None}, {'startTime': 0, 'endTime': 0}, {'startTime': 'not-a-time', 'endTime': []}])
async def test_unknown_source_occurrence_keeps_real_detection_time_and_stays_independent(times):
    await install()
    await observe(incident('known'), stamp=DAY+'T01:00:00Z')
    await observe(incident('unknown', **times), stamp=DAY+'T03:00:00Z')
    result = await listing(include_backlog=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['unknown', 'known']
    item = result['items'][0]['members'][0]
    assert item['occurrence_time_unknown'] and item['occurrence_at'] is None
    assert item['first_seen_at'].startswith(DAY+'T03:00:00')
    assert item['in_period'] and result['metrics']['unknown_time_events'] == 1


async def test_processing_clock_uses_actual_audit_update_and_does_not_advance_on_read():
    from test_event_list import investigate, mark
    await install()
    item = incident('processed')
    await observe(item, stamp=DAY+'T01:00:00Z')
    await investigate(item)
    first = (await listing())['items'][0]['members'][0]
    assert first['last_processed_at'].startswith(DAY+'T13:00:00')
    assert first['last_processed_source'] == 'investigation'
    await mark(item)
    second = (await listing())['items'][0]['members'][0]
    assert second['last_processed_at'].startswith(DAY+'T14:00:00')
    assert second['last_processed_source'] == 'disposition'
    assert (await listing())['items'][0]['members'][0]['last_processed_at'] == second['last_processed_at']


async def test_candidate_limit_uses_first_detection_and_reports_truncation(monkeypatch):
    await install()
    for name, stamp, hour in [('newest', '06', 1), ('middle', '04', 3), ('oldest', '01', 6)]:
        await observe(incident(name, hour=hour), stamp=DAY+f'T{stamp}:00:00Z')
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 2)
    result = await listing(group=False)
    assert [item['members'][0]['id'] for item in result['items']] == ['newest', 'middle']
    assert result['truncated'] and '首次检出由近到远' in result['warnings'][0]


async def test_locally_ended_backlog_does_not_consume_candidate_limit(monkeypatch):
    await install()
    old, ended = incident('open'), incident('ended')
    for item, day in [(old, '2026-10-10'), (ended, '2026-10-11')]:
        await observe(item, day=day)
    await write('INSERT INTO monitor_event_followup(owner,scope,project,device,event_key,policy_revision,event_version,event,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('owner', COMPONENT_ID, 'project', ended['device'], ended['key'], 'synthetic-policy',
                 followup.event_version(ended), encode(ended), 'monitoring_ended', DAY+'T01:00:00Z', DAY+'T01:00:00Z'))
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 1)
    result = await listing(group=False)
    assert result['items'][0]['members'][0]['id'] == 'open'
    assert not result['truncated']
    assert not await rows('SELECT * FROM monitor_dispositions')


@pytest.mark.parametrize('sort_by,sort_order,expected', [
    ('first_seen_at', 'asc', ['oldest', 'middle']),
    ('last_processed_at', 'desc', ['oldest', 'middle']),
    ('last_processed_at', 'asc', ['newest', 'middle']),
])
async def test_bounded_candidate_selection_respects_requested_global_sort(monkeypatch, sort_by, sort_order, expected):
    from test_event_list import investigate
    await install()
    for name, detected, processed in [('oldest', 1, 9), ('middle', 3, 8), ('newest', 6, 7)]:
        event = incident(name)
        await observe(event, stamp=DAY+f'T{detected:02}:00:00Z')
        await investigate(event)
        await write('UPDATE monitor_investigations SET updated_at=? WHERE event_key=?',
                    (DAY+f'T{processed:02}:00:00Z', event['key']))
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 2)
    result = await listing(group=False, sort_by=sort_by, sort_order=sort_order)
    assert result['truncated']
    assert [item['members'][0]['id'] for item in result['items']] == expected
