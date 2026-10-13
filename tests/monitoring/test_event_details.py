"""Realistic host/Web examples for evidence-first incident narratives."""
import json

import pytest

from flocks.monitoring.event_details import (
    MAX_FACT_BYTES, MAX_PACKET_BODY, build_story, normalize_basics, redact_text,
    safe_facts, story_sections, story_text,
)


def proof(facts, **fields):
    return {'id': 'evidence-1', 'device': 'xdr-a', 'tool': 'sangfor_xdr_incidents',
            'kind': 'proof', 'success': True, 'partial': False,
            'retrieved_at': '2026-10-12T12:00:00Z', 'facts': facts, **fields}


def web_facts():
    return {'data': {'alertTimeLine': [
        {'alertId': 'alert-1', 'name': 'Version全局变量SQL注入攻击', 'alertTime': '2026-09-01 18:24:22',
         'srcIp': '192.0.2.8', 'dstIp': '10.22.10.9', 'domain': 'example.invalid',
         'requestHead': 'POST /prod-api/paper/search HTTP/1.1\nHost: example.invalid\nAuthorization: Bearer PRIVATE_AUTH\nCookie: PRIVATE_COOKIE',
         'requestBody': '{"password":"PRIVATE_PASSWORD","orderBy":"publish_date desc, case when @@version like 0x382e342e376925 then publish_date else main_title end"}',
         'responseHead': 'HTTP/1.1 200 OK\nContent-Type: application/json\nSet-Cookie: PRIVATE_SET_COOKIE',
         'responseBody': '{"code":200,"msg":"查询成功","token":"PRIVATE_TOKEN"}'}]}}


def test_host_basics_keep_required_fields_without_guessing():
    value = normalize_basics({
        'uuId': 'incident-1', 'alertIds': ['alert-1'], 'name': 'Hacktool病毒',
        'alertTime': '2026-10-10 01:15:41', 'incidentSeverity': 3, 'riskLevel': 0,
        'hostIp': '10.0.0.29', 'dataSource': 'EDR', 'deviceName': 'EDR-21627655573',
        'virusName': 'Hacktool.Win64.Donut.Vofi', 'fileId': 'file-1', 'fileMd5': 'abc123',
        'filePath': r'd:\tools\av_evasion_tool\bin\donut.dll', 'fileSizeKB': 195,
        'fileCreateTime': '2025-03-03 14:24:34', 'token': 'PRIVATE_TOKEN'})
    assert value['event_id'] == 'incident-1' and value['alert_ids'] == ['alert-1']
    assert value['file_md5'] == 'abc123' and value['file_size'] == 195
    assert value['file_created_at'] == '2025-03-03 14:24:34'
    assert value['severity_field'] == 'incidentSeverity' and value['severity'] == 3
    assert 'PRIVATE_TOKEN' not in json.dumps(value)
    assert 'domain' not in value


def test_basics_are_idempotent_and_keep_enum_names_separate():
    value = normalize_basics({'uuId': 'incident-1', 'incidentSeverity': 3, 'attackState': 2, 'attackResult': 0})
    assert normalize_basics(value) == value
    story = build_story({'id': 'incident-1', 'basics': value}, [])
    text = story_text(story)
    assert 'attackState：成功（来源原值 2' in text and 'attackResult：尝试（来源原值 0' in text
    assert '等级字段：incidentSeverity' in text
    assert story['verdict'] == 'unknown'


def test_unknown_metadata_does_not_mean_evidence_is_partial():
    value, partial = safe_facts({'data': {'item': [{'name': 'file', 'hostIp': '10.0.0.1',
                                                'debug': 'not-important', 'Authorization': 'PRIVATE'}]},
                               'traceId': 'not-important', 'duration': 3})
    assert partial is False
    assert value['data']['item'][0] == {'name': 'file', 'hostIp': '10.0.0.1'}
    assert 'PRIVATE' not in json.dumps(value)


