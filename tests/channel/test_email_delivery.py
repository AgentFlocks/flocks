"""Delivery certainty through the real MIME/smtplib/outbound stack, without sockets."""
import asyncio
import smtplib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.channel.base import DeliveryResult, OutboundContext
from flocks.channel.builtin.email.channel import EmailChannel
from flocks.channel.builtin.email.config import resolved_config
from flocks.channel.outbound.deliver import OutboundDelivery
from flocks.hooks.pipeline import HookPipeline
from flocks.monitoring import mail_transport


class ScriptedSMTP(smtplib.SMTP):
    """Keep send_message/sendmail/data real; substitute only protocol I/O."""

    def __init__(self, failure=None, cleanup_failure=False):
        super().__init__()  # No host means no network connection.
        self.helo_resp = b"ready"
        self.failure = failure
        self.cleanup_failure = cleanup_failure
        self.commands = []
        self.payloads = []
        self.replies = 0
        self.close_count = 0

    def mail(self, sender, options=()):
        self.commands.append("mail")
        return (550 if self.failure == "sender" else 250), b"sender result"

    def rcpt(self, recipient, options=()):
        self.commands.append("rcpt")
        return (550 if self.failure == "recipient" else 250), b"recipient result"

    def putcmd(self, command, args=""):
        self.commands.append(command)

    def getreply(self):
        self.replies += 1
        if self.replies == 1:
            return (554 if self.failure == "data_start" else 354), b"data result"
        if self.failure == "data_timeout":
            raise TimeoutError("The server may already have accepted DATA")
        if self.failure == "data_disconnect":
            raise smtplib.SMTPServerDisconnected("Connection lost awaiting acceptance")
        return (554 if self.failure == "data_rejected" else 250), b"data result"

    def send(self, payload):
        self.payloads.append(payload)

    def _rset(self):
        pass

    def quit(self):
        self.commands.append("quit")
        if self.cleanup_failure:
            raise smtplib.SMTPServerDisconnected("Disconnected after acceptance")

    def close(self):
        self.close_count += 1
        if self.cleanup_failure:
            raise OSError("Cleanup failed")


@pytest.fixture
def outbound(monkeypatch):
    channel = EmailChannel()
    channel._resolved = resolved_config({"address": "agent@example.com", "password": "fixture",
        "imapHost": "imap.example.com", "smtpHost": "smtp.example.com", "allowAll": True})
    channel.mark_connected()
    monkeypatch.setattr(mail_transport.default_registry, "get", lambda _: channel)
    monkeypatch.setattr(OutboundDelivery, "_rate_limiters", {})
    monkeypatch.setattr(OutboundDelivery, "_publish_sent_event", AsyncMock())
    monkeypatch.setattr(HookPipeline, "run_channel_outbound_before", AsyncMock(return_value=SimpleNamespace(output={})))
    monkeypatch.setattr(HookPipeline, "run_channel_outbound_after", AsyncMock())
    monkeypatch.setattr(channel, "_authenticate_smtp", lambda _: None)
    notice = {"recipient": "person@example.com", "body": "One incident", "subject": "事件 A",
              "message_id": "<stable@example.com>", "mailbox": mail_transport.mailbox_key(channel._resolved)}
    return channel, notice


@pytest.mark.parametrize("failure", ["connect", "auth", "sender", "recipient", "data_start", "data_rejected"])
async def test_proven_rejection_exposes_safe_manual_retry(outbound, monkeypatch, failure):
    channel, notice = outbound
    smtp = ScriptedSMTP(failure)

    def connect():
        if failure == "connect":
            raise TimeoutError("No SMTP session established")
        return smtp

    def authenticate(_):
        if failure == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"Authentication rejected")

    monkeypatch.setattr(channel, "_connect_smtp", connect)
    monkeypatch.setattr(channel, "_authenticate_smtp", authenticate)
    with pytest.raises(mail_transport.TransportNotReady):
        await mail_transport.send(notice, "session")
    assert len(smtp.payloads) == (1 if failure == "data_rejected" else 0)


@pytest.mark.parametrize("failure", ["data_timeout", "data_disconnect"])
async def test_lost_acceptance_stays_unknown_and_is_not_retried(outbound, monkeypatch, failure):
    channel, notice = outbound
    smtp = ScriptedSMTP(failure)
    monkeypatch.setattr(channel, "_connect_smtp", lambda: smtp)
    assert await mail_transport.send(notice, "session") is False
    assert len(smtp.payloads) == 1
    # The ordinary dispatcher must not retry an uncertain email either.
    smtp = ScriptedSMTP(failure)
    context = OutboundContext(channel_id="email", to=notice["recipient"], text=notice["body"])
    results = await OutboundDelivery.deliver(context, max_retries=3)
    assert len(smtp.payloads) == 1
    assert results[0].delivery_state == "unknown" and not results[0].retryable


