"""The documented TDP log fields retain their own identity and semantics."""
import json

import pytest

from flocks.monitoring.correlation import response_metadata
from flocks.monitoring.event_details import build_story, normalize_basics, safe_facts
from flocks.monitoring.evidence_presenter import evidence_cards
from flocks.monitoring.investigation import fingerprint

EVENT = {'id': 'incident-one', 'device': 'xdr-a', 'host': '192.0.2.10',
         'key': 'xdr-a:incident:incident-one', 'startTime': 1791860154, 'endTime': 1791860390}


def proof(facts, **updates):
    return {'id': 'evidence-1', 'event': EVENT['id'], 'event_revision': fingerprint(EVENT),
            'device': 'tdp-a', 'source_name': 'Fixture TDP', 'product': 'tdp',
            'tool': 'tdp_log_search', 'kind': 'related', 'success': True, 'partial': False,
            'query': {'action': 'search', 'time_from': 1791860154, 'time_to': 1791860390},
            'facts': facts, **updates}


def fields(card):
    return {field['key']: field['value'] for field in card['fields']}


@pytest.mark.parametrize('nested', [False, True])
def test_official_tdp_http_fields_preserve_alert_packets_and_separate_result_enum(nested):
    threat = {'id': 'tdp-alert-a', 'name': 'SQL 注入尝试', 'msg': '来源记录 password=PRIVATE_MSG',
              'level': 'attack', 'result': 'success'}
    http = {'method': 'POST', 'status': '200', 'reqs_host': 'example.invalid', 'url': '/search',
            'reqs_line': 'POST /search HTTP/1.1', 'resp_line': 'HTTP/1.1 200 OK',
            'reqs_header': 'Authorization: PRIVATE_AUTH\nX-Forwarded-For: 203.0.113.3',
            'reqs_body': '{"password":"PRIVATE_BODY","order":"@@version"}',
            'resp_header': 'Set-Cookie: PRIVATE_COOKIE', 'resp_body': 'original response',
            'reqs_cookie': 'PRIVATE_COOKIE_FIELD'}
    net = {'src_ip': '192.0.2.10', 'dest_ip': '198.51.100.20', 'src_port': 30001, 'dest_port': 443,
           'http_xff': '203.0.113.4, 10.0.0.8'}
    row = {'id': 'log-document-id', 'time': 1791860154}
    if nested:
        row.update(threat=threat, net={**net, 'http': http})
    else:
        row.update({f'threat.{key}': value for key, value in threat.items()})
        row.update({f'net.{key}': value for key, value in net.items()})
        row.update({f'net.http.{key}': value for key, value in http.items()})
    safe, partial = safe_facts({'records': [row]})
    assert not partial
    cards = evidence_cards(EVENT, [proof(safe)])
    assert len(cards) == 1 and cards[0]['alert_id'] == 'tdp-alert-a'
    assert cards[0]['kind'] == 'request' and len(cards[0]['packets']) == 4
    values = fields(cards[0])
    assert values['name'] == 'SQL 注入尝试'
    assert '来源情报标记' not in cards[0]['summary']
    assert values['src_ip'] == '192.0.2.10' and values['dst_ip'] == '198.51.100.20'
    assert values['src_port'] == 30001 and values['dst_port'] == 443
    assert values['tdp_result'] == 'success' and values['tdp_level'] == 'attack'
    assert values['http_status'] == '200' and values['request_line'] == http['reqs_line']
    assert values['response_line'] == http['resp_line']
    assert set(values['x_forwarded_for']) == {'203.0.113.4, 10.0.0.8', '203.0.113.3'}
    assert not {'event_id', 'attack_result', 'attack_state', 'severity'}.intersection(values)
    serialized = json.dumps(cards, ensure_ascii=False)
    assert 'PRIVATE_' not in serialized and '@@version' in serialized and 'original response' in serialized
    story = build_story(EVENT, [proof(safe)])
    assert story['evidence_cards'] == cards
    assert story['correlation_results'][0]['status'] == 'candidate'
    assert {packet['field'] for packet in story['packets']} == {
        'requestHead', 'requestBody', 'responseHead', 'responseBody'}