@pytest.mark.parametrize('text', [
    'Authorization: Bearer PRIVATE\nHost: example.invalid',
    'Cookie: session=PRIVATE\nAccept: */*',
    'Set-Cookie: PRIVATE\nContent-Type: text/plain',
    'X-Api-Key: PRIVATE\nHost: example.invalid',
    'X-Auth-Token: PRIVATE\nHost: example.invalid',
    '{"password":"PRIVATE","query":"@@version"}',
    "{'token':'PRIVATE','query':'@@version'}",
    'https://user:PRIVATE@example.invalid/?query=@@version',
    'GET /?password=PRIVATE&query=@@version HTTP/1.1',
    'GET /?access_token=PRIVATE&query=@@version HTTP/1.1',
    'program --password PRIVATE --query @@version',
    'program --token="PRIVATE" --query @@version',
])
def test_secret_redaction_from_packets_urls_and_commands(text):
    redacted = redact_text(text)
    assert 'PRIVATE' not in redacted
    if '@@version' in text:
        assert '@@version' in redacted
    assert '[已脱敏]' in redacted


def test_web_proof_keeps_payload_context_but_not_authentication():
    facts, partial = safe_facts(web_facts())
    assert not partial
    serial = json.dumps(facts, ensure_ascii=False)
    assert 'PRIVATE_' not in serial and '@@version like 0x382e342e376925' in serial
    row = facts['data']['alertTimeLine'][0]
    assert 'requestHead' in row and 'responseBody' in row
    assert 'HTTP/1.1 200' in row['responseHead']


def test_json_packet_objects_keep_application_keys_and_mask_nested_secrets():
    facts, partial = safe_facts({'data': {'requestBody': {'orderBy': '@@version',
        'nested': {'password': 'PRIVATE', 'X-Api-Key': 'PRIVATE'}, 'other': ['value']}}})
    assert not partial
    text = facts['data']['requestBody']
    assert '@@version' in text and 'nested' in text and 'PRIVATE' not in text


@pytest.mark.parametrize('field,value', [
    ('requestBody', None), ('responseHead', 100), ('responseBody', 'x' * 9000),
])
def test_missing_unreadable_and_truncated_packet_are_explicit(field, value):
    facts, partial = safe_facts({'data': {field: value}})
    assert partial
    assert facts['_evidenceLimits']
    if isinstance(value, str):
        text = facts['data'][field]
        assert len(text.encode()) <= MAX_PACKET_BODY and '已截断' in text


def test_huge_unicode_packets_stay_under_combined_budget():
    facts, partial = safe_facts({'data': {'item': [
        {'name': f'alert-{i}', 'requestBody': '中' * 9000, 'responseBody': '文' * 9000}
        for i in range(20)]}})
    assert partial
    assert len(json.dumps(facts, ensure_ascii=False).encode()) <= MAX_FACT_BYTES
    assert '内容不完整' in json.dumps(facts, ensure_ascii=False)


def test_explicit_partial_and_pagination_shortfall_stay_visible():
    facts, partial = safe_facts({'data': {'partial': True, 'total': 10, 'item': [{'name': 'one'}]}})
    assert partial and len(facts['_evidenceLimits']) == 2


def test_web_story_uses_original_event_times_not_retrieval_time():
    facts, partial = safe_facts(web_facts())
    story = build_story({'id': 'incident-1', 'name': 'SQL注入', 'device_name': '股份 XDR',
                         'basics': normalize_basics({'uuId': 'incident-1', 'hostIp': '10.22.10.9'})},
                        [proof(facts, partial=partial)],
                        {'verdict': 'risk', 'reason': '请求有SQL可疑语句；利用效果待核对', 'evidence_ids': ['evidence-1']})
    text = story_text(story)
    assert '2026-09-01 18:24:22' in text and '2026-10-12T12:00:00Z' not in text
    assert 'SQL 可疑片段' in text and '@@version' in text
    assert '状态码本身不证明攻击成功或安全' in text
    assert '不能单独证明利用成功或误报' in text
    assert 'PRIVATE_' not in text
    assert story['evidence_ids'] == ['evidence-1']
    assert len(story['packets']) == 4
    assert story['verdict'] == 'risk'


def test_no_pattern_is_not_a_benign_verdict_and_empty_result_is_not_safe():
    story = build_story({'id': 'incident-1', 'name': 'SQL注入'}, [proof({'data': {'item': []}})])
    assert story['verdict'] == 'unknown'
    assert any('未取得请求包' in gap for gap in story['gaps'])
    assert any('未取得响应包' in gap for gap in story['gaps'])
    assert '没有发现匹配片段不等于误报' in story_text(story)
    assert '已查' in story['other_queries'][0]


