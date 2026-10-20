"""Operator summaries stay tied to facts and leave source payloads expandable."""
import json

from flocks.monitoring.evidence_presenter import evidence_cards, MAX_CARDS, MAX_RECORDS
from flocks.monitoring.event_details import build_story, readable_story, safe_facts
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
    assert 'DNS 查询' in card['summary']
    assert not any(word in card['summary'] for word in ('C2', '感染', '依据', '不能据此'))
    assert 'random-14.biz' not in card['summary'] and 'random-14.biz' in json.dumps(card['fields'])
    assert card['time'] == 1791860390  # normalized alert time is source lastTime, not retrieval
    assert 'evidence-2' not in card['summary'] and 'incident-one' not in card['summary']


def test_cross_device_record_explains_real_intelligence_and_candidate_boundary():
    cards = evidence_cards(EVENT, [proof({'data': {'records': [{
        'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22', 'dstPort': 3333,
        'domain': 'pool.example', 'intelligenceTag': '挖矿矿池', 'time': 1791860154}]}},
        source_name='华东TDP', device='tdp-1', tool='tdp_query', kind='related')])
    assert '华东TDP' in cards[0]['summary'] and '挖矿矿池' in cards[0]['summary']
    assert '访问 192.0.2.22' in cards[0]['summary']
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
    assert len(cards) == 1 and cards[0]['time'] == 1791860154
    assert cards[0]['alert_id'] == 'alert-a'
    serialized = json.dumps(cards, ensure_ascii=False)
    assert 'PRIVATE_' not in serialized and '@@version' in serialized and 'original-response' in serialized
    assert len(cards[0]['packets']) == 4
    assert cards[0]['raw']['proof']['responseBody'] == '{"answer":"original-response"}'
    assert cards[0]['records'][0]['raw'] == cards[0]['raw']


def test_story_exposes_card_contract_and_separate_operator_copy():
    evidence = [proof({'data': {'item': [{'domain': 'example.invalid', 'threatLevel': 3}]}})]
    story = build_story(EVENT, evidence, {'operator_summary': '已确认异常访问。',
        'operator_explanation': ['主机持续访问风险目标。', '需要清理异常程序。']})
    assert story['evidence_cards'] and readable_story(story)['evidence_cards'] == story['evidence_cards']
    assert story['operator_summary'] == '已确认异常访问。'
    assert len(story['operator_explanation']) == 2
    assert story['key_evidence']  # old audit text retained separately


