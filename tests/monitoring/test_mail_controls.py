"""Operator mail actions preserve one incident record and next-round XDR writes."""
import json
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from flocks.monitoring import mailflow, mail_retry, mail_manual
from flocks.monitoring.store import rows, write
from tests.monitoring.test_automatic import auto, notice, reply, consume, interpretation, writes, Recorder
from tests.monitoring.test_mail_manual import case, change, request, consume_queued


async def test_unanswered_sent_notice_can_be_resent_without_duplicate_alert_or_xdr_write(auto):
    original = await notice(auto)
    req = mail_retry.RetryMailRequest(request_id=uuid4(), expected_updated_at=original['updated_at'])
    result = await mail_retry.retry('owner', original['id'], req)
    assert result['state'] == 'sent' and result['retry']['available']
    assert auto.send.await_count == 2 and not writes(auto)
    delivered = auto.send.call_args.args[0]
    assert delivered['message_id'] != original['message_id']
    assert delivered['recipient'] == original['recipient'] and delivered['body'] == original['body']
    assert len(await rows('SELECT * FROM monitor_mail_notices')) == 1
    assert (await rows('SELECT message_id FROM monitor_mail_retries'))[0]['message_id'] == delivered['message_id']
    await mail_retry.retry('owner', original['id'], req)
    assert auto.send.await_count == 2
    await notice(auto)
    assert auto.send.await_count == 2  # Background ticks never send reminders.


async def test_reply_to_resend_message_id_links_to_original_notice_and_verifies_feedback(auto):
    original = await notice(auto)
    await mail_retry.retry('owner', original['id'], mail_retry.RetryMailRequest(request_id=uuid4()))
    message_id = auto.send.call_args.args[0]['message_id']
    msg = await reply(auto, {**original, 'message_id': message_id})
    result = await consume(auto, interpretation(original, msg.text))
    assert result['verified'] == 1 and len(writes(auto)) == 1
    history = await mailflow.history('owner')
    assert len(history['notices']) == 1 and history['notices'][0]['reply_received']
    assert history['notices'][0]['disposition_state'] == 'handled'
    assert not history['notices'][0]['retry']['available']


async def test_stale_send_click_does_not_send_again_after_another_delivery(auto):
    original = await notice(auto)
    await mail_retry.retry('owner', original['id'], mail_retry.RetryMailRequest(request_id=uuid4()))
    with pytest.raises(ValueError, match='已更新'):
        await mail_retry.retry('owner', original['id'], mail_retry.RetryMailRequest(
            request_id=uuid4(), expected_updated_at=original['updated_at']))
    assert auto.send.await_count == 2


async def test_smtp_health_failure_keeps_risk_notice_and_full_preview_before_delivery(auto, monkeypatch):
    monkeypatch.setattr(mailflow, 'health_snapshot', AsyncMock(return_value={
        'enabled': True, 'send': {'state': 'unavailable'}, 'receive': {'state': 'unavailable'},
        'errors': ['SMTP连接暂不可用'],
    }))
    await mailflow.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    history = await mailflow.history('owner')
    record = history['notices'][0]
    assert record['state'] == 'blocked' and '未发送' in record['error']
    assert record['subject'] and '调查结论' in record['body']
    assert json.loads((await rows('SELECT event FROM monitor_mail_notices'))[0]['event'])['mail_presentation']['html_body']
    assert record['retry']['available'] and record['manual']['available']
    assert auto.send.await_count == 0 and not writes(auto)
    # A user may retry the prepared notice; health caches do not veto an explicit attempt.
    result = await mail_retry.retry('owner', record['id'], mail_retry.RetryMailRequest(request_id=uuid4()))
    assert result['state'] == 'sent' and auto.send.await_count == 1
    assert len(await rows('SELECT * FROM monitor_mail_notices')) == 1


async def test_imap_health_problem_does_not_block_smtp(auto, monkeypatch):
    monkeypatch.setattr(mailflow, 'health_snapshot', AsyncMock(return_value={
        'enabled': True, 'send': {'state': 'healthy'}, 'receive': {'state': 'unavailable'}, 'errors': ['IMAP不可用'],
    }))
    assert (await notice(auto))['state'] == 'sent' and auto.send.await_count == 1


async def test_new_pending_intent_has_subject_and_body_even_without_sender_execution(auto):
    await mailflow.queue_notices(auto.policy, [auto.event], auto.session.id)
    record = (await mailflow.history('owner'))['notices'][0]
    assert record['state'] == 'queued' and record['subject'] and record['body']
    assert record['retry']['available'] and auto.send.await_count == 0