def test_timeline_sorts_actual_times_and_distinguishes_file_creation_from_alert():
    facts = {'data': {'alertTimeLine': [
        {'name': '第二次', 'alertTime': '2026-10-10 02:00:00'},
        {'name': '第一次', 'alertTime': '2026-10-10 01:00:00', 'fileCreateTime': '2025-01-01 00:00:00'},
        {'name': '没有时间的记录', 'requestBody': '@@version'}]}}
    story = build_story({'id': 'incident-1'}, [proof(facts)])
    assert '文件创建' in story['timeline'][0]['text'] and '不等于告警发生时间' in story['timeline'][0]['text']
    assert '第一次' in story['timeline'][1]['text'] and '第二次' in story['timeline'][2]['text']
    assert len(story['timeline']) == 3
    assert '2025-01-01' in story_text(story)


def test_http_200_never_changes_unknown_to_success_or_benign():
    facts = {'data': {'requestBody': 'normal input', 'responseHead': 'HTTP/1.1 200 OK'}}
    story = build_story({'id': 'incident-1', 'name': 'Web攻击'}, [proof(facts)])
    assert story['verdict'] == 'unknown'
    assert '状态码本身不证明攻击成功或安全' in story_text(story)


def test_failed_queries_and_bad_references_are_not_valid_evidence():
    story = build_story({'id': 'incident-1'}, [proof({}, success=False, error='未连接')],
                        {'reason': '未完成', 'evidence_ids': ['evidence-1'], 'gaps': ['需要日志']})
    assert story['evidence_ids'] == []
    assert any('查询未完成' in gap for gap in story['gaps'])
    assert any('引用了未取得成功结果' in gap for gap in story['gaps'])
    assert '需要日志' in story['gaps']


def test_mail_is_concise_but_keeps_key_evidence_and_material_gaps():
    facts, partial = safe_facts(web_facts())
    story = build_story({'id': 'incident-1', 'name': 'SQL注入', 'device_name': '同方 XDR'},
                        [proof(facts, partial=partial), proof({'data': {'item': []}}, id='evidence-2', device='tdp-a', kind='related')],
                        {'verdict': 'risk', 'reason': 'SQL注入尝试，成功与否待核对', 'evidence_ids': ['evidence-1']})
    mail = story_text(story, mail=True)
    assert '同方 XDR' in mail and 'incident-1' in mail and 'SQL注入尝试' in mail and '@@version' in mail
    assert '其他联动查询' not in mail and '请求与响应（' not in mail
    assert '状态码本身不证明攻击成功或安全' in mail
    assert len(mail.encode()) <= 12_000


def test_packet_markup_stays_plain_text_without_generating_html_or_markdown():
    story = build_story({'id': 'incident-1', 'name': 'Web攻击'}, [proof({'data': {
        'requestBody': '<img src=x onerror=alert(1)>', 'responseBody': '[link](https://invalid.example)'}})])
    text = story_text(story)
    assert '<img src=x onerror=alert(1)>' in text
    assert '[link](https://invalid.example)' in text
    sections = story_sections(story)
    assert all(set(section) == {'label', 'text'} for section in sections)


def test_legacy_events_have_a_readable_fallback():
    story = build_story({'id': 'incident-old', 'name': '历史事件', 'device': 'xdr-old', 'host': '10.0.0.1'}, [])
    text = story_text(story)
    assert 'incident-old' in text and '10.0.0.1' in text and '历史事件' in text
    assert '未用查询时间替代' in text and '不能据此判为误报' in text


def test_invalid_time_and_nonfinite_numbers_do_not_crash_facts():
    facts, partial = safe_facts({'data': {'time': float('nan'), 'name': 'alert'}})
    assert partial and facts['data']['time'] is None
    story = build_story({'id': 'incident-1', 'endTime': 10**100}, [proof(facts)])
    assert not story['timeline']