def test_card_budget_is_bounded_and_duplicate_activity_is_not_dumped_twice():
    facts = {'data': {'item': [{'alertId': 'alert-a', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22', 'dstPort': 443}]*10}}
    assert len(evidence_cards(EVENT, [proof(facts)])) == 1
    proofs = [proof({'srcIp': '10.0.0.1', 'dstIp': f'192.0.2.{n}',
                    'requestBody': 'x'*30_000}, id=f'evidence-{n}') for n in range(24)]
    cards = evidence_cards(EVENT, proofs)
    assert len(cards) <= MAX_CARDS
    assert all(len(json.dumps(card['raw'], ensure_ascii=False).encode()) <= 24_000 for card in cards)
    assert all(card['partial'] for card in cards)


def test_file_and_process_in_one_record_are_one_card_without_invented_execution_time():
    proofs = [proof({'data': {'item': [{'processName': 'miner.exe', 'commandLine': 'miner --pool pool.example',
        'filePath': '/tmp/miner.exe', 'virusName': 'CoinMiner', 'fileCreateTime': 1791800000}]}})]
    cards = evidence_cards(EVENT, proofs)
    assert len(cards) == 1 and cards[0]['kind'] == 'process'
    assert all(card['time'] is None for card in cards)  # creation != execution timestamp
    assert any(field['key'] == 'file_created_at' for field in cards[0]['fields'])
    assert '/tmp/miner.exe' in cards[0]['summary'] and '进程或命令行' in cards[0]['summary']


def field_values(card):
    return {field['key']: field['value'] for field in card['fields']}


def test_one_alert_combines_parent_network_url_packets_file_and_process_facts():
    row = {'alertId': 'alert-a', 'name': 'SQL注入', 'firstTime': 1791860154,
           'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22',
           'proof': {'url': '/search', 'requestBody': 'order=@@version',
                     'responseBody': 'original response',
                     'file': {'uuid': 'file-entity', 'name': '文件实体', 'filePath': '/tmp/sample'},
                     'process': {'uuid': 'process-entity', 'processName': 'worker', 'commandLine': 'worker --read sample'}}}
    cards = evidence_cards(EVENT, [proof({'data': {'alertTimeLine': [row]}})])
    assert len(cards) == 1
    card = cards[0]
    assert card['alert_id'] == 'alert-a' and card['kind'] == 'request'
    fields = field_values(card)
    assert fields['alert_ids'] == ['alert-a'] and fields['name'] == 'SQL注入'
    assert fields['src_ip'] == '10.0.0.1' and fields['url'] == '/search'
    assert fields['file_path'] == '/tmp/sample' and fields['command_line'] == 'worker --read sample'
    assert 'event_id' not in fields
    assert len(card['packets']) == 2 and len(card['records']) == 1
    assert card['raw'] == row
    assert all(text in card['summary'] for text in ('10.0.0.1', '192.0.2.22', '请求及响应报文', '文件信息', '进程或命令行', 'SQL 注入可疑语句'))
    assert '不能' not in card['summary'] and '提供' not in card['summary']


def test_alert_list_and_alert_proof_queries_merge_by_device_and_alert_with_all_provenance():
    listed = proof({'data': {'records': [{'uuid': 'plain-alert-id', 'name': '可疑访问',
                    'lastTime': 1791860390, 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22'}]}},
                   id='evidence-list', query={'action': 'list'})
    details = proof({'data': {'proof': {'url': '/query', 'requestBody': 'x=1', 'responseBody': 'ok'}}},
                    id='evidence-proof', kind='alert_proof',
                    query={'action': 'get_proof', 'uuid': 'plain-alert-id'})
    cards = evidence_cards(EVENT, [listed, details])
    assert len(cards) == 1
    card = cards[0]
    assert card['alert_id'] == 'plain-alert-id' and card['kind'] == 'request'
    assert field_values(card)['name'] == '可疑访问' and field_values(card)['url'] == '/query'
    assert card['time'] == 1791860390
    assert [item['evidence_id'] for item in card['records']] == ['evidence-list', 'evidence-proof']
    assert card['records'][1]['query'] == details['query']
    assert card['records'][1]['raw']['responseBody'] == 'ok'
    assert card['raw'] == card['records'][0]['raw'] and card['query'] == listed['query']
    assert card['evidence_id'] == 'evidence-list'


def test_distinct_alert_ids_with_identical_behavior_time_and_summary_stay_separate():
    base = {'name': '相同检测', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22', 'time': 1791860154}
    cards = evidence_cards(EVENT, [proof({'records': [{**base, 'alertId': 'alert-a'},
                                                    {**base, 'alertId': 'alert-b'}]})])
    assert len(cards) == 2
    assert {card['alert_id'] for card in cards} == {'alert-a', 'alert-b'}
    assert cards[0]['summary'] == cards[1]['summary']
    assert all(len(card['records']) == 1 for card in cards)


def test_unnumbered_records_keep_parent_child_scope_but_do_not_guess_cross_record_identity():
    row = {'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22', 'proof': {'requestBody': 'x=1'}}
    cards = evidence_cards(EVENT, [proof({'records': [row, row]}),
                                   proof(row, id='evidence-other')])
    assert len(cards) == 3
    assert all('alert_id' not in card and len(card['packets']) == 1 for card in cards)
    assert all('独立记录' in card['boundary'] for card in cards)


def test_unnumbered_sibling_records_do_not_inherit_one_identity_from_a_named_wrapper():
    facts = {'name': '事件响应', 'records': [
        {'name': '告警1', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.1'},
        {'name': '告警2', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.2'},
    ]}
    cards = evidence_cards(EVENT, [proof(facts)])
    assert len(cards) == 2 and all('alert_id' not in card for card in cards)
    assert [field_values(card)['name'] for card in cards] == ['告警1', '告警2']
    assert [field_values(card)['dst_ip'] for card in cards] == ['192.0.2.1', '192.0.2.2']


def test_query_and_entity_uuids_are_not_alert_ids_without_an_alert_proof_contract():
    entity = {'uuid': 'alert-file-entity', 'fileName': 'sample', 'filePath': '/tmp/sample'}
    cards = evidence_cards(EVENT, [proof(entity, kind='file', query={'action': 'get_entities', 'uuid': EVENT['id']}),
                                   proof({'requestBody': 'x=1'}, kind='proof', id='event-proof',
                                         query={'action': 'get_proof', 'uuid': EVENT['id']})])
    assert len(cards) == 2 and all('alert_id' not in card for card in cards)
    assert all('alert_ids' not in field_values(card) for card in cards)
    assert all('独立记录' in card['boundary'] for card in cards)
    targeted = evidence_cards(EVENT, [proof(entity, kind='alert_proof',
                                            query={'action': 'get_proof', 'uuid': 'alert-a'})])
    assert len(targeted) == 1 and targeted[0]['alert_id'] == 'alert-a'
    assert field_values(targeted[0])['alert_ids'] == ['alert-a']
    assert 'event_id' not in field_values(targeted[0])


def test_multi_alert_records_are_not_duplicated_or_assigned_to_one_of_the_alerts():
    base = {'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22'}
    cards = evidence_cards(EVENT, [proof({'records': [
        {**base, 'alertIds': ['alert-a', 'alert-b']},
        {**base, 'alertIds': ['alert-a', 'alert-b']},
        {**base, 'alertId': 'alert-a'},
        {**base, 'alertIds': ['alert-a']},
    ]})])
    assert len(cards) == 3
    ambiguous = [card for card in cards if 'alert_id' not in card]
    assert len(ambiguous) == 2 and all('同时关联多条告警' in card['boundary'] for card in ambiguous)
    assert all(field_values(card)['alert_ids'] == ['alert-a', 'alert-b'] for card in ambiguous)
    single = next(card for card in cards if card.get('alert_id') == 'alert-a')
    assert len(single['records']) == 2


def test_conflicting_explicit_alert_aliases_keep_an_ambiguous_record_separate():
    cards = evidence_cards(EVENT, [proof({'records': [
        {'alertId': 'alert-a', 'alertIds': ['alert-a', 'alert-b'], 'requestBody': 'mixed facts'},
        {'alertId': 'alert-a', 'requestBody': 'only a'},
    ]})])
    assert len(cards) == 2
    ambiguous = next(card for card in cards if 'alert_id' not in card)
    assert field_values(ambiguous)['alert_ids'] == ['alert-a', 'alert-b']
    assert '同时关联多条告警' in ambiguous['boundary']
    assert len(next(card for card in cards if card.get('alert_id') == 'alert-a')['packets']) == 1


def test_a_different_child_alert_does_not_inherit_the_parent_alerts_behavior():
    facts = {'alertId': 'alert-a', 'name': '告警A', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.1',
             'filePath': '/tmp/a', 'alerts': [{'alertId': 'alert-b', 'name': '告警B', 'requestBody': 'only b packet'}]}
    cards = evidence_cards(EVENT, [proof(facts)])
    assert len(cards) == 2
    child = next(card for card in cards if card['alert_id'] == 'alert-b')
    fields = field_values(child)
    assert fields['name'] == '告警B'
    assert not {'src_ip', 'dst_ip', 'file_path'}.intersection(fields)
    assert '192.0.2.1' not in child['summary'] and '/tmp/a' not in child['summary']
    assert child['packets'][0]['text'] == 'only b packet'


def test_a_multi_alert_summary_is_split_only_when_children_identify_each_alert():
    facts = {'alertIds': ['alert-a', 'alert-b'], 'name': '两条告警汇总',
             'srcIp': '10.0.0.1', 'dstIp': ['192.0.2.1', '192.0.2.2'],
             'alerts': [{'alertId': 'alert-a', 'name': '告警A', 'requestBody': 'only a packet'},
                        {'alertId': 'alert-b', 'name': '告警B', 'requestBody': 'only b packet'}]}
    cards = evidence_cards(EVENT, [proof(facts)])
    assert len(cards) == 2 and {card['alert_id'] for card in cards} == {'alert-a', 'alert-b'}
    assert [field_values(card)['name'] for card in cards] == ['告警A', '告警B']
    assert all('dst_ip' not in field_values(card) and 'src_ip' not in field_values(card) for card in cards)
    assert [card['packets'][0]['text'] for card in cards] == ['only a packet', 'only b packet']
    assert all(card['raw'] == facts and '上层汇总' in card['boundary'] for card in cards)
    incomplete = {**facts, 'alerts': facts['alerts'][:1]}
    partial_cards = evidence_cards(EVENT, [proof(incomplete)])
    assert len(partial_cards) == 2
    assert any('alert_id' not in card and '同时关联多条告警' in card['boundary'] for card in partial_cards)


def test_an_entity_alert_name_does_not_display_its_generic_entity_name_as_an_alert():
    cards = evidence_cards(EVENT, [proof({'alertId': 'alert-a', 'alertName': '告警名',
        'file': {'name': 'file-entity-name', 'alertName': '真实告警名', 'filePath': '/tmp/file'}})])
    assert len(cards) == 1
    assert field_values(cards[0])['name'] == ['告警名', '真实告警名']
    assert 'file-entity-name' in json.dumps(cards[0]['raw'])


def test_truncated_query_provenance_marks_the_card_partial():
    cards = evidence_cards(EVENT, [proof({'alertId': 'alert-a', 'requestBody': 'x=1'},
        query={'action': 'list', 'host': 'x' * 10_000})])
    assert len(cards) == 1 and cards[0]['partial']
    assert cards[0]['query']['truncated'] and cards[0]['records'][0]['query']['truncated']


def test_same_device_sources_merge_without_repeating_packets_and_other_devices_stay_candidates():
    row = {'alertId': 'alert-a', 'name': '可疑访问', 'requestBody': 'x=1'}
    cards = evidence_cards(EVENT, [proof(row, id='evidence-one', source_name='XDR列表'),
                                   proof(row, id='evidence-two', source_name='XDR举证'),
                                   proof(row, id='evidence-tdp', device='tdp-1', tool='tdp_query',
                                         kind='related', source_name='华东TDP')])
    assert len(cards) == 2
    assert [item['source'] for item in cards[0]['records']] == ['XDR列表', 'XDR举证']
    assert len(cards[0]['packets']) == 1 and not cards[0]['boundary']
    assert cards[1]['alert_id'] == 'alert-a' and '候选关联' in cards[1]['boundary']


def test_filling_the_card_budget_still_merges_later_proof_into_an_existing_alert():
    listed = [proof({'records': [{'alertId': f'alert-{n}', 'name': f'检测 {n}',
                                 'srcIp': '10.0.0.1', 'dstIp': f'192.0.2.{n}'}
                                for n in range(start, min(start + 18, MAX_CARDS))]}, id=f'evidence-list-{start}')
              for start in range(0, MAX_CARDS, 18)]
    later = proof({'requestBody': 'late request', 'responseBody': 'late response'},
                  id='evidence-later', kind='alert_proof', query={'action': 'get_proof', 'uuid': 'alert-0'})
    omitted = proof({'alertId': 'alert-omitted', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.200'}, id='evidence-omitted')
    cards = evidence_cards(EVENT, [*listed, omitted, later])
    assert len(cards) == MAX_CARDS and all(card['partial'] for card in cards)
    retained = next(card for card in cards if card.get('alert_id') == 'alert-0')
    assert retained['kind'] == 'request' and len(retained['packets']) == 2
    assert retained['records'][-1]['evidence_id'] == 'evidence-later'
    assert retained['records'][-1]['raw']['responseBody'] == 'late response'


def test_incomplete_source_rows_do_not_consume_the_budget_before_later_usable_proof():
    placeholders = [proof({'records': [{'alertId': f'alert-{n}', 'srcIp': '10.0.0.1'}
                                      for n in range(start, min(start + 20, MAX_CARDS))]}, id=f'evidence-{start}')
                    for start in range(0, MAX_CARDS, 20)]
    usable = proof({'alertId': 'useful-alert', 'name': 'SQL注入', 'requestBody': 'order=@@version'}, id='evidence-useful')
    cards = evidence_cards(EVENT, [*placeholders, usable])
    assert len(cards) == 1 and cards[0]['alert_id'] == 'useful-alert'
    assert cards[0]['packets'][0]['text'] == 'order=@@version' and not cards[0]['partial']


def test_source_records_remain_bounded_and_partial_while_distinct_fields_are_retained():
    proofs = [proof({'records': [
        {'alertId': 'alert-a', 'srcIp': '10.0.0.1', 'dstIp': f'192.0.2.{n}'}
        for n in range(start, min(start + 20, MAX_RECORDS + 3))]}, id=f'evidence-{start}')
        for start in range(0, MAX_RECORDS + 3, 20)]
    cards = evidence_cards(EVENT, proofs)
    assert len(cards) == 1 and len(cards[0]['records']) == MAX_RECORDS
    assert cards[0]['partial'] and '展示上限' in cards[0]['boundary']
    assert len(field_values(cards[0])['dst_ip']) == MAX_RECORDS + 3


def test_merged_query_and_raw_records_are_redacted_in_all_compatibility_fields():
    listed = proof({'alertId': 'alert-a', 'srcIp': '10.0.0.1', 'dstIp': '192.0.2.22'}, id='evidence-list')
    later = proof({'proof': {'requestHead': 'Authorization: PRIVATE_HEAD',
                            'requestBody': '{"password":"PRIVATE_BODY","value":"kept"}',
                            'responseBody': 'visible response'}},
                  kind='alert_proof', id='evidence-later',
                  query={'action': 'get_proof', 'uuid': 'alert-a', 'token': 'PRIVATE_QUERY'})
    cards = evidence_cards(EVENT, [listed, later])
    assert len(cards) == 1 and len(cards[0]['records']) == 2
    serialized = json.dumps(cards, ensure_ascii=False)
    assert 'PRIVATE_' not in serialized and 'visible response' in serialized and 'kept' in serialized


def test_documented_alert_list_ip_info_and_xff_fields_merge_with_incident_timeline_proof():
    listed = proof({'data': {'item': [{'uuId': 'alert-network', 'name': '可疑HTTP访问',
        'srcIp': [], 'srcIpInfos': [{'ip': '192.0.2.10'}],
        'dstIp': ['198.51.100.20'], 'dstIpInfos': [{'ip': '198.51.100.21'}],
        'srcPort': [33001, 33002], 'dstPort': [443], 'xForwardedFor': ['203.0.113.1', '10.0.0.1']}]}})
    timeline = proof({'data': [{'uuId': EVENT['id'], 'alertTimeLine': [
        {'alertId': 'alert-network', 'name': '可疑HTTP访问', 'lastTime': 1791860390,
         'proof': {'dataType': 'NDR', 'srcIps': ['192.0.2.11'], 'dstIps': ['198.51.100.22'],
                   'xForwardedFor': ['203.0.113.2, 10.0.0.2'], 'requestBody': 'sample request'}}]}]},
        id='evidence-timeline', kind='proof', query={'action': 'get_proof', 'uuid': EVENT['id']})
    cards = evidence_cards(EVENT, [listed, timeline])
    assert len(cards) == 1 and cards[0]['alert_id'] == 'alert-network'
    fields = field_values(cards[0])
    assert fields['src_ip'] == ['192.0.2.10', '192.0.2.11']
    assert fields['dst_ip'] == ['198.51.100.20', '198.51.100.21', '198.51.100.22']
    assert fields['src_port'] == [33001, 33002] and fields['dst_port'] == [443]
    assert fields['x_forwarded_for'] == ['203.0.113.1', '10.0.0.1', '203.0.113.2, 10.0.0.2']
    assert next(field['label'] for field in cards[0]['fields'] if field['key'] == 'x_forwarded_for') == 'X-Forwarded-For（请求头原值）'
    assert cards[0]['records'][0]['raw']['srcIpInfos'] == [{'ip': '192.0.2.10'}]
    assert len(cards[0]['records']) == 2


def test_alert_proof_original_alert_ips_survive_and_header_recovers_only_explicit_xff():
    cards = evidence_cards(EVENT, [proof({'data': {
        'srcIp': ['192.0.2.10'], 'srcPort': [33001], 'dstIp': ['198.51.100.20'], 'dstPort': [443],
        'proof': {'requestHead': 'GET / HTTP/1.1\nX-Forwarded-For: 203.0.113.2, 10.0.0.2\nAuthorization: PRIVATE',
                  'responseHead': 'X-Forwarded-For: 203.0.113.99'},
        'originalAlert': {'srcIp': '192.0.2.11', 'dstIp': '198.51.100.21', 'name': 'process entity'}}},
        kind='alert_proof', query={'action': 'get_proof', 'uuid': 'alert-network'})])
    assert len(cards) == 1 and cards[0]['alert_id'] == 'alert-network'
    fields = field_values(cards[0])
    assert fields['src_ip'] == ['192.0.2.10', '192.0.2.11']
    assert fields['dst_ip'] == ['198.51.100.20', '198.51.100.21']
    assert fields['x_forwarded_for'] == ['203.0.113.2, 10.0.0.2']
    assert fields['src_port'] == [33001] and fields['dst_port'] == [443]
    assert 'process entity' not in str(fields.get('name'))
    assert 'PRIVATE' not in json.dumps(cards)


def test_xff_only_facts_are_shown_without_becoming_observed_source_or_destination_ips():
    cards = evidence_cards(EVENT, [proof({'data': {'item': [
        {'uuId': 'alert-xff', 'xForwardedFor': ['203.0.113.1, 10.0.0.1']}]}})])
    assert len(cards) == 1 and cards[0]['alert_id'] == 'alert-xff'
    assert field_values(cards[0])['x_forwarded_for'] == ['203.0.113.1, 10.0.0.1']
    assert not {'src_ip', 'dst_ip', 'host'}.intersection(field_values(cards[0]))
    assert '请求头原值' in cards[0]['summary']
    assert all(word not in cards[0]['summary'] for word in ('攻击源', '访问 ', '代理服务器'))


def test_xff_fields_still_require_the_current_successful_bound_event_version():
    facts = {'alertId': 'alert-xff', 'xForwardedFor': ['203.0.113.1']}
    for changes in ({'success': False}, {'event': 'other-event'}, {'event_revision': 'old'}, {'device': 'xdr-other'}):
        assert evidence_cards(EVENT, [proof(facts, **changes)]) == []


def test_a_long_header_xff_value_keeps_its_original_raw_header_and_marks_display_limits():
    header = 'X-Forwarded-For: ' + ', '.join(f'192.0.2.{n}' for n in range(100))
    cards = evidence_cards(EVENT, [proof({'alertId': 'alert-xff', 'requestHead': header})])
    assert len(cards) == 1 and cards[0]['partial']
    assert cards[0]['raw']['requestHead'] == header
    assert '已截断' in field_values(cards[0])['x_forwarded_for'][0]


def test_all_bounded_explicit_xff_alias_values_survive_combining_more_than_fifty_addresses():
    aliases = ('x_forwarded_for', 'xForwardedFor', 'X-Forwarded-For', 'x-forwarded-for')
    raw = {'alertId': 'alert-xff', **{
        alias: [f'192.0.{group}.{n}' for n in range(20)] for group, alias in enumerate(aliases)}}
    facts, partial = safe_facts(raw)
    assert not partial and all(len(facts[alias]) == 20 for alias in aliases)
    cards = evidence_cards(EVENT, [proof(facts)])
    expected = [address for alias in aliases for address in facts[alias]]
    assert len(cards) == 1 and not cards[0]['partial']
    assert field_values(cards[0])['x_forwarded_for'] == expected
    assert len(expected) == 80
    assert all(cards[0]['raw'][alias] == facts[alias] for alias in aliases)
