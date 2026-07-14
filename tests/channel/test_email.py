from __future__ import annotations

import asyncio
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock

import pytest

from flocks.channel.base import ChatType, OutboundContext
from flocks.channel.builtin.email.channel import EmailChannel, _send_imap_id
from flocks.channel.builtin.email.config import parse_allowed_senders, resolved_config
from flocks.channel.builtin.email.inbound import (
    build_inbound_message,
    decode_header_value,
    extract_text_body,
    strip_html,
    verify_sender_authentication,
)


def _raw_email(
    *,
    sender: str = "User <user@example.com>",
    subject: str = "Hello",
    body: str = "Test body",
    auth_results: str | None = None,
) -> bytes:
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uuid.uuid4().hex[:8]}@example.com>"
    if auth_results:
        msg["Authentication-Results"] = auth_results
    return msg.as_bytes()


def test_email_channel_meta_and_validate_config() -> None:
    plugin = EmailChannel()

    assert plugin.meta().id == "email"
    assert "mail" in plugin.meta().aliases
    assert ChatType.DIRECT in plugin.capabilities().chat_types
    assert "address" in (plugin.validate_config({}) or "")

    error = plugin.validate_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
    })
    assert error and "allowFrom" in error

    assert plugin.validate_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowFrom": ["user@example.com"],
        "authservId": "mx.example.com",
    }) is None

    error = plugin.validate_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowFrom": ["user@example.com"],
    })
    assert error and "authservId" in error


def test_email_channel_registered_as_builtin() -> None:
    from flocks.channel.registry import ChannelRegistry

    registry = ChannelRegistry()
    registry._register_builtin_channels()

    assert registry.get("email") is not None
    assert registry.get("mail") is registry.get("email")


def test_config_normalizes_allowed_senders() -> None:
    cfg = resolved_config({
        "address": "Agent@Example.COM",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowFrom": "User <USER@example.com>, second@example.com",
    })

    assert cfg["address"] == "agent@example.com"
    assert parse_allowed_senders(cfg) == {"user@example.com", "second@example.com"}


def test_email_parsing_helpers() -> None:
    assert decode_header_value("=?utf-8?B?TWVyaGFiYQ==?=") == "Merhaba"
    assert strip_html("<p>Hello<br>world &amp; team</p>") == "Hello\nworld & team"

    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText("<p>HTML</p>", "html", "utf-8"))
    msg.attach(MIMEText("Plain", "plain", "utf-8"))
    assert extract_text_body(msg) == "Plain"


def test_build_inbound_message_includes_subject_and_attachment_names() -> None:
    msg = MIMEMultipart()
    msg["From"] = "User <user@example.com>"
    msg["Subject"] = "Investigate"
    msg["Message-ID"] = "<m1@example.com>"
    msg.attach(MIMEText("Please check this.", "plain", "utf-8"))

    attachment = MIMEText("payload", "plain", "utf-8")
    attachment.add_header("Content-Disposition", "attachment", filename="report.txt")
    msg.attach(attachment)

    parsed = build_inbound_message(msg, uid="1", account_id="default", skip_attachments=False)

    assert parsed is not None
    inbound = parsed.inbound
    assert inbound.channel_id == "email"
    assert inbound.chat_type == ChatType.DIRECT
    assert inbound.sender_id == "user@example.com"
    assert inbound.chat_id == "user@example.com"
    assert "[Subject: Investigate]" in inbound.text
    assert "report.txt" in inbound.text


def test_sender_authentication_accepts_dmarc_pass() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = "mx.example.com; dmarc=pass header.from=example.com"

    assert verify_sender_authentication(
        msg,
        "user@example.com",
        authserv_id="mx.example.com",
    )[0] is True


def test_sender_authentication_rejects_unaligned_dmarc_pass() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = "mx.example.com; dmarc=pass header.from=evil.org"

    assert (
        verify_sender_authentication(
            msg,
            "user@example.com",
            authserv_id="mx.example.com",
        )[0]
        is False
    )