def test_real_incident_timeline_preserves_last_time_source_and_stage():
    raw = {'data': [{'uuId': 'incident-1', 'name': 'Web事件', 'endTime': 1791690000,
        'devSourceName': '事件来源', 'alertTimeLine': [{'alertId': 'alert-1', 'name': 'SQL注入',
            'lastTime': 1791689900, 'severity': 80, 'threatSubTypeDesc': 'SQL注入', 'stage': 2,
            'devSourceNames': ['STA-A'], 'proof': {'dataType': 'NDR', 'srcIps': ['192.0.2.1'],
                'dstIps': ['10.0.0.1'], 'attackResult': 1,
                'requestBody': '@@version', 'responseHead': 'HTTP/1.1 200 OK'}}]}]}
    facts, partial = safe_facts(raw)
    assert not partial
    row = facts['data'][0]['alertTimeLine'][0]
    assert row['lastTime'] == 1791689900 and row['devSourceNames'] == ['STA-A']
    story = build_story({'id': 'incident-1', 'name': 'Web事件'}, [proof(facts)])
    assert story['timeline'][0]['time'] == 1791689900
    text = story_text(story)
    assert 'STA-A' in text and '威胁子类型：SQL注入' in text and '来源阶段值：2' in text
    assert 'attackResult：成功（来源原值 1' in text
    assert story['packets'][0]['alert_id'] == 'alert-1'


def test_alert_list_uuids_are_alert_ids_in_context_not_incident_ids():
    facts, _ = safe_facts({'data': {'item': [{'uuId': '12345', 'name': 'SQL注入',
        'firstTime': 1791689800, 'lastTime': 1791689900, 'severity': 80, 'devSourceName': 'STA-A',
        'attackState': 1, 'srcIp': ['192.0.2.1'], 'dstIp': ['10.0.0.1']}], 'total': 1}})
    story = build_story({'id': 'incident-1', 'name': 'SQL注入'}, [proof(facts, kind='alerts')])
    alert = story['alert_details'][0]
    assert alert['alert_ids'] == ['12345'] and 'event_id' not in alert
    assert story['basics']['event_id'] == 'incident-1'
    assert 'attackState：失败（来源原值 1' in story_text(story)
    assert normalize_basics({'uuId': 'alert-123'})['alert_ids'] == ['alert-123']


def test_null_entity_list_is_partial_and_existing_limits_survive_second_pass():
    facts, partial = safe_facts({'data': {'item': None}})
    assert partial and '为空值' in facts['_evidenceLimits'][0]
    again, partial_again = safe_facts(facts)
    assert partial_again and again['_evidenceLimits'] == facts['_evidenceLimits']


def test_each_web_alert_tracks_packet_missing_independently():
    raw = {'data': {'alertTimeLine': [
        {'alertId': 'alert-with-packets', 'name': 'SQL注入', 'lastTime': 1791689900,
         'proof': {'requestBody': '@@version', 'responseHead': 'HTTP/1.1 200 OK'}},
        {'alertId': 'alert-missing', 'name': 'SQL注入', 'lastTime': 1791690000, 'proof': {}},
    ]}}
    facts, _ = safe_facts(raw)
    story = build_story({'id': 'incident-1', 'name': 'SQL注入'}, [proof(facts)])
    assert any('alert-missing' in gap and '未取得请求包' in gap for gap in story['gaps'])
    assert any('alert-missing' in gap and '未取得响应包' in gap for gap in story['gaps'])
    assert not any('alert-with-packets' in gap and '未取得请求包' in gap for gap in story['gaps'])


def test_explicit_empty_http_body_is_not_missing_packet():
    facts, partial = safe_facts({'data': {'uuId': 'alert-1', 'name': 'Web告警', 'proofType': ['http'],
        'proof': {'requestHead': 'GET / HTTP/1.1', 'requestBody': '',
                  'responseHead': 'HTTP/1.1 204 No Content', 'responseBody': ''}}})
    assert not partial
    story = build_story({'id': 'incident-1', 'name': 'Web告警'}, [proof(facts, kind='alert_proof')])
    assert any('正文为空，来源明确返回' in packet['text'] for packet in story['packets'])
    assert not any('未取得请求包' in gap or '未取得响应包' in gap for gap in story['gaps'])


