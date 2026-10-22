"""One source-time row per verified card, alongside the unchanged audit."""
from copy import deepcopy

from flocks.monitoring.event_details import build_story, readable_story, timeline_projection
from flocks.monitoring.investigation import fingerprint


EVENT = {'id': 'incident-one', 'device': 'xdr-a', 'name': '疑似DNS服务器-转发多个病毒的恶意请求',
         'host': '192.0.2.130', 'startTime': 1783830606, 'endTime': 1792540216}


def proof(facts, **updates):
    return {'id': 'evidence-list', 'event': EVENT['id'], 'device': EVENT['device'],
            'event_revision': fingerprint(EVENT), 'source_name': '测试XDR', 'success': True,
            'kind': 'alerts', 'facts': facts, 'query': {'action': 'list'}, **updates}


def card(identifier, fields, **updates):
    return {'id': f'card-{identifier}', 'alert_id': identifier, 'fields': [
        {'key': key, 'value': value} for key, value in fields.items()],
        'kind': 'network', 'summary': '192.0.2.130访问198.51.100.1并查询sample.invalid',
        'source': '同名XDR', 'evidence_id': 'evidence-list', 'records': [], **updates}


def test_deployed_dns_shape_projects_six_cards_once_after_list_and_proof_merging():
    # The deployed sample has five listed alerts, five individual proofs (one
    # outside that list), six cards and ten source records. Addresses/IDs here
    # are synthetic; the first/last time and overlap shape reproduce the case.
    names = ['fakehunter', 'phishing', '感染钓鱼软件家族phishing', 'coinminer', 'phishing', 'phishing']
    rows = [{'alertId': f'alert-{index}', 'name': names[index], 'srcIp': [EVENT['host']],
             'dstIp': [f'198.51.100.{index + 1}'], 'dstPort': [53],
             'firstTime': 1790000000 + index, 'lastTime': 1792000000 + index}
            for index in range(5)]
    proofs = [proof({'data': {'item': rows}})]
    for index in (0, 2, 3, 4, 5):
        proofs.append(proof({'data': {'name': names[index], 'srcIp': [EVENT['host']],
            'dstIp': [f'198.51.100.{index + 1}'], 'dstPort': [53], 'domain': 'sample.invalid',
            'firstTime': 1789999900 + index, 'lastTime': 1792000100 + index}},
            id=f'evidence-proof-{index}', kind='alert_proof',
            query={'action': 'get_proof', 'uuid': f'alert-{index}'}))
    story = build_story(EVENT, proofs)
    assert len(story['evidence_cards']) == len(story['alert_timeline']) == 6
    assert len({entry['alert_id'] for entry in story['alert_timeline']}) == 6
    assert len(story['timeline']) > 6  # Existing audit/consumer contract stays intact.
    assert sum(len(entry['evidence_ids']) for entry in story['alert_timeline']) == 10
    assert {entry['card_id'] for entry in story['alert_timeline']} == {item['id'] for item in story['evidence_cards']}
    first = next(entry for entry in story['alert_timeline'] if entry['alert_id'] == 'alert-0')
    assert first['first_time'] == 1789999900 and first['end_time'] == 1792000100
    assert story['event_time_range'] == {'first_time': EVENT['startTime'], 'end_time': EVENT['endTime']}
    cards = {item['id']: item for item in story['evidence_cards']}
    assert all(entry['text'] == cards[entry['card_id']]['summary'] for entry in story['alert_timeline'])
    assert '192.0.2.130 访问 198.51.100.1' in first['text'] and 'fakehunter' in first['text']
    overview = readable_story(story)
    assert overview['alert_timeline'] == story['alert_timeline']
    assert overview['event_time_range'] == story['event_time_range']


def test_missing_source_range_sides_remain_unknown_and_query_times_never_date_activity():
    stories = {'basics': {'start_time': 1783830606, 'end_time': 1792540216}, 'evidence_cards': [
        card('only-end', {'name': '检测', 'end_time': 1792000000, 'alert_time': 1792000000}),
        card('only-start', {'name': '检测', 'start_time': 1790000000, 'alert_time': 1790000000}),
        card('observation-time', {'name': '检测', 'alert_time': '2026-10-20T08:00:00Z'}),
        card('no-time', {'file_created_at': '2020-01-01T00:00:00Z'}, time=None,
             retrieved_at='2026-10-21T01:00:00Z'),
    ]}
    rows = {entry['alert_id']: entry for entry in timeline_projection(stories)['alert_timeline']}
    assert rows['only-end']['first_time'] is None and rows['only-end']['end_time'] == 1792000000
    assert rows['only-start']['first_time'] == 1790000000 and rows['only-start']['end_time'] is None
    assert rows['observation-time']['first_time'] == rows['observation-time']['end_time'] == '2026-10-20T08:00:00Z'
    assert rows['no-time']['first_time'] is None and rows['no-time']['end_time'] is None