def test_sender_authentication_accepts_spf_pass_when_helo_aligned() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = (
        "mx.example.com; spf=pass smtp.helo=mail.example.com smtp.mailfrom=mail.example.com "
        "header.from=example.com"
    )

    assert verify_sender_authentication(
        msg,
        "user@example.com",
        authserv_id="mx.example.com",
    )[0] is True


def test_sender_authentication_rejects_spf_fail_without_helo_alignment() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = (
        "mx.example.com; spf=pass smtp.helo=malicious.net smtp.mailfrom=evil.net "
        "header.from=example.com"
    )

    assert (
        verify_sender_authentication(
            msg,
            "user@example.com",
            authserv_id="mx.example.com",
        )[0]
        is False
    )


def test_sender_authentication_rejects_dmarc_when_visible_from_misaligned() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = "mx.example.com; dmarc=pass header.from=evil.org"

    assert (
        verify_sender_authentication(
            msg,
            "user@example.com",
            authserv_id="mx.example.com",
        )[0]
        is False
    )


def test_sender_authentication_uses_only_trusted_authserv_id() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = "attacker.example; dmarc=pass header.from=example.com"
    msg["Authentication-Results"] = "mx.example.com; dmarc=fail header.from=example.com"

    assert verify_sender_authentication(
        msg,
        "user@example.com",
        authserv_id="mx.example.com",
    )[0] is False

    trusted_pass = MIMEText("hi", "plain", "utf-8")
    trusted_pass["Authentication-Results"] = "attacker.example; dmarc=fail header.from=example.com"
    trusted_pass["Authentication-Results"] = "mx.example.com; dmarc=pass header.from=example.com"

    assert verify_sender_authentication(
        trusted_pass,
        "user@example.com",
        authserv_id="mx.example.com",
    )[0] is True


def test_sender_authentication_rejects_without_authserv_id() -> None:
    msg = MIMEText("hi", "plain", "utf-8")
    msg["Authentication-Results"] = "mx.example.com; dmarc=pass header.from=example.com"

    assert verify_sender_authentication(msg, "user@example.com")[0] is False


def test_build_resolved_defaults_apply_ssl_and_starttls_modes() -> None:
    cfg = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "imapPort": 143,
        "smtpPort": 465,
    })

    assert cfg["imapSecurity"] == "starttls"
    assert cfg["smtpSecurity"] == "ssl"


def test_custom_ports_require_explicit_security_mode() -> None:
    plugin = EmailChannel()
    assert (
        plugin.validate_config({
            "address": "agent@example.com",
            "password": "pw",
            "imapHost": "imap.example.com",
            "smtpHost": "smtp.example.com",
            "imapPort": 1143,
            "smtpPort": 1993,
        })
        == "IMAP security must be one of: ssl, starttls, insecure"
    )


def test_validate_insecure_requires_explicit_risk_confirmation() -> None:
    plugin = EmailChannel()
    assert plugin.validate_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "imapPort": 1143,
        "smtpPort": 1993,
        "imapSecurity": "insecure",
        "smtpSecurity": "insecure",
    }) == "IMAP insecure mode requires allowInsecureConnections=true"


def test_parse_and_authorize_blocks_unallowlisted_sender() -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowFrom": ["allowed@example.com"],
    })
    msg = MIMEText("hello", "plain", "utf-8")
    msg["From"] = "user@example.com"
    msg["Subject"] = "Hello"
    msg["Message-ID"] = "<m1@example.com>"

    assert plugin._parse_and_authorize(msg, "1") is None


def test_parse_and_authorize_requires_authenticated_sender_for_allowlist() -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowFrom": ["user@example.com"],
        "authservId": "mx.example.com",
    })
    msg = MIMEText("hello", "plain", "utf-8")
    msg["From"] = "user@example.com"
    msg["Subject"] = "Hello"
    msg["Message-ID"] = "<m1@example.com>"

    assert plugin._parse_and_authorize(msg, "1") is None

    msg["Authentication-Results"] = "mx.example.com; dmarc=pass header.from=example.com"
    inbound = plugin._parse_and_authorize(msg, "2")
    assert inbound is not None
    assert inbound.sender_id == "user@example.com"


