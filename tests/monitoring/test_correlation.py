"""A successful request, an empty response and a corroborating fact differ."""
import json

import pytest

from flocks.monitoring.correlation import correlation_results, response_metadata
from flocks.monitoring.event_details import build_story, readable_story
from flocks.monitoring.investigation import fingerprint

EVENT = {'id': 'incident-one', 'device': 'xdr-a', 'host': '192.0.2.10',
         'key': 'xdr-a:incident:incident-one', 'startTime': 1791860154, 'endTime': 1791860390}


def proof(facts, **updates):
    return {'id': 'evidence-1', 'event': EVENT['id'], 'event_revision': fingerprint(EVENT),
            'device': 'tdp-a', 'source_name': 'Fixture TDP', 'product': 'tdp',
            'tool': 'tdp_log_search', 'kind': 'related', 'success': True, 'partial': False,
            'query': {'action': 'search', 'time_from': 1791860154, 'time_to': 1791860390},
            'facts': facts, **updates}


def plan(**updates):
    return {'event': EVENT['id'], 'event_device': EVENT['device'], 'event_revision': fingerprint(EVENT),
            'device': 'tdp-a', 'source': 'Fixture TDP', 'tool': 'tdp_log_search', 'product': 'tdp',
            'status': 'not_queried', 'reason': '本轮尚未查询', **updates}


@pytest.mark.parametrize('raw', [[], {'items': []}, {'data': {'list': [], 'total': 0}}, {'total': 0}])
def test_explicit_empty_search_schema_can_establish_no_match(raw):
    meta = response_metadata(raw, 'tdp')
    assert meta['schema'] == 1 and meta['explicit_empty'] and not meta['partial']
    row = correlation_results(EVENT, [proof(raw, result_meta=meta)])[0]
    assert row['status'] == 'no_match' and not row['partial']
    assert row.get('returned_count') == 0 or row.get('total_count') == 0


@pytest.mark.parametrize('raw', [{}, None, '', {'metadata': {'total': 0}},
                              {'total': 0, 'records': [{'name': 'present'}]},
                              {'total': 0, 'unknown_records': ['present']},
                              {'total': 0, 'partial': True}, {'total': False}])
def test_unknown_contradictory_or_partial_responses_never_establish_no_match(raw):
    meta = response_metadata(raw, 'tdp')
    assert not meta['explicit_empty']
    assert correlation_results(EVENT, [proof(raw, result_meta=meta)])[0]['status'] != 'no_match'


def test_legacy_no_facts_does_not_become_an_empty_query_or_invent_a_zero_count():
    row = correlation_results(EVENT, [proof({})])[0]
    assert row['status'] == 'unknown' and 'returned_count' not in row and 'total_count' not in row
    legacy = readable_story({'other_queries': ['已查，未取得值得展开的新增事实']})
    assert legacy['correlation_results'] == []


@pytest.mark.parametrize('facts', [{'items': []}, {'data': {'items': []}}, {'total': 0}])
def test_legacy_sanitized_empty_containers_do_not_prove_the_original_response_empty(facts):
    row = correlation_results(EVENT, [proof(facts)])[0]
    assert row['status'] in {'unknown', 'insufficient'}
    assert 'returned_count' not in row and 'total_count' not in row


def test_failed_same_version_query_remains_visible_but_does_not_generate_a_card():
    p = proof({'items': []}, success=False, error='authorization: PRIVATE_ERROR',
              query={'action': 'search', 'token': 'PRIVATE_QUERY'})
    story = build_story(EVENT, [p])
    row = story['correlation_results'][0]
    assert row['status'] == 'failed' and story['evidence_cards'] == []
    assert 'PRIVATE_' not in json.dumps(row, ensure_ascii=False)
    assert readable_story(story)['correlation_results'] == [row]


@pytest.mark.parametrize('updates', [{'event': 'incident-other'}, {'event_revision': 'old'},
                                   {'event_revision': None}])
def test_query_results_never_cross_incident_or_revision(updates):
    assert correlation_results(EVENT, [proof({'items': []}, **updates)]) == []
    changed = {**EVENT, 'endTime': EVENT['endTime'] + 1}
    assert correlation_results(changed, [proof({'items': []})], plan=[plan()]) == []
    assert correlation_results(changed, [proof({'items': []})], canonical=EVENT, plan=[plan()]) == []


def test_count_metadata_is_rechecked_against_partial_and_contradictory_saved_facts():
    empty = {'schema': 1, 'explicit_empty': True, 'partial': False, 'returned_count': 0, 'total_count': 0}
    for p in (proof({'items': []}, partial=True, result_meta=empty),
              proof({'records': [{'threat.id': 'tdp-a', 'net.http.url': '/actual'}]}, result_meta=empty),
              proof({'items': [], '_evidenceLimits': ['来源内容不完整']}, result_meta=empty)):
        assert correlation_results(EVENT, [p])[0]['status'] != 'no_match'
    assert correlation_results(EVENT, [proof({}, result_meta={'schema': '1', 'explicit_empty': True})])[0]['status'] == 'unknown'


@pytest.mark.parametrize('meta', [
    {'has_content': True, 'returned_count': 5, 'total_count': 5},
    {'returned_count': 0, 'total_count': 5}, {'returned_count': True}, {},
])
def test_inconsistent_or_unsubstantiated_persisted_empty_claims_cannot_establish_no_match(meta):
    row = correlation_results(EVENT, [proof({}, result_meta={
        'schema': 1, 'explicit_empty': True, 'partial': False, **meta})])[0]
    assert row['status'] != 'no_match'