@pytest.mark.parametrize('body,secret', [
    ('password="PRIVATE"&orderBy=@@version', 'PRIVATE'),
    ("password='PRIVATE WITH SPACES'&orderBy=@@version", 'PRIVATE WITH SPACES'),
    ('access_token="PRIVATE"; orderBy=@@version', 'PRIVATE'),
    ('{"password":12345678,"orderBy":"@@version"}', '12345678'),
    ('{"token":1.2345678e9,"orderBy":"@@version"}', '1.2345678e9'),
    ('{"password":-12345678,"orderBy":"@@version"}', '-12345678'),
    ('--boundary\r\nContent-Disposition: form-data; name="password"\r\n\r\nPRIVATE\r\n'
     '--boundary\r\nContent-Disposition: form-data; name="orderBy"\r\n\r\n@@version\r\n--boundary--', 'PRIVATE'),
    ("--boundary\nContent-Disposition: form-data; name='password'\nContent-Type: text/plain\n\nPRIVATE\n"
     '--boundary\nContent-Disposition: form-data; name="orderBy"\n\n@@version\n--boundary--', 'PRIVATE'),
    ('--boundary\nContent-Disposition: form-data; name=access_token\n\nPRIVATE\n'
     '--boundary\nContent-Disposition: form-data; name=orderBy\n\n@@version\n--boundary--', 'PRIVATE'),
    ('--boundary\nContent-Disposition: form-data; name="password"\n\nPRIVATE FIRST\nPRIVATE SECOND\n'
     '--boundary\nContent-Disposition: form-data; name="orderBy"\n\n@@version\n--boundary--', 'PRIVATE'),
])
def test_credential_variants_are_redacted_before_facts_story_mail_and_model_context(body, secret):
    redacted = redact_text(body)
    assert secret not in redacted and '@@version' in redacted and '[已脱敏]' in redacted
    assert redact_text(redacted) == redacted
    facts, partial = safe_facts({'data': {'uuId': 'alert-1', 'name': 'SQL注入', 'proofType': ['http'],
        'proof': {'requestBody': body, 'responseHead': 'HTTP/1.1 200 OK'}}})
    # Masking credentials alone does not claim a partial query or an original
    # unmodified packet. Real truncation is covered separately above.
    assert partial is False
    model_context = json.dumps({'evidence': [proof(facts, kind='alert_proof')]}, ensure_ascii=False)
    assert secret not in model_context and '@@version' in model_context and '[已脱敏]' in model_context
    story = build_story({'id': 'incident-1', 'name': 'SQL注入'}, [proof(facts, kind='alert_proof')],
                        {'reason': '需核对 SQL 语句及利用效果', 'evidence_ids': ['evidence-1']})
    for output in (story_text(story), story_text(story, mail=True), json.dumps(story, ensure_ascii=False)):
        assert secret not in output and '@@version' in output
    assert story['packets'][0]['truncated'] is False
    if 'Content-Disposition' in body:
        packet = facts['data']['proof']['requestBody']
        assert '--boundary' in packet and ('name="orderBy"' in packet or 'name=orderBy' in packet)
        assert 'Content-Disposition' in packet


def test_business_overview_omits_bookkeeping_and_keeps_source_timeline_and_original_proofs():
    from flocks.monitoring.event_details import readable_story
    story = {
        'basics': {'event_id': 'incident-00000000-0000-0000-0000-000000000000', 'name': 'DGA行为',
                   'host': '10.1.2.3', 'alert_ids': ['alert-00000000-0000-0000-0000-000000000001']},
        'device': '股份 XDR', 'verdict': 'risk',
        'conclusion': '事件 incident-00000000-0000-0000-0000-000000000000 有真实异常DNS请求 [evidence-2]。',
        'key_evidence': [{'evidence_id': 'evidence-2', 'text': '[evidence-2] 告警编号：alert-00000000-0000-0000-0000-000000000001；主机存在异常查询'}],
        'alert_details': [{'name': '随机域名查询', 'start_time': 1791860154, 'end_time': 1791860390,
                           'src_ip': '10.1.2.3', 'dst_ip': '223.5.5.5', 'dst_port': 53}],
        'timeline': [], 'gaps': [],
    }
    overview = readable_story(story)
    assert 'alert_ids' not in overview['basics']
    assert 'evidence-2' not in overview['conclusion']
    assert 'alert-0000' not in overview['evidence'][0]['text']
    assert [item['time'] for item in overview['timeline']] == [1791860154, 1791860390]
    assert '223.5.5.5:53' in overview['timeline'][0]['text']
    assert story['key_evidence'][0]['evidence_id'] == 'evidence-2'