def test_fetch_new_messages_only_marks_uid_after_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })

    uid = b"101"
    raw_msg = _raw_email(sender="user@example.com", subject="Check", body="ping",)

    class FakeIMAP:
        def __init__(self):
            self.calls = 0

        def uid(self, command, *args):
            self.calls += 1
            if self.calls == 1:
                return ("OK", [uid])
            return ("OK", [(uid, raw_msg)])

        def login(self, _address, _password):
            return None

        def select(self, _folder):
            return None

        def logout(self):
            return None

    fake_imap = FakeIMAP()
    monkeypatch.setattr(plugin, "_connect_imap", lambda: fake_imap)

    messages = plugin._fetch_new_messages()
    assert len(messages) == 1
    assert uid in plugin._seen_uids

    fake_imap.calls = 0
    messages = plugin._fetch_new_messages()
    assert messages == []
    assert fake_imap.calls == 1


def test_fetch_new_messages_does_not_mark_uid_on_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })

    uid = b"202"

    class FakeIMAP:
        def uid(self, command, *args):
            if command == "search":
                return ("OK", [uid])
            return ("OK", [(uid, b"not bytes")])

        def login(self, _address, _password):
            return None

        def select(self, _folder):
            return None

        def logout(self):
            return None

    monkeypatch.setattr(plugin, "_connect_imap", lambda: FakeIMAP())
    # Force a decode/parsing failure by patching the parser to raise.
    monkeypatch.setattr(
        "flocks.channel.builtin.email.channel.email_lib.message_from_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("parse failure")),
    )

    messages = plugin._fetch_new_messages()
    assert messages == []
    assert uid not in plugin._seen_uids


def test_send_email_threads_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })
    plugin._thread_context["user@example.com"] = {
        "subject": "Question",
        "message_id": "<orig@example.com>",
        "references": "<root@example.com>",
    }

    sent = {}

    class FakeSMTP:
        def login(self, address, password):
            sent["login"] = (address, password)

        def send_message(self, msg):
            sent["msg"] = msg

        def quit(self):
            sent["quit"] = True

    monkeypatch.setattr(plugin, "_connect_smtp", lambda: FakeSMTP())

    message_id = plugin._send_email("user@example.com", "reply", None, None, None)

    assert message_id.startswith("<flocks-")
    msg = sent["msg"]
    assert msg["Subject"] == "Re: Question"
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert msg["References"] == "<root@example.com>"


@pytest.mark.asyncio
async def test_start_marks_connected_after_successful_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin.reset_status("email")
    abort = asyncio.Event()

    monkeypatch.setattr(plugin, "_test_connections", lambda: None)

    def fake_fetch():
        abort.set()
        return []

    monkeypatch.setattr(plugin, "_fetch_new_messages", fake_fetch)

    await plugin.start(
        {
            "address": "agent@example.com",
            "password": "pw",
            "imapHost": "imap.example.com",
            "smtpHost": "smtp.example.com",
            "allowAll": True,
            "pollIntervalSeconds": 5,
        },
        lambda message: None,
        abort,
    )

    assert plugin.status.connected is True


@pytest.mark.asyncio
async def test_start_restores_connected_status_after_transient_poll_error(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin.reset_status("email")
    abort = asyncio.Event()
    calls = 0

    monkeypatch.setattr(plugin, "_test_connections", lambda: None)

    def fake_fetch():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary IMAP failure")
        abort.set()
        return []

    monkeypatch.setattr(plugin, "_fetch_new_messages", fake_fetch)

    await plugin.start(
        {
            "address": "agent@example.com",
            "password": "pw",
            "imapHost": "imap.example.com",
            "smtpHost": "smtp.example.com",
            "allowAll": True,
            "pollIntervalSeconds": 0.01,
        },
        lambda message: None,
        abort,
    )

    assert plugin.status.connected is True
    assert plugin.status.error_count == 1


def test_send_email_prefers_requested_thread_context(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })
    plugin._thread_context["user@example.com"] = {
        "subject": "Second question",
        "message_id": "<second@example.com>",
        "references": "<second@example.com>",
    }
    plugin._thread_context["<first@example.com>"] = {
        "subject": "First question",
        "message_id": "<first@example.com>",
        "references": "<root@example.com> <first@example.com>",
    }

    sent = {}

    class FakeSMTP:
        def login(self, address, password):
            pass

        def send_message(self, msg):
            sent["msg"] = msg

        def quit(self):
            pass

    monkeypatch.setattr(plugin, "_connect_smtp", lambda: FakeSMTP())

    plugin._send_email(
        "user@example.com",
        "reply",
        "<first@example.com>",
        "<first@example.com>",
        None,
    )

    msg = sent["msg"]
    assert msg["Subject"] == "Re: First question"
    assert msg["In-Reply-To"] == "<first@example.com>"
    assert msg["References"] == "<first@example.com>"


