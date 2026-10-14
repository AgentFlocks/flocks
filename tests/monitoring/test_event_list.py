"""Event follow-up is a scoped read projection, never a disposition workflow."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from flocks.monitoring import event_list
from flocks.monitoring.investigation import fingerprint
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import encode, rows, write


DAY = '2026-10-12'


async def install(owner='owner', project='project', scope=COMPONENT_ID):
    await write('INSERT OR REPLACE INTO monitor_installations(owner,scope,project,policy) VALUES(?,?,?,?)',
        (owner, scope, project, encode({'timezone': 'Asia/Shanghai', 'device_names': {'a': '股份 XDR', 'b': '同方 XDR'}})))


def incident(identifier='one', device='a', host='192.0.2.10', hour=1, **updates):
    start = int(datetime(2026, 10, 12, hour, tzinfo=timezone.utc).timestamp())
    return {'key': f'{device}:incident:{identifier}', 'id': identifier, 'device': device,
        'name': 'SQL 注入事件', 'host': host, 'startTime': start, 'endTime': start,
        'risk': 'risk', 'dealStatus': 0, 'alertIds': [], **updates}


async def observe(event, *, owner='owner', project='project', scope=COMPONENT_ID, day=DAY, stamp=None, suffix=''):
    key = event['key']
    attempt = f'{owner}-{project}-{scope}-{key}-{day}{suffix}'
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (attempt, attempt, owner, project, scope, day, 'session-'+attempt, 'message-'+attempt,
         stamp or day+'T12:00:00+00:00', 'completed'))
    await write('INSERT INTO monitor_observations(attempt_id,event_key,data) VALUES(?,?,?)', (attempt, key, encode(event)))


async def investigate(event, *, values=None, state='ready', result=None, device=None, proof_event=None, partial=False, owner='owner', project='project', evidence=None):
    if evidence is None:
        evidence = [{'id': 'evidence-1', 'event': proof_event or event['id'], 'device': device or event['device'],
            'event_revision': fingerprint(event), 'kind': 'dns', 'success': True, 'partial': partial,
            'tool': 'sangfor_xdr_incidents', 'query': {'action': 'get_entities', 'uuid': event['id'], 'entity_type': 'dns'},
            'facts': {'data': {'item': [{'domain': value, 'threatLevel': 3} for value in values or ['BAD.Example.']]}}}]
    await write('INSERT INTO monitor_investigations(owner,project,event_key,event,evidence,result,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',
        (owner, project, event['key'], encode(event), encode(evidence), encode(result or {'verdict': 'risk', 'reason': '明确恶意域名通信'}), state, DAY+'T13:00:00Z'))


async def mark(event, *, owner='owner', project='project', scope=COMPONENT_ID, target=40, observed=40, status='verified'):
    await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,observed_status,created_at,updated_at,target_status,project) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        ('mark-'+event['key'], owner, scope, event['key'], '责任人已反馈', status, observed,
         DAY+'T14:00:00Z', DAY+'T14:00:00Z', target, project))


async def notice(event, *, state='sent', sent_at=DAY+'T12:30:00Z'):
    identifier = 'notice-'+event['key']
    await write('INSERT INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,created_at,updated_at,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identifier, 'owner', COMPONENT_ID, 'project', event['key'], 'owner@example.invalid', 'rev', 'box', identifier,
         '通知：单条事件', 'Authorization: Bearer PRIVATE\n关键证据', encode(event), state, DAY+'T12:00:00Z', DAY+'T12:30:00Z', sent_at))
    return identifier


async def reply(identifier, *, notice_ids=None, state='pending'):
    await write('INSERT INTO monitor_mail_replies(id,owner,project,mailbox,message_id,sender,payload,state,received_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (identifier, 'owner', 'project', 'box', identifier, 'owner@example.invalid', encode({'notice_ids': notice_ids, 'text': '已经处理好了'}), state, DAY+'T15:00:00Z', DAY+'T15:00:00Z'))


async def listing(**kwargs):
    return await event_list.list_events('owner', COMPONENT_ID, start_date=DAY, end_date=DAY, **kwargs)


async def test_same_device_same_type_groups_and_keeps_members():
    await install()
    for event in [incident('one'), incident('two', hour=3)]:
        await observe(event)
        await investigate(event)
    result = await listing()
    assert result['metrics']['events'] == 2 and result['metrics']['groups'] == 1
    assert result['items'][0]['member_count'] == 2
    assert result['items'][0]['ioc_matches'] == [{'type': 'domain', 'value': 'bad.example'}]
    assert result['window_hours'] == 24 and result['limit'] == 10
    before = await rows('SELECT * FROM monitor_observations')
    assert len(before) == 2
    assert await rows('SELECT * FROM monitor_mail_notices') == []
    assert await rows('SELECT * FROM monitor_dispositions') == []


@pytest.mark.parametrize('changed', [
    {'device': 'b'}, {'name': '另一类安全事件'}, {'name': ''}, {'startTime': None, 'endTime': None},
    {'startTime': 0, 'endTime': 0},
])
async def test_missing_identity_time_or_different_device_never_merges(changed):
    await install()
    first = incident('one')
    second = incident('two', **changed)
    for event in (first, second):
        await observe(event)
        await investigate(event)
    assert (await listing())['total'] == 2


async def test_whole_group_time_span_prevents_bridges_without_requiring_common_ioc():
    await install()
    for identifier, hour, iocs in [('one', 0, ['x.example']), ('two', 2, ['x.example', 'y.example']), ('three', 4, ['y.example'])]:
        event = incident(identifier, hour=hour)
        await observe(event)
        await investigate(event, values=iocs)
    result = await listing(window_hours=3)
    assert sorted(item['member_count'] for item in result['items']) == [1, 2]
    result = await listing(window_hours=6)
    assert sorted(item['member_count'] for item in result['items']) == [3]


async def test_repeat_observations_deduplicate_and_latest_disposition_updates_old_day():
    await install()
    occurred = int(datetime(2026, 10, 11, 1, tzinfo=timezone.utc).timestamp())
    event = incident(startTime=occurred, endTime=occurred)
    await observe(event, day='2026-10-11')
    await observe(event, suffix='again')
    await investigate(event)
    await mark(event)
    result = await event_list.list_events('owner', COMPONENT_ID, start_date='2026-10-11', end_date='2026-10-11')
    assert result['metrics']['events'] == 1
    member = result['items'][0]['members'][0]
    assert member['handling'] == 'closed' and member['handling_source'] == 'monitor_verified'
    assert member['last_seen_at'].startswith(DAY)
    detail = await event_list.detail('owner', COMPONENT_ID, event['key'])
    assert detail['event']['handling'] == 'closed'
    assert len(detail['observations']) == 2


async def test_current_project_owner_and_scope_do_not_leak():
    await install()
    shared = incident()
    await observe(shared)
    for owner, project, scope in [('other', 'project', COMPONENT_ID), ('owner', 'old', COMPONENT_ID), ('owner', 'project', 'other-scope')]:
        await observe(incident(owner+project+scope), owner=owner, project=project, scope=scope)
    await investigate(shared, owner='other', result={'reason': 'OTHER_SECRET'})
    result = await listing()
    assert result['metrics']['events'] == 1 and 'OTHER_SECRET' not in encode(result)
    for key in ['a:incident:otherproject'+COMPONENT_ID, 'a:incident:ownerold'+COMPONENT_ID, 'a:incident:ownerprojectother-scope']:
        with pytest.raises(LookupError):
            await event_list.detail('owner', COMPONENT_ID, key)


@pytest.mark.parametrize('kind', ['failed', 'other_device', 'other_event', 'wrong_revision', 'safe', 'host_ip'])
async def test_presentation_groups_never_promote_untrusted_iocs_to_facts(kind):
    await install()
    for identifier in ('one', 'two'):
        event = incident(identifier)
        proof = {'id': 'evidence-1', 'event': event['id'], 'device': 'a', 'event_revision': fingerprint(event),
            'kind': 'dns', 'success': True, 'partial': False,
            'query': {'action': 'get_entities', 'uuid': event['id'], 'entity_type': 'dns'},
            'facts': {'data': {'item': [{'domain': 'bad.example', 'threatLevel': 3}]}}}
        if kind == 'failed':
            proof['success'] = False
        elif kind == 'other_device':
            proof['device'] = 'b'
        elif kind == 'other_event':
            proof['event'] = 'unrelated'
        elif kind == 'wrong_revision':
            proof['event_revision'] = 'old'
        elif kind == 'safe':
            proof['facts']['data']['item'][0]['threatLevel'] = 1
        elif kind == 'host_ip':
            proof.update(kind='ip', query={'action': 'get_entities', 'uuid': event['id'], 'entity_type': 'ip'},
                facts={'data': {'item': [{'ip': event['host'], 'threatLevel': 3}]}})
        await observe(event)
        await investigate(event, evidence=[proof])
    result = await listing()
    assert result['metrics']['groups'] == 1
    assert result['items'][0]['ioc_matches'] == []
    assert all(m['assessment']['status'] == 'insufficient' for m in result['items'][0]['members'])


async def test_canonical_host_and_current_revision_must_match():
    await install()
    for identifier in ('one', 'two'):
        old = incident(identifier)
        await observe(old)
        await investigate(old)
        await observe({**old, 'host': '192.0.2.20'}, suffix='new')
    result = await listing()
    assert result['total'] == 1
    assert all(member['investigation']['state'] == 'stale' for item in result['items'] for member in item['members'])
    detail = await event_list.detail('owner', COMPONENT_ID, 'a:incident:one')
    assert detail['investigation']['state'] == 'stale'
    assert detail['investigation']['result']['verdict'] == 'unknown'
    assert detail['investigation']['historical_result']['verdict'] == 'risk'


async def test_backlog_keeps_old_unhandled_and_contained_not_terminal_or_future():
    await install()
    for identifier, status, day in [('old-open', 0, '2026-10-10'), ('old-contained', 70, '2026-10-10'),
                                  ('old-closed', 40, '2026-10-10'), ('old-ignored', 60, '2026-10-10'),
                                  ('today', 0, DAY), ('future', 0, '2026-10-13')]:
        occurred = int(datetime.fromisoformat(day+'T01:00:00+00:00').timestamp())
        await observe(incident(identifier, dealStatus=status, startTime=occurred, endTime=occurred), day=day)
    result = await listing()
    assert result['metrics']['events_in_range'] == 1 and result['metrics']['backlog_events'] == 2
    assert {m['id'] for item in result['items'] for m in item['members']} == {'old-open', 'old-contained', 'today'}
    no_backlog = await listing(include_backlog=False)
    assert no_backlog['metrics']['events'] == 1


async def test_member_status_counts_do_not_close_whole_group_from_one_member():
    await install()
    for identifier in ('one', 'two'):
        event = incident(identifier)
        await observe(event)
        await investigate(event)
        if identifier == 'one':
            await mark(event)
    result = await listing()
    item = result['items'][0]
    assert item['handling'] == 'open' and item['closure_counts']['closed'] == 1 and item['closure_counts']['open'] == 1
    assert result['metrics']['closed'] == 1 and result['metrics']['open'] == 1


async def test_mail_pending_and_failed_stay_open_reply_count_only_unambiguous():
    await install()
    first, second = incident('one'), incident('two')
    for event in (first, second):
        await observe(event)
    n1, n2 = await notice(first), await notice(second, state='failed', sent_at=None)
    await reply('ambiguous', notice_ids=[n1, n2])
    result = await listing()
    assert result['metrics']['open'] == 2 and result['metrics']['mail_replied'] == 0
    await reply('matched', notice_ids=[n1])
    result = await listing()
    assert result['metrics']['mail_replied'] == 1
    assert sum(i['mail']['failed'] for i in result['items']) == 1
    detail = await event_list.detail('owner', COMPONENT_ID, first['key'])
    assert len(detail['notice']['replies']) == 1 and 'PRIVATE' not in encode(detail)


@pytest.mark.parametrize('evidence', [[], ['not-json'], {'x': 'abc'}, [{'kind': 'dns', 'success': True, 'facts': {'data': {'item': 'bad'}}}], [{'kind': 'dns', 'success': True, 'facts': {'data': {'item': ['bad']}}}]])
async def test_legacy_bad_shapes_do_not_crash_projection(evidence):
    await install()
    event = incident(basics=['not-a-dict'])
    await observe(event)
    await investigate(event, evidence=evidence)
    assert (await listing())['metrics']['events'] == 1


@pytest.mark.parametrize('kind', [[], {}])
async def test_malformed_proof_kind_keeps_list_and_detail_available(kind):
    await install()
    event = incident()
    await observe(event)
    proof = {'id': 'evidence-invalid', 'event': event['id'], 'device': event['device'],
        'event_revision': fingerprint(event), 'kind': kind, 'success': True,
        'facts': {'data': {'item': [{'domain': 'invalid.example', 'threatLevel': 3}]}}}
    valid = {**proof, 'id': 'evidence-valid', 'kind': 'dns',
        'query': {'action': 'get_entities', 'uuid': event['id'], 'entity_type': 'dns'},
        'facts': {'data': {'item': [{'domain': 'BAD.Example.', 'threatLevel': 3}]}}}
    await investigate(event, evidence=[proof, valid])
    listed = (await listing())['items'][0]['members'][0]
    detailed = (await event_list.detail('owner', COMPONENT_ID, event['key']))['event']
    assert listed['handling'] == detailed['handling'] == 'open'
    assert [item['value'] for item in listed['iocs']] == ['bad.example']
    assert [item['value'] for item in detailed['iocs']] == ['bad.example']


@pytest.mark.parametrize('target', [[], {}, '40', None])
def test_malformed_target_status_cannot_establish_closure(target):
    from zoneinfo import ZoneInfo
    disposition = {'status': 'verified', 'target_status': target, 'observed_status': 40,
        'updated_at': DAY+'T14:00:00Z'}
    assert event_list._handling({'dealStatus': 0}, disposition, DAY+'T12:00:00Z',
        ZoneInfo('Asia/Shanghai')) == 'open'


async def test_invalid_json_or_oversized_detail_is_explicit(monkeypatch):
    await install()
    event = incident()
    await observe(event)
    await investigate(event)
    await write('UPDATE monitor_investigations SET result=?', ('not-json',))
    assert (await listing())['truncated']
    monkeypatch.setattr(event_list, 'MAX_DETAIL_JSON', 10)
    detail = await event_list.detail('owner', COMPONENT_ID, event['key'])
    assert detail['truncated'] and detail['warnings']


async def test_pagination_filters_and_bound_counts(monkeypatch):
    await install()
    for index in range(4):
        await observe(incident(str(index), device='b' if index == 3 else 'a', dealStatus=40 if index == 1 else 0))
    result = await listing(group=False, limit=2)
    assert result['total'] == 4 and result['has_more'] and len(result['items']) == 2
    assert (await listing(handling='closed'))['metrics']['events'] == 1
    assert (await listing(device='b', search='sql'))['metrics']['events'] == 1
    assert (await listing(search="' OR 1=1 --"))['metrics']['events'] == 0
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 2)
    limited = await listing()
    assert limited['truncated'] and limited['metrics']['events'] == 2
    assert '仅覆盖已加载事件' in limited['warnings'][0]


@pytest.mark.parametrize('kwargs', [{'window_hours': 0}, {'window_hours': 25}, {'window_hours': True}, {'limit': 0}, {'offset': -1}, {'handling': 'fake'}, {'risk': 'fake'}, {'search': 'x'*201}])
async def test_invalid_query_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        await listing(**kwargs)


@pytest.mark.parametrize('start,end', [('2026-02-30', DAY), ('2026-10-13', DAY), ('2024-01-01', DAY)])
async def test_invalid_ranges_are_rejected(start, end):
    with pytest.raises(ValueError):
        await event_list.list_events('owner', COMPONENT_ID, start_date=start, end_date=end)


async def test_read_projection_does_not_initialize_or_migrate_missing_database(monkeypatch, tmp_path):
    from flocks.task.store import TaskStore
    missing = tmp_path/'missing.sqlite'
    monkeypatch.setattr(TaskStore, 'get_db_path', classmethod(lambda cls: missing))
    result = await listing()
    assert result['items'] == [] and not Path(missing).exists()
    with pytest.raises(LookupError):
        await event_list.detail('owner', COMPONENT_ID, 'a:incident:one')


async def test_contained_native_readback_mapping_and_source_status_origin():
    await install()
    event = incident()
    await observe(event)
    await mark(event, target=70, observed=30)
    result = await listing()
    member = result['items'][0]['members'][0]
    assert member['handling'] == 'contained' and member['handling_source'] == 'monitor_verified'
    source = incident('already-closed', dealStatus=40)
    await observe(source)
    assert (await event_list.detail('owner', COMPONENT_ID, source['key']))['event']['handling_source'] == 'xdr'


async def test_legal_json_wrong_enum_shapes_do_not_fail_whole_list():
    await install()
    event = incident(risk=[], dealStatus=[], basics=['bad'])
    await observe(event)
    await investigate(event, result={'verdict': [], 'reason': ['bad']})
    await mark(event)
    result = await listing()
    member = result['items'][0]['members'][0]
    assert member['risk'] == 'unknown' and member['handling'] == 'closed'


async def test_detail_history_limit_does_not_change_actual_first_seen(monkeypatch):
    await install()
    event = incident()
    for day in ('2026-10-09', '2026-10-10', '2026-10-11', DAY):
        await observe(event, day=day)
    monkeypatch.setattr(event_list, 'MAX_DETAIL_ROWS', 2)
    result = await event_list.detail('owner', COMPONENT_ID, event['key'])
    assert result['event']['first_seen_at'].startswith('2026-10-09')
    assert result['truncated'] and len(result['observations']) == 2


async def test_known_closed_history_does_not_use_up_backlog_candidate_limit(monkeypatch):
    await install()
    old = int(datetime(2026, 10, 1, 1, tzinfo=timezone.utc).timestamp())
    closed = int(datetime(2026, 10, 11, 1, tzinfo=timezone.utc).timestamp())
    await observe(incident('old-open', startTime=old, endTime=old), day='2026-10-01')
    for index in range(3):
        await observe(incident('closed-'+str(index), dealStatus=40, startTime=closed, endTime=closed), day='2026-10-11')
    monkeypatch.setattr(event_list, 'MAX_EVENTS', 2)
    result = await listing()
    assert result['metrics']['backlog_events'] == 1
    assert result['items'][0]['members'][0]['id'] == 'old-open'
    assert not result['truncated']


@pytest.mark.parametrize('key', ['session_key', 'refresh_token', 'id_token', 'client_secret', 'credential', 'Proxy-Authorization', 'X-Auth-Code'])
def test_recursive_redaction_covers_header_and_structured_credential_keys(key):
    value = {'old': {key: 'PRIVATE'}, 'packets': [{'text': 'Authorization: Bearer PRIVATE\nHost: example.invalid'}]}
    assert 'PRIVATE' not in encode(event_list._redacted(value))


async def test_ip_and_host_normalization_are_exact_and_do_not_expand_subnets():
    await install()
    for identifier, host, ip in [('one', '2001:0db8::1', '2001:0db8:0000::9'),
                                 ('two', '2001:db8:0:0::1', '2001:db8::9'),
                                 ('three', '2001:db8::2', '2001:db8::9')]:
        event = incident(identifier, host=host)
        await observe(event)
        proof = {'id': 'evidence-1', 'event': identifier, 'device': 'a', 'event_revision': fingerprint(event),
            'kind': 'ip', 'success': True, 'partial': False,
            'query': {'action': 'get_entities', 'uuid': identifier, 'entity_type': 'ip'},
            'facts': {'data': {'item': [{'ip': ip, 'threatLevel': 3}]}}}
        await investigate(event, evidence=[proof])
    result = await listing()
    assert sorted(item['member_count'] for item in result['items']) == [3]
    assert result['items'][0]['host_count'] == 2
    assert result['items'][0]['ioc_matches'] == [{'type': 'ip', 'value': '2001:db8::9'}]


async def test_large_packets_not_read_in_list_projection_and_ioc_cap_is_explicit():
    await install()
    event = incident()
    await observe(event)
    await investigate(event, values=[f'bad-{index}.example' for index in range(60)])
    await write('UPDATE monitor_observations SET data=json_set(data,\'$.story\',?)', ('PRIVATE_PACKET'*50_000,))
    result = await listing()
    assert result['truncated']
    assert len(result['items'][0]['members'][0]['iocs']) == 50
    assert 'PRIVATE_PACKET' not in encode(result)


async def test_same_type_across_hosts_and_unjudged_events_are_visible_without_iocs():
    await install()
    for event in [incident('one', host='192.0.2.11', risk='benign'),
                  incident('two', host='192.0.2.12', risk='unknown'),
                  incident('three', device='b', host='192.0.2.13', risk='ignored')]:
        await observe(event)
    result = await listing()
    assert result['metrics']['events'] == 3
    assert sorted(item['member_count'] for item in result['items']) == [1, 2]
    grouped = next(item for item in result['items'] if item['grouped'])
    assert grouped['host_count'] == 2
    assert grouped['assessment_counts']['insufficient'] == 2
    assert await rows('SELECT * FROM monitor_dispositions') == []


async def test_mixed_assessments_remain_separate_in_group_and_filters_match_original_members():
    await install()
    for identifier, verdict, status in [('risk', 'risk', 0), ('closed', 'risk', 40),
                                         ('safe', 'benign', 0), ('uncertain', 'unknown', 0)]:
        event = incident(identifier, dealStatus=status)
        proof = {'id': 'evidence-1', 'event': identifier, 'device': 'a', 'success': True,
                 'event_revision': fingerprint(event), 'kind': 'alert_proof',
                 'facts': {'data': {'requestBody': 'UNION SELECT value FROM users'}}}
        await observe(event)
        await investigate(event, evidence=[proof], result={'verdict': verdict,
            'reason': '原始请求与调查结论对应', 'confidence': 'high',
            'risk_confirmed': verdict == 'risk', 'blocking_gaps': [], 'evidence_ids': ['evidence-1']})
    result = await listing()
    item = result['items'][0]
    assert item['assessment_status'] == item['members'][0]['assessment']['status'] == 'insufficient'
    expected_counts = {'risk_open': 1, 'risk_closed': 1, 'benign': 1, 'insufficient': 1}
    if 'ended' in item['assessment_counts']:
        expected_counts['ended'] = 0
    assert item['assessment_counts'] == expected_counts
    assert result['metrics']['assessment_counts'] == item['assessment_counts']
    for state, count in item['assessment_counts'].items():
        if not count:
            continue
        selected = await listing(assessment=state)
        assert selected['metrics']['events'] == 1
        member = selected['items'][0]['members'][0]
        assert member['assessment']['status'] == state
        detailed = await event_list.detail('owner', COMPONENT_ID, member['key'])
        assert detailed['event']['assessment'] == member['assessment']
        assert detailed['overview']['conclusion'] and detailed['story']['key_evidence']


async def test_type_group_does_not_chain_across_more_than_24_hours():
    await install()
    start = incident('one')['startTime']
    for identifier, hours in [('one', 0), ('two', 20), ('three', 40)]:
        await observe(incident(identifier, startTime=start+hours*3600, endTime=start+hours*3600))
    result = await event_list.list_events('owner', COMPONENT_ID, start_date=DAY, end_date='2026-10-14')
    assert sorted(item['member_count'] for item in result['items']) == [1, 2]


async def test_compact_assessment_does_not_return_large_packet_data():
    await install()
    event = incident()
    await observe(event)
    proof = {'id': 'evidence-1', 'event': event['id'], 'device': event['device'], 'success': True,
             'event_revision': fingerprint(event), 'kind': 'alert_proof',
             'facts': {'requestBody': 'PRIVATE_PAYLOAD' * 100_000}}
    await investigate(event, evidence=[proof], result={'verdict': 'risk', 'reason': '已证实恶意请求',
        'confidence': 'high', 'risk_confirmed': True, 'blocking_gaps': [], 'evidence_ids': ['evidence-1']})
    result = await listing()
    assert result['items'][0]['members'][0]['assessment']['status'] == 'risk_open'
    assert 'PRIVATE_PAYLOAD' not in encode(result)


async def test_status_only_refresh_keeps_confirmed_risk_closed_in_list_and_detail():
    await install()
    old = incident(basics={'deal_status': 0})
    await observe(old)
    proof = {'id': 'evidence-1', 'event': old['id'], 'device': 'a', 'success': True,
             'event_revision': fingerprint(old), 'kind': 'alert_proof',
             'facts': {'requestBody': 'UNION SELECT value FROM users'}}
    await investigate(old, evidence=[proof], result={'verdict': 'risk', 'reason': '已确认恶意请求',
        'confidence': 'high', 'risk_confirmed': True, 'blocking_gaps': [], 'evidence_ids': ['evidence-1']})
    completed = {**old, 'dealStatus': 40, 'basics': {'deal_status': 40}}
    await observe(completed, suffix='completed')
    member = (await listing())['items'][0]['members'][0]
    assert member['assessment']['status'] == 'risk_closed'
    assert member['investigation']['state'] == 'ready' and member['investigation']['is_current']
    detailed = await event_list.detail('owner', COMPONENT_ID, old['key'])
    assert detailed['event']['assessment'] == member['assessment']
    assert detailed['investigation']['state'] == 'ready'


@pytest.mark.parametrize('facts', ['not-json', None, 123, ['unstructured']])
async def test_malformed_fact_payload_is_insufficient_not_a_list_failure(facts):
    await install()
    event = incident()
    await observe(event)
    await investigate(event, evidence=[{'id': 'evidence-1', 'event': event['id'], 'device': 'a',
        'event_revision': fingerprint(event), 'success': True, 'facts': facts}], result={'verdict': 'risk',
        'reason': '无法核实来源', 'confidence': 'high', 'risk_confirmed': True,
        'blocking_gaps': [], 'evidence_ids': ['evidence-1']})
    member = (await listing())['items'][0]['members'][0]
    assert member['assessment']['status'] == 'insufficient'