@pytest.mark.parametrize('key', ['items', 'records', 'rows'])
@pytest.mark.parametrize('value', [None, {}, 'unreadable'])
def test_unreadable_known_list_is_partial_even_when_source_total_is_zero(key, value):
    raw = {key: value, 'total': 0}
    meta = response_metadata(raw, 'tdp')
    assert meta['partial'] and not meta['explicit_empty']
    row = correlation_results(EVENT, [proof(raw, result_meta=meta)])[0]
    assert row['partial'] and row['status'] != 'no_match'


@pytest.mark.parametrize('partial', [False, True])
def test_monitor_transport_partial_flags_are_metadata_instead_of_record_content(partial):
    raw = {'records': [], 'total': 0, '_monitor_partial': partial,
           'truncated': False, 'incomplete': False}
    meta = response_metadata(raw, 'tdp')
    assert meta['partial'] is partial and meta['explicit_empty'] is not partial
    row = correlation_results(EVENT, [proof(raw, result_meta=meta)])[0]
    assert (row['status'] == 'no_match') is not partial


def test_counts_are_measured_before_trimming_and_partial_records_stay_candidates():
    raw = {'records': [{'threat.id': f'tdp-{n}', 'net.http.url': f'/record-{n}'} for n in range(25)], 'total': 27}
    meta = response_metadata(raw, 'tdp')
    assert meta['returned_count'] == 25 and meta['total_count'] == 27 and meta['partial']
    row = correlation_results(EVENT, [proof(raw, result_meta=meta)])[0]
    assert row['status'] == 'candidate' and row['partial']
    assert row['returned_count'] == 25 and row['total_count'] == 27
    assert len(json.dumps(row['facts'], ensure_ascii=False).encode()) <= 8000


def test_sig_empty_assets_and_configuration_are_separate_from_security_alerts():
    empty = proof({'items': []}, product='sig', tool='onesig_strategy_api_query',
                  result_meta=response_metadata({'items': []}, 'sig'))
    row = correlation_results(EVENT, [empty])[0]
    assert row['status'] == 'no_match' and '资产查询' in row['summary']
    meta = response_metadata({'vendor_specific_subnets': [{'cidr': '192.0.2.0/24'}]}, 'sig')
    row = correlation_results(EVENT, [proof({}, product='sig', tool='onesig_strategy_api_query', result_meta=meta)])[0]
    assert row['status'] == 'insufficient' and 'returned_count' not in row


def test_plan_is_bound_to_original_version_and_suppressed_only_for_the_actual_device_and_tool():
    rows = correlation_results(EVENT, [proof({'items': []}, result_meta=response_metadata({'items': []}, 'tdp'))], plan=[plan(),
        plan(device='tdp-b', status='unavailable', reason='当前未配置'),
        plan(tool='another_tool', status='skipped', reason='用户未选择'),
        plan(device='other', event_revision='old'), plan(device='other', event_device='other')])
    assert len(rows) == 3 and [row['status'] for row in rows] == ['no_match', 'unavailable', 'skipped']
    assert all('evidence_id' not in row for row in rows[1:])
    virtual = correlation_results(EVENT, [], plan=[plan(device=None, product='unknown', tool=None, status='skipped')])
    assert len(virtual) == 1 and virtual[0]['status'] == 'skipped'
    story = build_story(EVENT, [], {'correlation_plan': [plan()]})
    assert story['correlation_results'][0]['status'] == 'not_queried'


def test_observed_tdp_http_shape_is_a_candidate_with_its_original_scope():
    # Shape retained in the deployed attachment: nested HTTP, no alert ID.
    raw = {'records': [{'type': 'http', 'src_ip': '192.0.2.10', 'src_port': 51210,
                       'http': {'method': 'OPTIONS', 'domain': 'example.invalid', 'url': '/example'}}]}
    row = correlation_results(EVENT, [proof(raw, result_meta=response_metadata(raw, 'tdp'))])[0]
    assert row['status'] == 'candidate' and row['returned_count'] == 1
    assert row['window'] == {'start': EVENT['startTime'], 'end': EVENT['endTime']}
    assert '同一资产' in row['boundary'] and row['facts']['records'][0]['http']['url'] == '/example'


def test_rows_envelope_retains_record_identity_packets_and_counts_together():
    raw = {'rows': [{'threat.id': 'tdp-a', 'threat.name': 'SQL injection',
                     'net.src_ip': EVENT['host'], 'net.http.url': '/actual',
                     'net.http.reqs_body': 'order=@@version'}], 'total': 1}
    story = build_story(EVENT, [proof(raw, result_meta=response_metadata(raw, 'tdp'))])
    row = story['correlation_results'][0]
    assert row['status'] == 'candidate' and row['returned_count'] == 1 and not row['partial']
    assert story['evidence_cards'][0]['alert_id'] == 'tdp-a'
    assert row['facts']['rows'][0]['net.http.reqs_body'] == 'order=@@version'


def test_query_and_plan_input_limits_are_disclosed_without_increasing_the_budget():
    rows = correlation_results(EVENT, [proof({}, id=f'evidence-{n}') for n in range(25)])
    assert len(rows) == 24 and rows[-1]['partial'] and '条数上限' in rows[-1]['boundary']
    plans = correlation_results(EVENT, [], plan=[plan(device=f'tdp-{n}') for n in range(25)])
    assert len(plans) == 24 and plans[-1]['partial'] and '条数上限' in plans[-1]['boundary']


def test_unrelated_xdr_proofs_do_not_become_correlation_rows_or_fill_missing_products():
    assert correlation_results(EVENT, [proof({'items': []}, product='xdr', kind='alerts',
                                           tool='sangfor_xdr_alerts', device=EVENT['device'])]) == []