@pytest.mark.parametrize('nested', [False, True])
def test_dns_answer_is_retained_without_inventing_observed_destination(nested):
    dns = {'type': 'answer', 'rrname': 'example.invalid', 'rrtype': 'A', 'rdata': ['198.51.100.5']}
    row = {'threat.id': 'tdp-dns', 'threat.name': 'DNS 命中', 'net.src_ip': '192.0.2.10'}
    row.update({'net': {'dns': dns}} if nested else {f'net.dns.{k}': v for k, v in dns.items()})
    safe, _ = safe_facts(row)
    card = evidence_cards(EVENT, [proof(safe)])[0]
    values = fields(card)
    assert values['domain'] == 'example.invalid' and values['dns_answers'] == ['198.51.100.5']
    assert values['dns_type'] == 'answer' and values['dns_query_type'] == 'A'
    assert 'dst_ip' not in values and '198.51.100.5' not in card['summary']
    assert normalize_basics(normalize_basics(safe)) == normalize_basics(safe)


def test_tdp_alert_identity_merges_queries_but_log_document_ids_never_identify_alerts():
    first = {'id': 'document-one', 'threat.id': 'tdp-a', 'threat.name': '相同名称',
             'time': 1791860154, 'net.http.url': '/same'}
    second = {**first, 'id': 'document-two', 'threat.id': 'tdp-b'}
    more = {'threat': {'id': 'tdp-a'}, 'net': {'http': {'resp_body': 'new response'}}}
    cards = evidence_cards(EVENT, [proof({'records': [first, second]}), proof(more, id='evidence-2')])
    assert len(cards) == 2 and {card['alert_id'] for card in cards} == {'tdp-a', 'tdp-b'}
    assert len(next(card for card in cards if card['alert_id'] == 'tdp-a')['records']) == 2
    missing = evidence_cards(EVENT, [proof({'records': [{k: v for k, v in first.items() if k != 'threat.id'},
                                                    {k: v for k, v in second.items() if k != 'threat.id'}]})])
    assert len(missing) == 2 and all('alert_id' not in card for card in missing)


@pytest.mark.parametrize('wrapper', ['alerts', 'alert', 'records'])
def test_tdp_wrappers_never_promote_generic_document_or_entity_ids_to_alert_ids(wrapper):
    row = {'id': 'document-one', 'uuid': 'alert-entity-uuid', 'threat.name': 'SQL injection',
           'net.http.url': '/actual'}
    p = proof({wrapper: [row]})
    story = build_story(EVENT, [p])
    assert len(story['evidence_cards']) == 1 and 'alert_id' not in story['evidence_cards'][0]
    assert 'alert_ids' not in fields(story['evidence_cards'][0])
    assert all(not record.get('alert_ids') for record in story['alert_details'])


def test_sig_asset_configuration_is_not_rendered_as_a_malicious_alert():
    facts = {'items': [{'name': '数据中心网段', 'ip': '192.0.2.0/24', 'domain': 'example.invalid'}]}
    p = proof(facts, device='sig-a', product='sig', tool='onesig_strategy_api_query',
              query={'action': 'asset_list', 'body': {'search': EVENT['host']}},
              result_meta=response_metadata(facts, 'sig'))
    story = build_story(EVENT, [p])
    assert story['evidence_cards'] == []
    result = story['correlation_results'][0]
    assert result['status'] == 'insufficient' and '不提供威胁日志' in result['summary']
    assert result['returned_count'] == 1 and result['facts']['items'][0]['name'] == '数据中心网段'


def test_tdp_packets_and_facts_keep_existing_redaction_and_size_limits():
    safe, partial = safe_facts({'threat.id': 'tdp-a', 'net.http.reqs_body': 'token=PRIVATE_TOKEN ' + 'x'*30_000,
                                'net.http.resp_body': 'y'*30_000, 'threat.msg': 'z'*30_000})
    assert partial and 'PRIVATE_' not in json.dumps(safe)
    card = evidence_cards(EVENT, [proof(safe)])[0]
    assert card['partial'] and all(len(packet['text'].encode()) <= 8000 for packet in card['packets'])
    assert len(json.dumps(card['raw'], ensure_ascii=False).encode()) <= 24_000