def test_email_password_is_extracted_to_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.security.channel_secrets import extract_channel_secrets

    class FakeSecretManager:
        def __init__(self) -> None:
            self.values = {}

        def set(self, key: str, value: str) -> None:
            self.values[key] = value

    fake = FakeSecretManager()
    monkeypatch.setattr("flocks.security.secrets.get_secret_manager", lambda: fake)

    result = extract_channel_secrets({
        "email": {
            "enabled": True,
            "address": "agent@example.com",
            "password": "plain-password",
        }
    })

    assert result["email"]["password"] == "{secret:channel_email_password}"
    assert fake.values["channel_email_password"] == "plain-password"


@pytest.mark.asyncio
async def test_send_text_rejects_invalid_target() -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })

    result = await plugin.send_text(
        OutboundContext(channel_id="email", to="not-an-email", text="hello")
    )

    assert result.success is False
    assert "valid recipient" in (result.error or "")


def test_fetch_new_messages_skips_malformed_imap_response(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })

    fetch_calls = iter([
        ("OK", [None]),
        ("OK", [(b"2 (RFC822 {123}", _raw_email(auth_results="mx.example.com; dmarc=pass header.from=example.com"))]),
    ])

    fake_imap = MagicMock()
    fake_imap.uid.side_effect = lambda command, *args: (
        ("OK", [b"1 2"]) if command == "search" else next(fetch_calls)
    )

    monkeypatch.setattr(
        "flocks.channel.builtin.email.channel.imaplib.IMAP4_SSL",
        lambda *args, **kwargs: fake_imap,
    )

    messages = plugin._fetch_new_messages()

    assert len(messages) == 1
    assert messages[0].sender_id == "user@example.com"


def test_connect_smtp_starttls_fails_when_not_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            self.started = False

        def starttls(self, context=None):
            self.started = True
            return 421, b"not-supported"

        def close(self):
            pass

    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "smtpSecurity": "starttls",
        "smtpPort": 587,
    })
    fake = FakeSMTP()
    monkeypatch.setattr("flocks.channel.builtin.email.channel.smtplib.SMTP", lambda *args, **kwargs: fake)

    with pytest.raises(RuntimeError, match="SMTP STARTTLS not available"):
        plugin._connect_smtp()


def test_connect_smtp_starttls_exception_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def starttls(self, context=None):
            raise RuntimeError("tls failed")

        def close(self):
            self.closed = True

    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "smtpSecurity": "starttls",
        "smtpPort": 587,
    })
    fake = FakeSMTP()
    monkeypatch.setattr("flocks.channel.builtin.email.channel.smtplib.SMTP", lambda *args, **kwargs: fake)

    with pytest.raises(RuntimeError, match="tls failed"):
        plugin._connect_smtp()
    assert fake.closed is True


def test_connect_smtp_falls_back_to_ipv4_on_connection_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as socket_module

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            self.started = False

        def starttls(self, context=None):
            self.started = True
            return 220, b"ready"

    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "smtpSecurity": "starttls",
        "smtpPort": 587,
    })
    fake = FakeSMTP()
    monkeypatch.setattr(
        "flocks.channel.builtin.email.channel.smtplib.SMTP",
        lambda *args, **kwargs: (_ for _ in ()).throw(socket_module.timeout("timed out")),
    )
    monkeypatch.setattr("flocks.channel.builtin.email.channel._IPv4SMTP", lambda *args, **kwargs: fake)

    assert plugin._connect_smtp() is fake
    assert fake.started is True


