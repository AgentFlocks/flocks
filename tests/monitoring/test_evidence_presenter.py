"""Operator summaries stay tied to facts and leave source payloads expandable."""
import json

from flocks.monitoring.evidence_presenter import evidence_cards, MAX_CARDS
from flocks.monitoring.event_details import build_story, readable_story
from flocks.monitoring.investigation import fingerprint

EVENT = {'id': 'incident-one', 'device': 'xdr-a', 'key': 'xdr-a:incident:incident-one',
         'name': '主机存在异常通信', 'host': '10.0.0.1', 'endTime': 1791860390,
         'startTime': 1791860154, 'dealStatus': 0}


def proof(facts, **updates):
    return {'id': 'evidence-2', 'device': 'xdr-a', 'source_name': '股份XDR',
            'tool': 'sangfor_xdr_incidents', 'event': EVENT['id'], 'event_revision': fingerprint(EVENT),
            'kind': 'alerts', 'success': True, 'partial': False,
            'retrieved_at': '2026-10-13T16:00:00Z', 'facts': facts,
            'query': {'action': 'list', 'host': EVENT['host']}, **updates}


def test_dns_summary_explains_observation_without_inventing_c2_or_infection():
    facts = {'data': {'item': [{'srcIp': '10.0.0.1', 'dstIp': ['223.5.5.5'], 'dstPort': 53,
        'domain': [f'random-{n}.biz' for n in range(15)], 'name': '访问大量随机域名',
        'firstTime': 1791860154, 'lastTime': 1791860390, 'attackState': 3}]}}
    cards = evidence_cards(EVENT, [proof(facts)])
    assert len(cards) == 1
    card = cards[0]
    assert 'DNS 查询' in card['summary'] and '域名解析行为依据' in card['summary']
    assert '不能据此认定已连接恶意服务器或感染' in card['summary']
    assert 'random-14.biz' not in card['summary'] and 'random-14.biz' in json.dumps(card['fields'])
    assert card['time'] == 1791860390  # normalized alert time is source lastTime, not retrieval
    assert 'evidence-2' not in card['summary'] and 'incident-one' not in card['summary']


def test_cross_device_record_explains_real_intelligence_and_candidate_boundary():
    cards = evidence_cards(EVENT, [proof({'data': {'records': [{
        'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22', 'dstPort': 3333,
        'domain': 'pool.example', 'intelligenceTag': '挖矿矿池', 'time': 1791860154}]}},
        source_name='华东TDP', device='tdp-1', tool='tdp_query', kind='related')])
    assert '华东TDP' in cards[0]['summary'] and '挖矿矿池' in cards[0]['summary']
    assert '网络访问行为的依据' in cards[0]['summary']
    assert '候选关联' in cards[0]['boundary']
    assert cards[0]['time'] == 1791860154


def test_empty_failed_and_labels_only_queries_never_become_evidence_rows():
    proofs = [proof({'data': {'item': []}}), proof({'data': {'gptResult': 120, 'attackState': 3}}),
              proof({'data': {'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22'}}, success=False)]
    assert evidence_cards(EVENT, proofs) == []


def test_mismatched_incident_revision_device_or_actual_query_is_excluded():
    facts = {'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22'}
    for updates in ({'event': 'incident-other'}, {'event_revision': 'old'}, {'device': 'xdr-b'},
                    {'kind': 'dns', 'query': {'action': 'get_entities', 'uuid': 'incident-other'}}):
        assert evidence_cards(EVENT, [proof(facts, **updates)]) == []
    assert evidence_cards(EVENT, [proof(facts, event_revision=None)]) == []


def test_status_feedback_can_keep_same_activity_proofs_but_new_activity_cannot():
    evidence = [proof({'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22'})]
    assert evidence_cards({**EVENT, 'dealStatus': 40}, evidence, canonical=EVENT)
    assert not evidence_cards({**EVENT, 'endTime': 1791860999}, evidence, canonical=EVENT)


def test_source_times_and_packets_are_preserved_with_final_query_and_payload_redaction():
    evidence = [proof({'data': {'alertTimeLine': [{'name': 'SQL注入', 'alertId': 'alert-a',
        'firstTime': 1791860154, 'proof': {'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22',
        'requestHead': 'POST /search HTTP/1.1\nAuthorization: PRIVATE_AUTH',
        'requestBody': '{"password":"PRIVATE_BODY","order":"@@version"}',
        'responseHead': 'HTTP/1.1 200 OK', 'responseBody': '{"answer":"original-response"}'}}]}},
        query={'action': 'get_proof', 'uuid': 'alert-a', 'token': 'PRIVATE_QUERY'})]
    cards = evidence_cards(EVENT, evidence)
    assert len(cards) == 2 and cards[0]['time'] == 1791860154
    serialized = json.dumps(cards, ensure_ascii=False)
    assert 'PRIVATE_' not in serialized and '@@version' in serialized and 'original-response' in serialized
    assert len(cards[0]['packets']) == 4
    assert cards[0]['raw']['responseBody'] == '{"answer":"original-response"}'


def test_story_exposes_card_contract_and_separate_operator_copy():
    evidence = [proof({'data': {'item': [{'domain': 'example.invalid', 'threatLevel': 3}]}})]
    story = build_story(EVENT, evidence, {'operator_summary': '已确认异常访问。',
        'operator_explanation': ['主机持续访问风险目标。', '需要清理异常程序。']})
    assert story['evidence_cards'] and readable_story(story)['evidence_cards'] == story['evidence_cards']
    assert story['operator_summary'] == '已确认异常访问。'
    assert len(story['operator_explanation']) == 2
    assert story['key_evidence']  # old audit text retained separately


def test_card_budget_is_bounded_and_duplicate_activity_is_not_dumped_twice():
    facts = {'data': {'item': [{'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22', 'dstPort': 443}]*10}}
    assert len(evidence_cards(EVENT, [proof(facts)])) == 1
    proofs = [proof({'srcIp': '10.0.0.1', 'dstIp': f'192.0.2.{n}',
                    'requestBody': 'x'*30_000}, id=f'evidence-{n}') for n in range(24)]
    cards = evidence_cards(EVENT, proofs)
    assert len(cards) <= MAX_CARDS
    assert all(len(json.dumps(card['raw'], ensure_ascii=False).encode()) <= 24_000 for card in cards)
    assert all(card['partial'] for card in cards)


def test_file_process_and_packets_have_distinct_evidence_kinds_and_source_times():
    proofs = [proof({'data': {'item': [{'processName': 'miner.exe', 'commandLine': 'miner --pool pool.example',
        'filePath': '/tmp/miner.exe', 'virusName': 'CoinMiner', 'fileCreateTime': 1791800000}]}})]
    cards = evidence_cards(EVENT, proofs)
    assert {card['kind'] for card in cards} == {'file', 'process'}
    assert all(card['time'] is None for card in cards)  # creation != execution timestamp
    assert any(field['key'] == 'file_created_at' for field in cards[0]['fields'])
    assert '程序' not in next(card['summary'] for card in cards if card['kind'] == 'file')