def test_equal_names_times_or_display_sources_do_not_merge_distinct_cards_or_unidentified_records():
    story = {'evidence_cards': [
        card('same-id', {'name': '相同名称', 'start_time': 1790000000}, id='device-a-card'),
        card('same-id', {'name': '相同名称', 'start_time': 1790000000}, id='device-b-card'),
        card('unused', {'name': '实体名称', 'start_time': 1790000000}, id='unknown-one', alert_id=None, kind='file'),
        card('unused', {'name': '实体名称', 'start_time': 1790000000}, id='unknown-two', alert_id=None, kind='file'),
    ]}
    before = deepcopy(story)
    rows = timeline_projection(story)['alert_timeline']
    assert len(rows) == 4 and len({entry['id'] for entry in rows}) == 4
    unknown = [entry for entry in rows if entry['kind'] == 'observation']
    assert len(unknown) == 2 and all('alert_id' not in entry for entry in unknown)
    assert all(entry['name'] == '取证记录' and '未提供源 IP 和目的 IP' in entry['text'] for entry in unknown)
    assert story == before


def test_file_creation_stays_separate_and_only_current_bound_queries_supply_other_events():
    current = proof({'fileName': 'current.bin', 'fileCreateTime': '2026-10-20T00:00:00Z'}, kind='file')
    # Reusing an evidence label must not let a different version borrow the
    # current query's authorization for its file creation node.
    outdated = proof({'fileName': 'old.bin', 'fileCreateTime': '2020-01-01T00:00:00Z'},
                     kind='file', event_revision='old-version')
    story = build_story(EVENT, [outdated, current])
    assert len(story['alert_timeline']) == 1
    assert story['alert_timeline'][0]['first_time'] is None and story['alert_timeline'][0]['end_time'] is None
    assert len(story['other_timeline']) == 1
    assert story['other_timeline'][0]['time'] == '2026-10-20T00:00:00Z'
    assert 'current.bin' in story['other_timeline'][0]['text'] and '不等于告警发生时间' in story['other_timeline'][0]['text']
    assert 'old.bin' not in str(story['other_timeline'])


def test_other_event_limit_applies_after_sorting_and_keeps_the_latest_source_node():
    events = [{'time': 1790000000 + index, 'text': f'文件创建 {index}'} for index in reversed(range(60))]
    projection = timeline_projection({'evidence_cards': []}, other_events=events)
    assert len(projection['other_timeline']) == 40 and projection['timeline_truncated']
    assert projection['other_timeline'][0]['time'] == 1790000000
    assert projection['other_timeline'][-1]['time'] == 1790000059
    assert readable_story({**projection, 'evidence_cards': []})['timeline_truncated']


def test_old_stories_keep_the_legacy_timeline_and_card_boundaries_remain_visible():
    legacy = {'basics': {'start_time': 1790000000},
              'timeline': [{'time': 1790000000, 'text': '原时间线'}]}
    overview = readable_story(legacy)
    assert 'alert_timeline' not in overview and overview['timeline'] == legacy['timeline']
    assert overview['event_time_range'] == {'first_time': 1790000000, 'end_time': None}
    row = timeline_projection({'evidence_cards': [card('alert-a', {'name': '检测'},
        partial=True, boundary='字段已截断，范围不完整。')]})['alert_timeline'][0]
    assert row['partial'] and row['boundary'] == '字段已截断，范围不完整。'


def test_timeline_rebuilds_old_bare_names_with_compact_addresses_without_using_xff_as_source():
    original = {'evidence_cards': [card('silverfox', {
        'name': 'silverfox', 'src_ip': ['192.0.2.130', '192.0.2.131'],
        'dst_ip': ['198.51.100.1', '198.51.100.2', '198.51.100.3'],
        'x_forwarded_for': '203.0.113.9', 'start_time': 1790000000, 'end_time': 1792000000,
        'attack_result': 1,
    }, summary='silverfox'), card('missing-source', {'name': 'fakewebsite', 'dst_ip': '198.51.100.8',
        'x_forwarded_for': '203.0.113.9'})]}
    before = deepcopy(original)
    rows = timeline_projection(original)['alert_timeline']
    assert rows[0]['text'] == '192.0.2.130 等 2 个 IP 访问 198.51.100.1 等 3 个 IP，触发「silverfox」告警。'
    assert '源 IP 未提供' in rows[1]['text'] and '198.51.100.8' in rows[1]['text']
    assert all('203.0.113.9' not in row['text'] and '成功' not in row['text'] for row in rows)
    assert original == before