async def test_ignore_with_notes_is_queued_then_written_and_confirmed_on_next_round(case):
    case.statuses = [0, 60]
    req = request('ignored', notes='  风险已核实，按变更安排忽略本条。  ')
    queued = await change(case, req)
    assert queued['state'] == 'pending' and queued['target_status'] == 60
    assert queued['notes'] == '风险已核实，按变更安排忽略本条。' and not case.calls
    assert (await consume_queued(case))['state'] == 'ignored'
    writes = [p for _, p in case.calls if p['action'] == 'update_status']
    assert len(writes) == 1 and writes[0]['deal_status'] == 60
    assert queued['notes'] in writes[0]['deal_comment']
    record = (await mailflow.history('owner'))['notices'][0]
    assert record['disposition_state'] == 'ignored' and record['manual']['notes'] == queued['notes']
    assert not record['retry']['available'] and not record['manual']['available']


async def test_uncertain_ignore_only_reads_back_target_sixty_and_keeps_notes(case):
    case.statuses = [0, 0]
    await change(case, request('ignored', notes='保留处置依据'))
    assert (await consume_queued(case))['state'] == 'pending'
    case.statuses = [60]
    result = await mail_manual.recheck('owner', case.notice, case.adapter)
    assert result['state'] == 'ignored' and result['notes'] == '保留处置依据'
    assert sum(p['action'] == 'update_status' for _, p in case.calls) == 1


async def test_handled_notes_are_durable_and_request_id_cannot_change_notes(case):
    req = request(notes='已清理样本并复查')
    assert (await change(case, req))['notes'] == req.notes
    with pytest.raises(ValueError, match='其他操作'):
        await change(case, req.model_copy(update={'notes': '不同说明'}))
    assert (await consume_queued(case))['state'] == 'handled'
    decision = json.loads((await rows('SELECT decision FROM monitor_dispositions'))[0]['decision'])
    assert decision['notes'] == req.notes


def test_notes_and_retry_context_cannot_redirect_the_target():
    with pytest.raises(ValidationError):
        request('ignored', notes='x' * 2001)
    with pytest.raises(ValidationError):
        mail_retry.RetryMailRequest(request_id=uuid4(), recipient='other@example.com')


async def test_verified_xdr_terminal_prevents_unanswered_mail_reminders(auto):
    from flocks.monitoring import followup
    n = await notice(auto)
    await followup.observe(auto.policy, auto.event, 'source-round')
    await write("UPDATE monitor_event_followup SET state='external_closed',result=json_set(result,'$.observed_status',40)")
    history = await mailflow.history('owner')
    assert not history['notices'][0]['retry']['available']
    assert not history['notices'][0]['manual']['available']
    assert history['notices'][0]['disposition_state'] == 'handled'
    with pytest.raises(ValueError):
        await mail_retry.retry('owner', n['id'], mail_retry.RetryMailRequest(request_id=uuid4()))
    assert auto.send.await_count == 1


def test_suite_requires_updated_mail_controls_contract():
    from pathlib import Path
    from flocks.monitoring.lifecycle import CORE_CAPABILITIES
    manifest = json.loads((Path(__file__).parents[2]/'.flocks/flockshub/plugins/components/host-security-monitor/manifest.json').read_text())
    assert 'monitor.mail-controls.v1' in CORE_CAPABILITIES
    assert 'monitor.mail-controls.v1' in manifest['requiredCoreCapabilities']


async def test_full_operator_notes_survive_bounded_xdr_comment(case):
    full = '备' * 2000
    await change(case, request(notes=full))
    await consume_queued(case)
    mutation = next(p for _, p in case.calls if p['action'] == 'update_status')
    assert len(mutation['deal_comment']) <= 2048
    record = (await mailflow.history('owner'))['notices'][0]
    assert record['manual']['notes'] == full


async def test_failed_reminder_keeps_previous_accepted_delivery_fact(auto):
    from flocks.monitoring.reports import snapshot
    original = await notice(auto)
    auto.send.side_effect = mailflow.transport.TransportNotReady('SMTP暂不可用')
    result = await mail_retry.retry('owner', original['id'], mail_retry.RetryMailRequest(request_id=uuid4()))
    assert result['state'] == 'queued'
    history = await mailflow.history('owner')
    assert history['notices'][0]['sent_at'] == original['sent_at']
    assert history['counts']['sent'] == 1
    report = await snapshot('owner', auto.policy.scope, auto.day)
    assert report['mail']['sentToday'] == 1
    await notice(auto)
    assert auto.send.await_count == 2


async def test_background_cannot_take_over_manual_reminder_intent(auto):
    n = await notice(auto)
    await write("UPDATE monitor_mail_notices SET state='queued',error=NULL")
    pending = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert await mailflow.notify_one(auto.policy, pending, auto.session.id, auto.adapter) == 'already_attempted'
    assert auto.send.await_count == 1 and n['sent_at']