def test_connect_smtp_tls_error_does_not_retry_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "smtpSecurity": "ssl",
        "smtpPort": 465,
    })
    monkeypatch.setattr(
        "flocks.channel.builtin.email.channel.smtplib.SMTP_SSL",
        lambda *args, **kwargs: (_ for _ in ()).throw(__import__("ssl").SSLError("cert verify failed")),
    )
    monkeypatch.setattr(
        "flocks.channel.builtin.email.channel._IPv4SMTP_SSL",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not retry")),
    )

    with pytest.raises(__import__("ssl").SSLError):
        plugin._connect_smtp()


def test_connect_imap_insecure_does_not_call_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            self.started = False

        def starttls(self, ssl_context=None):
            self.started = True
            return ("OK", b"")

        def close(self):
            pass

    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "imapSecurity": "insecure",
        "imapPort": 1143,
        "allowInsecureConnections": True,
    })
    plugin._resolved["smtpSecurity"] = "starttls"
    plugin._resolved["smtpPort"] = 587
    fake = FakeIMAP()
    monkeypatch.setattr("flocks.channel.builtin.email.channel.imaplib.IMAP4", lambda *args, **kwargs: fake)

    conn = plugin._connect_imap()
    assert conn is fake
    assert fake.started is False


def test_connect_imap_starttls_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def starttls(self, ssl_context=None):
            return ("NO", b"not supported")

        def close(self):
            pass

    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "imapSecurity": "starttls",
        "imapPort": 143,
        "smtpSecurity": "starttls",
        "smtpPort": 587,
    })
    monkeypatch.setattr("flocks.channel.builtin.email.channel.imaplib.IMAP4", FakeIMAP)

    with pytest.raises(RuntimeError, match="IMAP STARTTLS not available"):
        plugin._connect_imap()


def test_connect_imap_starttls_bytes_ok_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeIMAP:
        def __init__(self, *args, **kwargs):
            pass

        def starttls(self, ssl_context=None):
            return (b"OK", b"ready")

        def close(self):
            pass

    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "imapSecurity": "starttls",
        "imapPort": 143,
        "smtpSecurity": "starttls",
        "smtpPort": 587,
    })
    fake = FakeIMAP()
    monkeypatch.setattr("flocks.channel.builtin.email.channel.imaplib.IMAP4", lambda *args, **kwargs: fake)

    assert plugin._connect_imap() is fake


def test_send_imap_id_is_best_effort() -> None:
    fake_imap = MagicMock()
    fake_imap.xatom.side_effect = RuntimeError("BAD command unknown")

    _send_imap_id(fake_imap)

    fake_imap.xatom.assert_called_once()


def test_fetch_new_messages_sends_imap_id_after_login(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = EmailChannel()
    plugin._resolved = resolved_config({
        "address": "agent@example.com",
        "password": "pw",
        "imapHost": "imap.example.com",
        "smtpHost": "smtp.example.com",
        "allowAll": True,
    })

    fake_imap = MagicMock()
    fake_imap.uid.return_value = ("OK", [b""])
    seen = []

    monkeypatch.setattr(plugin, "_connect_imap", lambda: fake_imap)
    monkeypatch.setattr("flocks.channel.builtin.email.channel._send_imap_id", lambda imap: seen.append(imap))

    assert plugin._fetch_new_messages() == []
    assert seen == [fake_imap]


def test_build_inbound_message_sanitizes_attachment_filename() -> None:
    msg = MIMEMultipart()
    msg["From"] = "User <user@example.com>"
    msg["Subject"] = "Case"
    msg["Message-ID"] = "<m1@example.com>"
    msg.attach(MIMEText("See attachment", "plain", "utf-8"))

    attachment = MIMEText("payload", "plain", "utf-8")
    attachment.add_header("Content-Disposition", 'attachment; filename="../../report.exe"')
    msg.attach(attachment)

    parsed = build_inbound_message(msg, uid="1", account_id="default", skip_attachments=False)

    assert parsed is not None
    assert ".._" not in parsed.inbound.text
    assert "../" not in parsed.inbound.text
    assert "report" in parsed.inbound.text