async def test_cleanup_failure_preserves_accepted_delivery(outbound, monkeypatch):
    channel, notice = outbound
    smtp = ScriptedSMTP(cleanup_failure=True)
    monkeypatch.setattr(channel, "_connect_smtp", lambda: smtp)
    assert await mail_transport.send(notice, "session") is True
    assert len(smtp.payloads) == 1 and smtp.close_count == 1
    assert b"Message-ID: <stable@example.com>" in smtp.payloads[0]
    assert channel.health_snapshot()["send"]["state"] == "healthy"


@pytest.mark.parametrize("mode", ["blocked", "hook_error", "split", "empty"])
async def test_pre_delivery_failure_is_explicitly_not_sent(outbound, monkeypatch, mode):
    channel, notice = outbound
    send = AsyncMock()
    monkeypatch.setattr(channel, "send_text", send)
    if mode == "blocked":
        monkeypatch.setattr(HookPipeline, "run_channel_outbound_before",
                            AsyncMock(return_value=SimpleNamespace(output={"blocked": True})))
    elif mode == "hook_error":
        monkeypatch.setattr(HookPipeline, "run_channel_outbound_before", AsyncMock(side_effect=RuntimeError("fixture")))
    else:
        monkeypatch.setattr(channel, "chunk_text", lambda *_: ["first", "second"] if mode == "split" else [])
    with pytest.raises(mail_transport.TransportNotReady):
        await mail_transport.send(notice, "session")
    send.assert_not_called()


async def test_legacy_failure_text_never_grants_retry_permission(outbound, monkeypatch):
    _, notice = outbound
    result = DeliveryResult(channel_id="email", message_id="", success=False,
                            error="SMTP connect failed; message not sent")
    monkeypatch.setattr(OutboundDelivery, "deliver", AsyncMock(return_value=[result]))
    assert result.delivery_state == "unknown"
    assert await mail_transport.send(notice, "session") is False


async def test_invalid_recipient_is_not_sent_and_unknown_exception_stays_unknown(outbound, monkeypatch):
    channel, _ = outbound
    result = await channel.send_text(OutboundContext(channel_id="email", to="not-an-email"))
    assert result.delivery_state == "not_sent" and not result.success
    monkeypatch.setattr(channel, "_send_email", lambda *_: (_ for _ in ()).throw(RuntimeError("not sent")))
    result = await channel.send_text(OutboundContext(channel_id="email", to="person@example.com"))
    assert result.delivery_state == "unknown" and not result.retryable


async def start_with_failed_imap(channel, monkeypatch):
    """Exercise the actual polling failure/mark_disconnected lifecycle."""
    failed = asyncio.Event()
    abort = asyncio.Event()
    monkeypatch.setattr(channel, '_test_connections', lambda: None)
    monkeypatch.setattr(channel, '_fetch_new_messages', lambda: (_ for _ in ()).throw(TimeoutError('IMAP unavailable')))
    monkeypatch.setattr(mail_transport, 'diagnose_poll', AsyncMock(side_effect=lambda *_: failed.set()))
    config = {**channel._resolved, 'enabled': True}
    task = asyncio.create_task(channel.start(config, AsyncMock(), abort))
    await asyncio.wait_for(failed.wait(), timeout=2)
    return task, abort


async def test_enabled_smtp_can_send_during_imap_failure_with_original_binding(outbound, monkeypatch):
    channel, notice = outbound
    smtp = ScriptedSMTP()
    monkeypatch.setattr(channel, '_connect_smtp', lambda: smtp)
    task, abort = await start_with_failed_imap(channel, monkeypatch)
    try:
        assert not channel.status.connected and channel.send_ready
        # Saving new configuration still requires the old connected check.
        with pytest.raises(ValueError, match='连接'):
            mail_transport.transport(notice['recipient'])
        assert await mail_transport.send(notice, 'session') is True
        assert len(smtp.payloads) == 1
        assert b'To: person@example.com' in smtp.payloads[0]
        assert b'Message-ID: <stable@example.com>' in smtp.payloads[0]
        assert channel.health_snapshot()['receive']['state'] == 'unavailable'
        assert channel.health_snapshot()['send']['state'] == 'healthy'
    finally:
        abort.set()
        await task


