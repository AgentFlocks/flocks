"""Local snapshot disappearance is neither a benign verdict nor an XDR write."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from flocks.monitoring import assessment as a, followup as f, investigation as i
from flocks.monitoring import mailflow, mail_manual, mail_retry
from flocks.monitoring.adapter import normalize
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import encode, rows, write


@pytest.fixture
async def case(tmp_path):
    policy = MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['one', 'two'])
    event = normalize('one', {'uuId': 'synthetic-event', 'name': 'Synthetic security event',
        'incidentSeverity': 2, 'hostIp': '192.0.2.1', 'dealStatus': 0,
        'whiteStatus': '未加白', 'startTime': 10, 'endTime': 100})
    event['investigationWindow'] = {'start': 0, 'end': 100}
    return SimpleNamespace(policy=policy, event=event)


async def decision(case, verdict='unknown'):
    proof = {'id': 'evidence-1', 'schema': i.EVIDENCE_SCHEMA, 'event': case.event['id'],
        'device': case.event['device'], 'event_revision': i.fingerprint(case.event),
        'window': case.event['investigationWindow'], 'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'success': True, 'partial': False, 'facts': {'srcIp': case.event['host'], 'dstIp': '192.0.2.2', 'dstPort': 443}}
    result = {'verdict': verdict, 'confidence': 'high', 'risk_confirmed': verdict == 'risk',
        'blocking_gaps': [], 'evidence_ids': ['evidence-1'], 'reason': '已核对具体连接行为。',
        'evidence_schema': i.EVIDENCE_SCHEMA}
    case.event['investigation'] = {'state': 'ready', **result, 'event_revision': i.fingerprint(case.event)}
    await write('INSERT INTO monitor_investigations(owner,project,event_key,event,evidence,result,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',
        (case.policy.owner, case.policy.project, case.event['key'], encode(case.event), encode([proof]), encode(result), 'ready', f.d.stamp()))
    return (await rows('SELECT * FROM monitor_investigations'))[0]


async def notice(case, state='sent'):
    nid = str(uuid4())
    await write('INSERT INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,error,created_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (nid, case.policy.owner, case.policy.scope, case.policy.project, case.event['key'],
         'owner@example.com', 'revision', 'mailbox', f'<{nid}@example.com>', 'Synthetic notice', 'Synthetic body',
         encode(case.event), state, 'Synthetic known failure' if state == 'queued' else None, f.d.stamp(), f.d.stamp()))
    return (await rows('SELECT * FROM monitor_mail_notices WHERE id=?', (nid,)))[0]


@pytest.mark.parametrize('verdict', ['unknown', 'risk', 'benign'])
async def test_absence_ends_any_verdict_locally_and_preserves_assessment(case, verdict):
    persisted = await decision(case, verdict)
    await f.observe(case.policy, case.event, 'first')
    before = (await rows('SELECT * FROM monitor_event_followup'))[0]
    ended = await f.end_missing(case.policy, case.event, 'missing')
    after = (await rows('SELECT * FROM monitor_event_followup'))[0]
    assert ended['state'] == 'monitoring_ended' and ended['closed']
    assert not ended['xdr_closed'] and not ended['external']
    assert '未确认风险消除' in ended['reason'] and '实际处置结果' in ended['reason']
    assert before['decision'] == after['decision'] and before['watch_count'] == after['watch_count']
    assert after['queued_round'] is None and after['target_status'] is None
    value = a.apply_followup(a.assess_event(case.event, persisted), case.event, ended)
    assert value['status'] == 'ended' and value['ended'] and not value['notify_eligible']
    assert '未确认风险消除' in value['reason'] and value['investigation_reason']
    assert value['verdict'] == verdict and value['label'] == '已归档'
    assert not await rows('SELECT * FROM monitor_dispositions')
    assert not await rows('SELECT * FROM monitor_mail_notices')


@pytest.mark.parametrize('state', ['watching', 'risk_open', 'queued', 'writing', 'pending', 'invalidated'])
async def test_any_previous_local_progress_can_end_and_unknown_audit_is_not_confirmed(case, state):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    await write('UPDATE monitor_event_followup SET state=?,target_status=60,queued_round=?,disposition_id=?', (state, 'old-round', 'unknown-write'))
    await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at,mode,target_status,project) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('unknown-write', 'owner', case.policy.scope, case.event['key'],
        'Synthetic uncertain write', 'pending', f.d.stamp(), f.d.stamp(), 'followup', 60, case.policy.project))
    ended = await f.end_missing(case.policy, case.event, 'missing')
    assert ended['closed'] and not ended['xdr_closed'] and ended['target_status'] is None
    audit = (await rows('SELECT * FROM monitor_dispositions'))[0]
    assert audit['status'] == 'pending' and audit['observed_status'] is None
    assert ended['disposition_id'] == 'unknown-write'
    result = await f.process_due(case.policy, 'session', 'later', adapter_factory=lambda *_: None)
    assert result['processed'] == 0


async def test_absence_can_end_uninvestigated_event_without_creating_fake_conclusion(case):
    ended = await f.end_missing(case.policy, case.event, 'missing')
    assert ended['closed'] and ended['watch_count'] == 0
    assert not await rows('SELECT * FROM monitor_investigations')
    assert not await rows('SELECT * FROM monitor_dispositions')


@pytest.mark.parametrize('state', ['verified', 'external_closed'])
async def test_actual_terminal_state_has_priority_over_local_disappearance(case, state):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    await write('UPDATE monitor_event_followup SET state=?,result=?', (state, encode({'observed_status': 40, 'reason': '已回查确认'})))
    terminal = await f.end_missing(case.policy, case.event, 'missing')
    assert terminal['state'] == state and terminal['xdr_closed']
    projected = a.apply_followup(a.assess_event(case.event), case.event, terminal)
    assert projected['ended'] and projected['status'] == 'ended'
    assert '待观察' not in projected['label']


async def test_cached_observation_cannot_reopen_but_source_presence_can(case):
    await decision(case, 'benign')
    first = await f.observe(case.policy, case.event, 'first')
    assert first['target_status'] == 60
    await f.end_missing(case.policy, case.event, 'missing')
    stale = await f.observe(case.policy, case.event, 'cached', query_complete=True)
    assert stale['state'] == 'monitoring_ended'
    restored = await f.restore_present(case.policy, case.event, 'present')
    assert restored['state'] == 'watching' and not restored['closed']
    assert restored['watch_count'] == first['watch_count']
    assert restored['target_status'] is None and restored['queued_round'] is None
    assert (await f.process_due(case.policy, 'session', 'next', adapter_factory=lambda *_: None))['processed'] == 0


async def test_new_activity_reappearance_starts_a_new_observation_count(case):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    await f.end_missing(case.policy, case.event, 'missing')
    current = {**case.event, 'endTime': 101}
    restored = await f.restore_present(case.policy, current, 'present')
    assert restored['watch_count'] == 0 and restored['last_round'] is None
    assert restored['event_version'] == f.event_version(current)


@pytest.mark.parametrize('state', ['verified', 'external_closed'])
async def test_explicit_source_reopen_releases_old_terminal_even_on_returned_partial_page(case, state):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    await write('UPDATE monitor_event_followup SET state=?,result=?', (state, encode({'observed_status': 40})))
    restored = await f.restore_present(case.policy, case.event, 'partial-page-present')
    assert restored['state'] == 'watching' and not restored['closed']
    assert restored['watch_count'] == 0 and restored['observed_status'] == 0


async def test_pending_and_status_summary_stop_counting_missing_event_and_resume_on_presence(case):
    await decision(case)
    await write("UPDATE monitor_investigations SET state='pending'")
    assert len(await i.pending(case.policy)) == 1
    await f.end_missing(case.policy, case.event, 'missing')
    assert not await i.pending(case.policy)
    assert (await i.status_summary(case.policy))['pending'] == 0
    assert (await i.status_summary(owner='owner', project='project'))['pending'] == 0
    await f.restore_present(case.policy, case.event, 'present')
    assert len(await i.pending(case.policy)) == 1
    assert (await i.status_summary(case.policy))['pending'] == 1


async def test_project_device_and_owner_do_not_share_local_ending(case):
    await decision(case)
    await f.end_missing(case.policy, case.event, 'missing')
    assert await f.is_monitoring_ended('owner', 'project', case.event['key'])
    assert not await f.is_monitoring_ended('other', 'project', case.event['key'])
    assert not await f.is_monitoring_ended('owner', 'other', case.event['key'])
    other = {**case.event, 'device': 'two', 'key': f'two:incident:{case.event["id"]}'}
    assert (await f.restore_present(case.policy, other, 'other-source'))['state'] == 'watching'
    assert await f.is_monitoring_ended('owner', 'project', case.event['key'])


async def test_ended_event_disables_manual_label_and_retry_and_cancels_unsent_notice(case):
    n = await notice(case, 'queued')
    await write('UPDATE monitor_mail_notices SET error=NULL WHERE id=?', (n['id'],))
    await f.end_missing(case.policy, case.event, 'missing')
    manual = await mail_manual.snapshot(n)
    retry = await mail_retry.snapshot(n)
    assert not manual['available'] and manual['monitoring_ended']
    assert not retry['available']
    stored = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert stored['state'] == 'skipped' and stored['sent_at'] is None


async def test_known_failed_delivery_remains_visible_in_history_after_local_end(case):
    n = await notice(case, 'queued')
    await f.end_missing(case.policy, case.event, 'missing')
    stored = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert stored['state'] == 'queued' and stored['error'] == n['error']
    assert not (await mail_retry.snapshot(stored))['available']


async def test_ended_event_cannot_send_notify_or_delivery_directly(case, monkeypatch):
    n = await notice(case, 'queued')
    await f.end_missing(case.policy, case.event, 'missing')
    send = AsyncMock()
    monkeypatch.setattr(mailflow.transport, 'send', send)
    authorize = AsyncMock(side_effect=AssertionError('must not authorize or contact XDR'))
    monkeypatch.setattr(mailflow, 'authorized', authorize)
    assert await mailflow.notify_one(case.policy, n, 'session') in {'skipped', 'already_attempted'}
    assert await mailflow.deliver_notice(n, 'session') == 'skipped'
    assert not send.called and not authorize.called


async def test_late_reply_is_archived_without_xdr_write_or_false_confirmation(case, monkeypatch):
    n = await notice(case)
    await f.end_missing(case.policy, case.event, 'missing')
    rid = str(uuid4())
    await write('INSERT INTO monitor_mail_replies(id,owner,project,mailbox,message_id,sender,payload,state,received_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?)', (rid, 'owner', 'project', 'mailbox', '<late@example.com>', 'owner@example.com',
        encode({'text': '已处理', 'authenticated_sender': True}), 'interpreted', f.d.stamp(), f.d.stamp()))
    await write('INSERT INTO monitor_mail_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        ('item', rid, n['id'], 'owner', 'project', 40, '已处理', 'pending', None, f.d.stamp(), f.d.stamp()))
    monkeypatch.setattr(mailflow, 'authorized', AsyncMock())
    monkeypatch.setattr(mailflow, 'settings', AsyncMock(return_value={'revision': 'revision'}))
    def forbidden(*_):
        pytest.fail('ended events must never contact XDR')
    reply = (await rows('SELECT * FROM monitor_mail_replies'))[0]
    await mailflow.process_reply(case.policy, reply, 'session', forbidden, AsyncMock(), lambda *_: None)
    assert (await rows('SELECT state FROM monitor_mail_items'))[0]['state'] == 'monitoring_ended'
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'monitoring_ended'
    assert not await rows('SELECT * FROM monitor_dispositions')
    assert (await f.snapshot(case.policy))[case.event['key']]['state'] == 'monitoring_ended'


async def test_completed_source_record_is_not_hidden_by_stale_local_ending(case):
    await f.end_missing(case.policy, case.event, 'missing')
    projection = (await f.snapshot(case.policy))[case.event['key']]
    ended = a.apply_followup(a.assess_event(case.event), case.event, projection)
    current = {**case.event, 'dealStatus': 40}
    confirmed = a.apply_followup(a.assess_event(current), current, projection)
    assert ended['monitoring_ended'] and not ended['xdr_closed']
    assert confirmed['ended'] and confirmed['xdr_closed'] and not confirmed['monitoring_ended']
    assert confirmed['label'] == '来源已处置'


async def test_actual_manual_tag_and_retry_endpoints_reject_ended_event(case, monkeypatch):
    n = await notice(case, 'queued')
    await f.end_missing(case.policy, case.event, 'missing')
    monkeypatch.setattr(mail_manual, 'bound_notice', AsyncMock(return_value=(n, case.policy, case.event)))
    monkeypatch.setattr(mail_manual, 'require_interactive', AsyncMock())
    monkeypatch.setattr(mail_retry, 'require_interactive', AsyncMock())
    with pytest.raises(ValueError, match='未再检出'):
        await mail_manual.change('owner', n['id'], mail_manual.ManualMailRequest(request_id=uuid4(), state='handled'))
    with pytest.raises(ValueError, match='未再检出'):
        await mail_retry.retry('owner', n['id'], mail_retry.RetryMailRequest(request_id=uuid4()))
    assert not await rows('SELECT * FROM monitor_dispositions')
    assert not await rows('SELECT * FROM monitor_mail_retries')


async def test_new_notice_intent_cannot_be_created_from_ended_risk_case(case, monkeypatch):
    await decision(case, 'risk')
    await f.end_missing(case.policy, case.event, 'missing')
    monkeypatch.setattr(mailflow, 'settings', AsyncMock(return_value={
        'enabled': True, 'project': case.policy.project, 'recipient': 'owner@example.com'}))
    assessment = await mailflow.notification_assessment(case.policy, case.event)
    assert assessment['status'] == 'ended' and assessment['verdict'] == 'risk'
    assert not assessment['notify_eligible']
    await mailflow.queue_notices(case.policy, [case.event], 'session')
    assert not await rows('SELECT * FROM monitor_mail_notices')


async def audit(case, state, *, mode='mail_manual', target=40, identifier='audit'):
    await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,observed_status,created_at,updated_at,mode,target_status,project,decision) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identifier, case.policy.owner, case.policy.scope, case.event['key'], 'Synthetic feedback', state,
         target if state == 'verified' else None, f.d.stamp(), f.d.stamp(), mode, target, case.policy.project,
         encode({'event_version': f.event_version(case.event)})))


@pytest.mark.parametrize('state', ['queued', 'writing', 'pending'])
async def test_end_cancels_only_unstarted_human_intent_and_reopen_cannot_replay_it(case, state):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    await audit(case, state)
    await f.end_missing(case.policy, case.event, 'missing')
    record = (await rows('SELECT * FROM monitor_dispositions'))[0]
    assert record['status'] == ('failed' if state == 'queued' else state)
    if state == 'queued':
        assert '未发起 XDR 写入' in record['error']
    await f.restore_present(case.policy, case.event, 'present')
    assert (await rows('SELECT * FROM monitor_dispositions'))[0]['status'] == record['status']


@pytest.mark.parametrize('mode', ['followup', 'mail', 'mail_manual'])
async def test_verified_disposition_is_preserved_when_source_stops_returning_incident(case, mode):
    await f.restore_present(case.policy, case.event, 'present')
    await decision(case, 'risk')
    await f.observe(case.policy, case.event, 'investigated')
    await audit(case, 'verified', mode=mode)
    closed = await f.end_missing(case.policy, case.event, 'missing')
    assert closed['state'] == 'verified' and closed['xdr_closed']
    assert closed['observed_status'] == 40 and closed['disposition_id'] == 'audit'
    assert not closed['monitoring_ended']


async def test_verified_disposition_from_before_real_source_reopen_is_not_current_closure(case):
    await decision(case, 'risk')
    await f.observe(case.policy, case.event, 'first')
    await audit(case, 'verified')
    await f.restore_present(case.policy, case.event, 'source-returned-active-after-old-write')
    await f.observe(case.policy, case.event, 'new-observation')
    ended = await f.end_missing(case.policy, case.event, 'missing')
    assert ended['state'] == 'monitoring_ended' and not ended['xdr_closed']
    assert (await rows('SELECT * FROM monitor_dispositions'))[0]['status'] == 'verified'


@pytest.mark.parametrize('received_at', ['2026-10-14T01:05:00+00:00', '2026-10-14T01:15:00+00:00'])
async def test_old_or_late_feedback_during_disappearance_cannot_replay_after_reappearance(case, monkeypatch, received_at):
    clock = ['2026-10-14T01:00:00+00:00']
    monkeypatch.setattr(f.d, 'stamp', lambda: clock[0])
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    n = await notice(case)
    clock[0] = '2026-10-14T01:10:00+00:00'
    await f.end_missing(case.policy, case.event, 'missing')
    clock[0] = '2026-10-14T01:20:00+00:00'
    await f.restore_present(case.policy, case.event, 'present')
    await f.observe(case.policy, case.event, 'new-observation')
    result = json.loads((await rows('SELECT result FROM monitor_event_followup'))[0]['result'])
    assert result['monitoring_end']['ended_at'] == '2026-10-14T01:10:00+00:00'
    assert result['monitoring_restore']['restored_at'] == clock[0]
    assert await f.feedback_expired('owner', 'project', case.event['key'], received_at)
    assert not await f.feedback_expired('owner', 'project', case.event['key'], '2026-10-14T01:21:00+00:00')
    rid = str(uuid4())
    await write('INSERT INTO monitor_mail_replies(id,owner,project,mailbox,message_id,sender,payload,state,received_at,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?)', (rid, 'owner', 'project', 'mailbox', f'<{rid}@example.com>', 'owner@example.com',
        encode({'text': '处理完成'}), 'interpreted', received_at, clock[0]))
    item = {'id': 'late-item', 'notice_id': n['id'], 'reply_id': rid, 'target': 40, 'reason': '处理完成'}
    def forbidden(*_):
        pytest.fail('stale feedback must never contact XDR')
    assert await mailflow.mark_item(case.policy, item, 'session', forbidden) == 'monitoring_ended'
    assert not await rows('SELECT * FROM monitor_dispositions')
