"""Failed notifications persist; explicit replay cannot resend accepted mail."""
import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from flocks.monitoring import mail_retry as retry, mailflow as m, mail_manual
from flocks.monitoring.store import rows, write, encode
from flocks.session.interaction_policy import unattended_scope
from flocks.task.store import TaskStore
from flocks.task.models import SchedulerStatus
from tests.monitoring.test_automatic import auto, notice, reply, Recorder, writes, consume, interpretation


def request():
    return retry.RetryMailRequest(request_id=uuid4())


async def failure(auto):
    auto.send.side_effect = m.transport.TransportNotReady('SMTP unavailable before sending')
    n = await notice(auto)
    auto.send.side_effect = None
    return n


async def test_failed_notice_retained_across_rounds_without_feedback(auto):
    n = await failure(auto)
    for _ in range(3):
        await notice(auto)
    history = await m.history('owner')
    assert auto.send.await_count == 1 and not writes(auto)
    row = history['notices'][0]
    assert row['id'] == n['id'] and row['state'] == 'queued'
    assert row['retry']['available'] and row['disposition_state'] == 'unhandled'
    assert not row['reply_received'] and len(history['notices']) == 1


async def test_explicit_retry_reuses_notification_and_repeated_request_is_idempotent(auto):
    n = await failure(auto)
    req = request()
    a, b = await asyncio.gather(retry.retry('owner', n['id'], req), retry.retry('owner', n['id'], req))
    assert a == b and a['state'] == 'sent' and not a['retry']['available']
    assert auto.send.await_count == 2 and not writes(auto)
    assert auto.send.call_args.args[0]['message_id'] == n['message_id']
    assert auto.send.call_args.args[0]['body'] == n['body']
    assert auto.send.call_args.args[0]['recipient'] == n['recipient']
    assert len(await rows('SELECT * FROM monitor_mail_notices')) == 1
    assert len(await rows('SELECT * FROM monitor_mail_retries')) == 1
    await notice(auto)
    assert auto.send.await_count == 2


async def test_retry_failure_can_retry_with_new_request_but_not_replay(auto):
    n = await failure(auto)
    auto.send.side_effect = m.transport.TransportNotReady('still down')
    req = request()
    assert (await retry.retry('owner', n['id'], req))['retry']['available']
    await retry.retry('owner', n['id'], req)
    assert auto.send.await_count == 2
    auto.send.side_effect = None
    assert (await retry.retry('owner', n['id'], request()))['state'] == 'sent'
    assert auto.send.await_count == 3 and not writes(auto)


async def test_unknown_retry_outcome_cannot_be_replayed_after_recovery(auto):
    n = await failure(auto)
    auto.send.side_effect = TimeoutError()
    req = request()
    result = await retry.retry('owner', n['id'], req)
    assert result['state'] == 'send_unknown' and not result['retry']['available']
    await m.recover()
    await retry.retry('owner', n['id'], req)
    with pytest.raises(ValueError):
        await retry.retry('owner', n['id'], request())
    assert auto.send.await_count == 2 and not writes(auto)


@pytest.mark.parametrize('state', ['sent','send_unknown','sending','skipped'])
async def test_nonfailed_notifications_never_retry(auto, state):
    n = await notice(auto)
    await write('UPDATE monitor_mail_notices SET state=? WHERE id=?', (state,n['id']))
    with pytest.raises(ValueError):
        await retry.retry('owner', n['id'], request())
    assert auto.send.await_count == 1 and not writes(auto)


async def test_feedback_or_manual_handled_blocks_retry(auto):
    n = await failure(auto)
    # A late reply remains feedback even if send result was previously ambiguous.
    await write("UPDATE monitor_mail_notices SET state='send_unknown' WHERE id=?", (n['id'],))
    await reply(auto, n)
    await write("UPDATE monitor_mail_notices SET state='queued' WHERE id=?", (n['id'],))
    with pytest.raises(ValueError, match='反馈'):
        await retry.retry('owner', n['id'], request())
    assert auto.send.await_count == 1 and not writes(auto)


async def test_manual_handled_then_retry_rejected(auto):
    n = await failure(auto)
    await mail_manual.change('owner', n['id'], mail_manual.ManualMailRequest(request_id=uuid4(),state='handled'), auto.adapter)
    with pytest.raises(ValueError, match='反馈'):
        await retry.retry('owner', n['id'], request())
    assert auto.send.await_count == 1


async def test_paused_scheduler_still_allows_explicit_failed_mail_retry(auto):
    n = await failure(auto)
    auto.scheduler.status = SchedulerStatus.DISABLED
    await TaskStore.update_scheduler(auto.scheduler)
    assert (await retry.retry('owner', n['id'], request()))['state'] == 'sent'
    assert not writes(auto)