@pytest.mark.parametrize('change', ['stop', 'abort', 'disabled_config', 'mailbox', 'recipient'])
async def test_send_readiness_does_not_bypass_shutdown_or_original_binding(outbound, monkeypatch, change):
    channel, notice = outbound
    smtp = ScriptedSMTP()
    monkeypatch.setattr(channel, '_connect_smtp', lambda: smtp)
    task, abort = await start_with_failed_imap(channel, monkeypatch)
    try:
        if change == 'stop':
            await channel.stop()
            channel.mark_connected()  # Late IMAP completion cannot re-enable SMTP.
        elif change == 'abort':
            abort.set()  # Gateway revokes sends before waiting for stop().
        elif change == 'disabled_config':
            channel._config['enabled'] = False
        elif change == 'mailbox':
            channel._resolved['address'] = 'other@example.com'
        else:
            channel._resolved.update(allowAll=False, allowFrom=['other@example.com'])
        with pytest.raises(mail_transport.TransportNotReady):
            await mail_transport.send(notice, 'session')
        assert not smtp.payloads
    finally:
        abort.set()
        await task


async def test_stop_during_outbound_hook_prevents_smtp_send(outbound, monkeypatch):
    channel, notice = outbound
    smtp = ScriptedSMTP()
    monkeypatch.setattr(channel, '_connect_smtp', lambda: smtp)

    async def stop_before_send(_):
        await channel.stop()
        return SimpleNamespace(output={})

    monkeypatch.setattr(HookPipeline, 'run_channel_outbound_before', stop_before_send)
    with pytest.raises(mail_transport.TransportNotReady):
        await mail_transport.send(notice, 'session')
    assert not smtp.payloads


async def test_monitor_notification_delivers_html_and_plain_alternative_once(outbound, monkeypatch):
    import json
    from email import policy
    from email.parser import BytesParser
    channel, notice = outbound
    html = '<html><body><h1>已确认风险</h1><p>请核查异常外联。</p></body></html>'
    notice['event'] = json.dumps({'mail_presentation': {'version': 1, 'html_body': html}})
    smtp = ScriptedSMTP()
    monkeypatch.setattr(channel, '_connect_smtp', lambda: smtp)
    assert await mail_transport.send(notice, 'session') is True
    assert len(smtp.payloads) == 1
    message = BytesParser(policy=policy.default).parsebytes(smtp.payloads[0])
    assert message.get_content_type() == 'multipart/alternative'
    assert [part.get_content_type() for part in message.iter_parts()] == ['text/plain', 'text/html']
    assert message.get_body(preferencelist=('plain',)).get_content().strip() == notice['body']
    assert message.get_body(preferencelist=('html',)).get_content().strip() == html
    assert str(message['Message-ID']) == notice['message_id']
    assert str(message['Subject']) == notice['subject']


async def test_outbound_hook_text_change_drops_stale_html(outbound, monkeypatch):
    import json
    from email import policy
    from email.parser import BytesParser
    channel, notice = outbound
    notice['event'] = json.dumps({'mail_presentation': {'version': 1, 'html_body': '<p>old body</p>'}})
    monkeypatch.setattr(HookPipeline, 'run_channel_outbound_before',
                        AsyncMock(return_value=SimpleNamespace(output={'text': 'approved replacement'})))
    smtp = ScriptedSMTP()
    monkeypatch.setattr(channel, '_connect_smtp', lambda: smtp)
    assert await mail_transport.send(notice, 'session') is True
    message = BytesParser(policy=policy.default).parsebytes(smtp.payloads[0])
    assert message.get_body(preferencelist=('plain',)).get_content().strip() == 'approved replacement'
    assert message.get_body(preferencelist=('html',)) is None


def test_html_alternative_and_attachment_have_separate_mime_parts(outbound, tmp_path):
    channel, notice = outbound
    attachment = tmp_path / 'evidence.txt'
    attachment.write_text('fixture')
    message = channel._prepare_email(notice['recipient'], notice['body'], None, None, attachment,
                                     notice['subject'], notice['message_id'], True, '<p>已确认风险</p>')
    assert message.get_content_type() == 'multipart/mixed'
    parts = message.get_payload()
    assert len(parts) == 2 and parts[0].get_content_type() == 'multipart/alternative'
    assert parts[1].get_content_disposition() == 'attachment'
    assert [part.get_content_type() for part in parts[0].get_payload()] == ['text/plain', 'text/html']
