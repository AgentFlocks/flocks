"""Mail dependency facts and recovery; all network endpoints are synthetic."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.channel.base import OutboundContext
from flocks.channel.builtin.email.channel import EmailChannel
from flocks.channel.builtin.email.config import resolved_config
from flocks.monitoring import mailflow, mail_transport
from flocks.monitoring.store import rows, write


@pytest.fixture
def channel(monkeypatch):
    plugin = EmailChannel()
    plugin._resolved = resolved_config({'address': 'agent@example.com', 'password': 'PRIVATE',
        'imapHost': 'imap.example.com', 'smtpHost': 'smtp.example.com', 'allowAll': True,
        'pollIntervalSeconds': 30})
    monkeypatch.setattr(mail_transport.default_registry, '_channels', {'email': plugin})
    monkeypatch.setattr(mailflow, 'settings', AsyncMock(return_value={
        'enabled': 1, 'project': 'project', 'mailbox': mail_transport.mailbox_key(plugin._resolved)}))
    return plugin


async def test_receive_failure_is_not_hidden_by_smtp_success(channel):
    channel._health_success('receive', 'poll')
    previous = channel.health_snapshot()['receive']['last_success_at']
    channel._health_stage('receive', 'search')
    channel._health_failure('receive', TimeoutError('PRIVATE_TOKEN example.com'))
    channel._health_success('send', 'send')
    result = await mailflow.health_snapshot('owner', 'project')
    assert result['receive']['state'] == 'unavailable'
    assert result['receive']['stage'] == 'search'
    assert result['receive']['last_success_at'] == previous
    assert result['receive']['consecutive_failures'] == 1
    assert result['send']['state'] == 'healthy'
    assert len(result['errors']) == 1 and '无法确认是否有新回信' in result['errors'][0]
    serialized = json.dumps(result)
    assert 'PRIVATE' not in serialized and 'example.com' not in serialized


@pytest.mark.parametrize('enabled,project', [(0, 'project'), (1, 'other-project')])
async def test_disabled_or_other_project_has_no_mail_dependency_error(channel, enabled, project):
    mailflow.settings.return_value['enabled'] = enabled
    channel._health_failure('receive', OSError('PRIVATE'))
    result = await mailflow.health_snapshot('owner', project)
    assert not result['enabled'] and not result['errors']
    assert result['receive']['state'] == result['send']['state'] == 'disabled'


async def test_new_channel_unknown_and_changed_mailbox_are_explicit(channel):
    result = await mailflow.health_snapshot('owner')
    assert result['receive']['state'] == 'unknown' and len(result['errors']) == 2
    assert all('尚未完成连接验证' in message for message in result['errors'])
    assert '不代表已经发生邮件投递失败' in result['errors'][1]
    assert '无法确认是否有新回信' in result['errors'][0]
    mailflow.settings.return_value['mailbox'] = 'old-mailbox'
    result = await mailflow.health_snapshot('owner')
    assert result['receive']['stage'] == 'configuration'
    assert '账号已变化' in result['errors'][0]


async def test_stale_receiver_and_recovery_are_reported_independently(channel):
    channel.mark_connected()
    channel._health_success('receive', 'poll')
    channel._health_success('send', 'probe')
    channel._mail_health['receive']['last_success_at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    result = await mailflow.health_snapshot('owner')
    assert result['receive']['stage'] == 'stale' and result['errors']
    channel._health_stage('receive', 'authenticate')
    channel._health_failure('receive', TimeoutError())
    channel._health_success('receive', 'poll')
    result = await mailflow.health_snapshot('owner')
    assert result['receive']['consecutive_failures'] == 0 and not result['errors']
    assert result['receive']['last_error_at']  # Retain last outage after recovery.
    assert result['receive']['stage'] == 'poll'
    assert result['receive']['last_error_stage'] == 'authenticate'


async def test_recovered_receive_outage_does_not_keep_a_new_round_failed(channel):
    channel.mark_connected()
    channel._health_stage('receive', 'connect')
    channel._health_failure('receive', TimeoutError('PRIVATE'))
    channel._health_success('send', 'probe')
    failed = await mailflow.health_snapshot('owner')
    assert len(failed['errors']) == 1
    assert failed['receive']['last_error_stage'] == 'connect'
    channel._health_success('receive', 'poll')
    recovered = await mailflow.health_snapshot('owner')
    assert recovered['errors'] == []
    assert recovered['receive']['state'] == 'healthy'
    assert recovered['receive']['last_error_at'] == failed['receive']['last_error_at']
    assert recovered['receive']['last_success_stage'] == 'poll'
    assert recovered['send']['last_success_stage'] == 'probe'  # Not a delivery receipt.


def test_poll_backoff_is_bounded_and_success_resets_it(channel):
    delays = []
    for _ in range(9):
        channel._health_failure('receive', TimeoutError())
        delays.append(channel._poll_delay())
    assert delays[:4] == [30, 60, 120, 240]
    assert max(delays) == 300
    channel._health_success('receive', 'poll')
    assert channel._poll_delay() == 30


@pytest.mark.parametrize('failures', [0, 2])
async def test_idle_wait_is_normal_and_reconnect_updates_health(channel, monkeypatch, failures):
    abort = asyncio.Event()
    attempts = 0
    waited = []
    cfg = dict(channel._resolved)
    def probe():
        channel._health_success('send', 'probe')
    # The fetch is executed in a worker. Abort safely through the main loop.
    loop = asyncio.get_running_loop()
    def worker_fetch():
        nonlocal attempts
        attempts += 1
        channel._health_stage('receive', 'search')
        if attempts <= failures:
            raise TimeoutError('PRIVATE_CONNECTION_DETAIL')
        if attempts >= failures + 2:
            loop.call_soon_threadsafe(abort.set)
        return []
    def delay():
        waited.append(channel._mail_health['receive']['consecutive_failures'])
        return .001
    diagnostics = AsyncMock()
    monkeypatch.setattr(channel, '_test_connections', probe)
    monkeypatch.setattr(channel, '_fetch_new_messages', worker_fetch)
    monkeypatch.setattr(channel, '_poll_delay', delay)
    monkeypatch.setattr(mail_transport, 'diagnose_poll', diagnostics)
    await channel.start(cfg, AsyncMock(), abort)
    health = channel.health_snapshot()
    assert health['receive']['state'] == health['send']['state'] == 'healthy'
    assert health['receive']['consecutive_failures'] == 0
    assert diagnostics.await_count == failures
    assert waited[-1] == 0
    if failures:
        assert waited[:failures] == [1, 2]
        assert health['receive']['last_error_at']


def imap_fixture(channel, monkeypatch, search=('OK', [b'1 2'])):
    message = MIMEText('reply', 'plain', 'utf-8')
    message['From'] = 'person@example.com'
    message['Message-ID'] = '<reply@example.com>'
    imap = SimpleNamespace(logout=lambda: None, select=lambda *_: ('OK', [b'2']),
        xatom=lambda *_: ('OK', []), login=lambda *_: None,
        uid=lambda command, *args: search if command == 'search' else ('OK', [(b'1', message.as_bytes())]))
    monkeypatch.setattr(channel, '_connect_imap', lambda: imap)
    monkeypatch.setattr(mail_transport, 'poll_range', lambda *_: ('mailbox', '77', ['UID', '1:*'], 0))
    return imap


def test_imap_search_rejection_is_failure_not_zero_replies(channel, monkeypatch):
    imap_fixture(channel, monkeypatch, ('NO', [b'PRIVATE_ERROR']))
    with pytest.raises(RuntimeError, match='IMAP search failed'):
        channel._fetch_new_messages()
    assert channel.health_snapshot()['receive']['stage'] == 'search'
    assert channel._monitor_high is None


def test_batch_time_budget_checkpoints_only_fetched_uids(channel, monkeypatch):
    imap_fixture(channel, monkeypatch)
    ticks = iter([0, 1, 95])
    monkeypatch.setattr('flocks.channel.builtin.email.channel.monotonic', lambda: next(ticks))
    messages = channel._fetch_new_messages()
    assert [uid for uid, _ in messages] == [b'1']
    assert channel._monitor_high == 1


def test_exhausted_batch_before_first_uid_is_not_reported_as_success(channel, monkeypatch):
    imap_fixture(channel, monkeypatch)
    ticks = iter([0, 95])
    monkeypatch.setattr('flocks.channel.builtin.email.channel.monotonic', lambda: next(ticks))
    with pytest.raises(TimeoutError, match='batch budget'):
        channel._fetch_new_messages()
    assert channel._monitor_high is None


async def test_cursor_is_monotonic_and_survives_channel_restart(channel):
    await write('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?)',
        ('owner', 'scope', 'project', 'person@example.com', '', 1, 'revision',
         mail_transport.mailbox_key(channel._resolved), datetime.now(timezone.utc).isoformat()))
    imap = SimpleNamespace(response=lambda *_: ('UIDVALIDITY', [b'77']))
    poll = mail_transport.poll_range(channel._resolved, imap)
    mail_transport.checkpoint(poll, 10)
    mail_transport.checkpoint(poll, 5)
    restarted = EmailChannel()
    restarted._resolved = dict(channel._resolved)
    assert mail_transport.poll_range(restarted._resolved, imap)[2] == ['UID', '11:*']
    imap.response = lambda *_: ('UIDVALIDITY', [b'78'])
    reset = mail_transport.poll_range(restarted._resolved, imap)
    assert reset[2][0] == 'SINCE'
    mail_transport.checkpoint(reset, 2)
    assert (await rows('SELECT * FROM monitor_mail_cursors'))[0]['last_uid'] == 2


async def test_smtp_acceptance_does_not_change_imap_health(channel, monkeypatch):
    channel._health_stage('receive', 'fetch')
    channel._health_failure('receive', TimeoutError('PRIVATE'))
    monkeypatch.setattr(channel, '_send_email', lambda *_: '<stable@example.com>')
    result = await channel.send_text(OutboundContext(channel_id='email', to='person@example.com', text='fixture'))
    assert result.success
    assert channel.health_snapshot()['send']['state'] == 'healthy'
    assert channel.health_snapshot()['receive']['state'] == 'unavailable'


async def test_smtp_recovers_by_probe_without_resending_message(channel, monkeypatch):
    channel._health_success('receive', 'poll')
    channel._health_stage('send', 'authenticate')
    channel._health_failure('send', OSError('PRIVATE'))
    channel._mail_health['send']['next_retry_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    sent = []
    smtp = SimpleNamespace(send_message=sent.append, quit=lambda: None, close=lambda: None)
    monkeypatch.setattr(channel, '_connect_smtp', lambda: smtp)
    monkeypatch.setattr(channel, '_authenticate_smtp', lambda _: None)
    await channel._recover_smtp_if_due()
    assert not sent
    assert channel.health_snapshot()['send']['state'] == 'healthy'
    assert channel.health_snapshot()['send']['last_success_stage'] == 'probe'
    assert channel.health_snapshot()['send']['last_error_stage'] == 'authenticate'
    assert channel.health_snapshot()['receive']['last_success_stage'] == 'poll'


def test_restart_does_not_skip_mail_arriving_while_disconnected(channel, monkeypatch):
    imap_fixture(channel, monkeypatch)
    channel._resolved['skipExistingOnStart'] = True
    channel._monitor_validity = '77'
    channel._seen_uids.add(b'1')
    monkeypatch.setattr(channel, '_test_smtp_connection', lambda: None)
    channel._test_connections()
    # UID 2 is new since the saved cursor even though it already exists in IMAP.
    assert b'2' not in channel._seen_uids
    assert [uid for uid, _ in channel._fetch_new_messages()] == [b'2']


async def test_failed_dispatch_keeps_cursor_and_replays_only_unconsumed_message(channel, monkeypatch):
    imap_fixture(channel, monkeypatch)
    cfg = dict(channel._resolved)
    abort = asyncio.Event()
    dispatches = []
    checkpoints = []
    async def dispatch(message):
        uid = message.raw['uid']
        dispatches.append(uid)
        if uid == '2' and dispatches.count('2') == 1:
            raise OSError('PRIVATE_DISPATCH')
        if uid == '2':
            abort.set()
    monkeypatch.setattr(channel, '_test_connections', lambda: None)
    monkeypatch.setattr(channel, '_poll_delay', lambda: .001)
    monkeypatch.setattr(mail_transport, 'diagnose_poll', AsyncMock())
    monkeypatch.setattr(mail_transport, 'checkpoint', lambda poll, uid: checkpoints.append(uid))
    await channel.start(cfg, dispatch, abort)
    assert dispatches == ['1', '2', '2']
    assert checkpoints == [2]
    assert channel.health_snapshot()['receive']['state'] == 'healthy'
    assert channel.health_snapshot()['receive']['last_error_at']