@pytest.mark.parametrize('change', ['owner','project','device','recipient','revision','disabled'])
async def test_retry_validates_original_target_and_mailbox(auto, change):
    n = await failure(auto)
    owner = 'other' if change == 'owner' else 'owner'
    if change == 'project':
        await write("UPDATE monitor_installations SET project='other'")
    elif change == 'device':
        policy = auto.policy.model_copy(update={'devices':['other']})
        await write('UPDATE monitor_installations SET policy=?', (encode(policy.model_dump()),))
    elif change == 'recipient':
        await write("UPDATE monitor_mail_settings SET recipient='other@example.com'")
    elif change == 'revision':
        await write("UPDATE monitor_mail_settings SET revision='other'")
    elif change == 'disabled':
        await write('UPDATE monitor_mail_settings SET enabled=0')
    with pytest.raises((FileNotFoundError, ValueError)):
        await retry.retry(owner,n['id'],request())
    assert auto.send.await_count == 1 and not writes(auto)


async def test_background_cannot_use_manual_retry_api(auto):
    n = await failure(auto)
    with unattended_scope(), pytest.raises(Exception):
        await retry.retry('owner', n['id'], request())
    assert auto.send.await_count == 1


async def test_failure_before_preparation_can_build_notice_and_retry(auto):
    n = await failure(auto)
    await write("UPDATE monitor_mail_notices SET subject='',body='' WHERE id=?", (n['id'],))
    result = await retry.retry('owner', n['id'], request(), auto.adapter)
    assert result['state'] == 'sent' and auto.send.await_count == 2
    assert not writes(auto)


async def test_notified_incident_outside_new_query_window_remains_pending(auto):
    n = await failure(auto)
    auto.leave_scope = True
    await notice(auto)
    result = await retry.retry('owner', n['id'], request())
    assert result['state'] == 'sent'
    assert auto.send.call_args.args[0]['body'] == n['body'] and not writes(auto)
    assert (await m.history('owner'))['notices'][0]['disposition_state'] == 'unhandled'


async def test_retry_then_reply_updates_original_incident_once(auto):
    n = await failure(auto)
    await retry.retry('owner', n['id'], request())
    msg = await reply(auto, n)
    assert (await consume(auto, interpretation(n,msg.text)))['verified'] == 1
    await consume(auto, interpretation(n,msg.text))
    assert len(writes(auto)) == 1
    assert (await m.history('owner'))['notices'][0]['disposition_state'] == 'handled'


def test_retry_payload_does_not_allow_redirect_or_replacement_content():
    for extra in ('recipient','device','body','notice_id'):
        with pytest.raises(ValidationError):
            retry.RetryMailRequest(request_id=uuid4(), **{extra:'attacker'})


async def test_failed_preparation_remains_visible_even_if_event_leaves_scope(auto):
    n = await failure(auto)
    await write("UPDATE monitor_mail_notices SET subject='',body='' WHERE id=?", (n['id'],))
    auto.leave_scope = True
    result = await retry.retry('owner', n['id'], request(), auto.adapter)
    assert result['state'] == 'queued' and result['error']
    history = await m.history('owner')
    assert history['notices'][0]['id'] == n['id']
    assert history['notices'][0]['disposition_state'] == 'unhandled' and not writes(auto)


async def test_two_different_clicks_in_parallel_deliver_one_original_notice(auto):
    n = await failure(auto)
    results = await asyncio.gather(retry.retry('owner',n['id'],request()),
        retry.retry('owner',n['id'],request()), return_exceptions=True)
    assert sum(isinstance(r, dict) and r['state']=='sent' for r in results) == 1
    assert sum(isinstance(r, ValueError) for r in results) == 1
    assert auto.send.await_count == 2 and not writes(auto)


async def test_retry_route_ownership_validation_and_replay(auto):
    from fastapi import FastAPI
    from types import SimpleNamespace
    from flocks.server.routes import security_monitoring as api
    import httpx
    n = await failure(auto)
    app=FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.require_user]=lambda:SimpleNamespace(id='owner')
    path=f'/monitoring/host-security-monitor/mail/notices/{n["id"]}/retry'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        body=request().model_dump(mode='json')
        assert (await client.post(path,json={**body,'recipient':'other@example.com'})).status_code==422
        assert (await client.post(path.replace(n['id'],str(uuid4())),json=body)).status_code==404
        response=await client.post(path,json=body)
        assert response.status_code==200 and response.json()['state']=='sent'
        assert (await client.post(path,json=body)).status_code==200
        assert (await client.post(path,json=request().model_dump(mode='json'))).status_code==409
        assert auto.send.await_count == 2 and not writes(auto)
