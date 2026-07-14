"""Email ChannelPlugin using IMAP for inbound and SMTP for outbound."""

from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import smtplib
import socket
import ssl
import uuid
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.channel.media_filename import sanitize_filename
from flocks.utils.log import Log

from .config import (
    is_valid_email,
    normalize_email_address,
    parse_allowed_senders,
    resolved_config,
)
from .inbound import (
    build_inbound_message,
    is_automated_sender,
    verify_sender_authentication,
)

log = Log.create(service="channel.email")

SMTP_CONNECT_TIMEOUT = 30


def _create_ipv4_connection(
    host: str,
    port: int,
    timeout: float,
    source_address: Any = None,
) -> socket.socket:
    """Create an SMTP socket using IPv4 addresses only."""
    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(
        host,
        port,
        socket.AF_INET,
        socket.SOCK_STREAM,
    ):
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"No IPv4 address found for {host}:{port}")


class _IPv4SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):  # type: ignore[override]
        return _create_ipv4_connection(
            host,
            port,
            timeout,
            source_address=self.source_address,
        )


class _IPv4SMTP_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):  # type: ignore[override]
        raw_sock = _create_ipv4_connection(
            host,
            port,
            timeout,
            source_address=self.source_address,
        )
        return self.context.wrap_socket(
            raw_sock,
            server_hostname=getattr(self, "_host", host),
        )


def _send_imap_id(imap: imaplib.IMAP4) -> None:
    """Best-effort RFC 2971 IMAP ID command for providers such as NetEase."""
    try:
        imap.xatom(
            "ID",
            '("name" "flocks" "version" "0" "vendor" "Flocks" '
            '"support-email" "noreply@flocks.local")',
        )
    except Exception as exc:
        log.debug("email.imap.id_unsupported", {"error": str(exc)})


class EmailChannel(ChannelPlugin):
    """Single-mailbox Email channel."""

    def __init__(self) -> None:
        super().__init__()
        self._resolved: dict[str, Any] = {}
        self._seen_uids: set[bytes] = set()
        self._seen_uids_max = 5000
        self._thread_context: dict[str, dict[str, str]] = {}

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="email",
            label="Email",
            aliases=["mail", "imap", "smtp"],
            order=60,
        )

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            media=True,
            threads=True,
            reactions=False,
            edit=False,
            rich_text=False,
        )

    @property
    def text_chunk_limit(self) -> int:
        return 50_000

    @property
    def rate_limit(self) -> tuple[float, int]:
        return (0.2, 2)

    def validate_config(self, config: dict) -> Optional[str]:
        cfg = resolved_config(config)
        missing = [
            name
            for name in ("address", "password", "imapHost", "smtpHost")
            if not cfg.get(name)
        ]
        if missing:
            return "Missing required config: " + ", ".join(missing)
        if not is_valid_email(cfg["address"]):
            return "Invalid email address"
        if cfg["imapPort"] <= 0 or cfg["smtpPort"] <= 0:
            return "IMAP/SMTP ports must be positive integers"
        if cfg["imapSecurity"] not in {"ssl", "starttls", "insecure"}:
            return "IMAP security must be one of: ssl, starttls, insecure"
        if cfg["smtpSecurity"] not in {"ssl", "starttls", "insecure"}:
            return "SMTP security must be one of: ssl, starttls, insecure"
        if cfg["imapSecurity"] == "insecure" and not cfg["allowInsecureConnections"]:
            return "IMAP insecure mode requires allowInsecureConnections=true"
        if cfg["smtpSecurity"] == "insecure" and not cfg["allowInsecureConnections"]:
            return "SMTP insecure mode requires allowInsecureConnections=true"
        allowed = parse_allowed_senders(cfg)
        if not cfg["allowAll"] and not allowed:
            return "Email channel requires allowFrom or allowAll=true"
        if (
            not cfg["allowAll"]
            and allowed
            and cfg["requireAuthenticatedSender"]
            and not cfg["authservId"]
        ):
            return "Email channel requires authservId when requireAuthenticatedSender=true and allowFrom is configured"
        return None

    def normalize_target(self, raw: str) -> Optional[str]:
        target = normalize_email_address(raw)
        return target if is_valid_email(target) else None

    def target_hint(self) -> str:
        return "user@example.com"

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        self._config = config
        self._resolved = resolved_config(config)
        self._on_message = on_message

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._test_connections)
        self.mark_connected()

        abort = abort_event or asyncio.Event()
        while not abort.is_set():
            try:
                messages = await loop.run_in_executor(None, self._fetch_new_messages)
                self.mark_connected()
                for message in messages:
                    if abort.is_set():
                        break
                    await on_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.mark_disconnected(str(exc))
                log.warning("email.poll.failed", {"error": str(exc)})

            try:
                await asyncio.wait_for(
                    abort.wait(),
                    timeout=float(self._resolved["pollIntervalSeconds"]),
                )
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        self.mark_disconnected()

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        target = self.normalize_target(ctx.to)
        if not target:
            return DeliveryResult(
                channel_id="email",
                message_id="",
                success=False,
                error="Email send requires a valid recipient email address",
            )

        try:
            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(
                None,
                self._send_email,
                target,
                ctx.text,
                ctx.reply_to_id,
                ctx.thread_id,
                None,
            )
            self.record_message()
            return DeliveryResult(
                channel_id="email",
                message_id=message_id,
                chat_id=target,
                success=True,
            )
        except Exception as exc:
            return DeliveryResult(
                channel_id="email",
                message_id="",
                success=False,
                error=f"Email send failed: {exc}",
                retryable=self._is_retryable(exc),
            )

    async def send_media(self, ctx: OutboundContext) -> DeliveryResult:
        target = self.normalize_target(ctx.to)
        if not target:
            return DeliveryResult(
                channel_id="email",
                message_id="",
                success=False,
                error="Email send_media requires a valid recipient email address",
            )
        if not ctx.media_url:
            return await self.send_text(ctx)

        path = self._local_media_path(ctx.media_url)
        if path is None:
            return await self.send_text(
                OutboundContext(
                    **{
                        **vars(ctx),
                        "text": f"{ctx.text}\n\nAttachment/link: {ctx.media_url}".strip(),
                    }
                )
            )

        try:
            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(
                None,
                self._send_email,
                target,
                ctx.text,
                ctx.reply_to_id,
                ctx.thread_id,
                path,
            )
            self.record_message()
            return DeliveryResult(
                channel_id="email",
                message_id=message_id,
                chat_id=target,
                success=True,
            )
        except Exception as exc:
            return DeliveryResult(
                channel_id="email",
                message_id="",
                success=False,
                error=f"Email send_media failed: {exc}",
                retryable=self._is_retryable(exc),
            )

    def _test_connections(self) -> None:
        cfg = self._resolved
        imap = self._connect_imap()
        try:
            imap.login(cfg["address"], cfg["password"])
            _send_imap_id(imap)
            imap.select("INBOX")
            if cfg["skipExistingOnStart"]:
                status, data = imap.uid("search", None, "ALL")
                if status == "OK" and data and data[0]:
                    self._seen_uids.update(data[0].split())
                    self._trim_seen_uids()
        finally:
            try:
                imap.logout()
            except Exception:
                pass

        smtp = self._connect_smtp()
        try:
            smtp.login(cfg["address"], cfg["password"])
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()

    def _fetch_new_messages(self) -> list[InboundMessage]:
        cfg = self._resolved
        parsed_messages: list[InboundMessage] = []
        imap = self._connect_imap()
        try:
            imap.login(cfg["address"], cfg["password"])
            _send_imap_id(imap)
            imap.select("INBOX")
            status, data = imap.uid("search", None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return parsed_messages

            for uid in data[0].split():
                if uid in self._seen_uids:
                    continue

                status, msg_data = imap.uid("fetch", uid, "(RFC822)")
                if status != "OK":
                    continue
                try:
                    raw_email = msg_data[0][1]
                    if not isinstance(raw_email, (bytes, bytearray)):
                        log.warning("email.imap.non_bytes_payload", {"uid": uid.decode(errors="replace")})
                        continue

                    message = email_lib.message_from_bytes(raw_email)
                    parsed = self._parse_and_authorize(
                        message,
                        uid.decode(errors="replace"),
                    )
                except (IndexError, TypeError):
                    log.warning("email.imap.malformed_response", {"uid": uid.decode(errors="replace")})
                    continue
                except Exception as exc:
                    log.warning("email.imap.parse_message_failed", {
                        "uid": uid.decode(errors="replace"),
                        "error": str(exc),
                    })
                    continue

                if parsed is not None:
                    parsed_messages.append(parsed)

                self._seen_uids.add(uid)
                self._trim_seen_uids()
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return parsed_messages

    def _parse_and_authorize(
        self,
        message: email_lib.message.Message,
        uid: str,
    ) -> Optional[InboundMessage]:
        cfg = self._resolved
        parsed = build_inbound_message(
            message,
            uid=uid,
            account_id="default",
            skip_attachments=bool(cfg["skipAttachments"]),
        )
        if parsed is None:
            return None

        inbound = parsed.inbound
        sender = inbound.sender_id
        if sender == cfg["address"]:
            return None
        if is_automated_sender(sender, dict(message.items())):
            return None

        allowed = parse_allowed_senders(cfg)
        if not cfg["allowAll"]:
            if not allowed or sender not in allowed:
                log.debug("email.sender.blocked", {"sender": sender})
                return None

        if cfg["requireAuthenticatedSender"] and allowed and not cfg["allowAll"]:
            if not cfg["authservId"]:
                log.warning("email.sender.authserv_id_missing", {"sender": sender})
                return None
            authenticated, reason = verify_sender_authentication(
                message,
                sender,
                authserv_id=str(cfg["authservId"]),
            )
            if not authenticated:
                log.warning("email.sender.unauthenticated", {
                    "sender": sender,
                    "reason": reason,
                })
                return None

        context = {
            "subject": parsed.subject,
            "message_id": parsed.message_id,
            "references": parsed.references,
        }
        self._thread_context[sender] = context
        for key in {parsed.message_id, parsed.inbound.thread_id or "", parsed.inbound.reply_to_id or ""}:
            if key:
                self._thread_context[key] = context
        return inbound

    def _connect_smtp(self) -> smtplib.SMTP:
        cfg = self._resolved
        context = ssl.create_default_context()
        host = cfg["smtpHost"]
        port = int(cfg["smtpPort"])

        def connect_once(*, ipv4_only: bool = False) -> smtplib.SMTP:
            smtp_cls = _IPv4SMTP if ipv4_only else smtplib.SMTP
            smtp_ssl_cls = _IPv4SMTP_SSL if ipv4_only else smtplib.SMTP_SSL
            if cfg["smtpSecurity"] == "ssl":
                return smtp_ssl_cls(
                    host,
                    port,
                    timeout=SMTP_CONNECT_TIMEOUT,
                    context=context,
                )
            smtp = smtp_cls(
                host,
                port,
                timeout=SMTP_CONNECT_TIMEOUT,
            )
            if cfg["smtpSecurity"] != "starttls":
                return smtp
            try:
                code, response = smtp.starttls(context=context)
            except Exception:
                smtp.close()
                raise
            if code != 220:
                smtp.close()
                raise RuntimeError(f"SMTP STARTTLS not available: {response}")
            return smtp

        try:
            return connect_once()
        except (socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            if isinstance(exc, ssl.SSLError):
                raise
            return connect_once(ipv4_only=True)

    def _connect_imap(self) -> imaplib.IMAP4:
        cfg = self._resolved
        context = ssl.create_default_context()
        if cfg["imapSecurity"] == "ssl":
            return imaplib.IMAP4_SSL(
                cfg["imapHost"],
                int(cfg["imapPort"]),
                timeout=30,
                ssl_context=context,
            )

        imap = imaplib.IMAP4(cfg["imapHost"], int(cfg["imapPort"]), timeout=30)
        if cfg["imapSecurity"] != "starttls":
            return imap

        try:
            code, response = imap.starttls(ssl_context=context)
        except Exception:
            imap.close()
            raise
        if isinstance(code, (bytes, bytearray)):
            code = code.decode("ascii", errors="ignore")
        if isinstance(response, (bytes, bytearray)):
            response = response.decode("ascii", errors="ignore")
        code = str(code or "").strip().upper()
        response = str(response or "").strip()
        if code != "OK":
            imap.close()
            raise RuntimeError(f"IMAP STARTTLS not available: {response}")
        return imap

    def _send_email(
        self,
        to_addr: str,
        body: str,
        reply_to_msg_id: Optional[str],
        thread_id: Optional[str],
        attachment_path: Optional[Path],
    ) -> str:
        cfg = self._resolved
        msg = MIMEMultipart()
        msg["From"] = cfg["address"]
        msg["To"] = to_addr

        ctx = self._resolve_thread_context(to_addr, reply_to_msg_id, thread_id)
        subject = ctx.get("subject") or cfg["defaultSubject"]
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject

        original_id = reply_to_msg_id or ctx.get("message_id")
        references = thread_id or ctx.get("references") or original_id
        if original_id:
            msg["In-Reply-To"] = original_id
        if references:
            msg["References"] = references

        msg["Date"] = formatdate(localtime=True)
        message_id = f"<flocks-{uuid.uuid4().hex[:12]}@{self._message_id_domain()}>"
        msg["Message-ID"] = message_id
        msg.attach(MIMEText(body or "", "plain", "utf-8"))

        if attachment_path is not None:
            with attachment_path.open("rb") as handle:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(handle.read())
            encoders.encode_base64(part)
            safe_name = sanitize_filename(attachment_path.name, fallback="attachment")
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=safe_name,
            )
            msg.attach(part)

        smtp = self._connect_smtp()
        try:
            smtp.login(cfg["address"], cfg["password"])
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()
        return message_id

    def _resolve_thread_context(
        self,
        to_addr: str,
        reply_to_msg_id: Optional[str],
        thread_id: Optional[str],
    ) -> dict[str, str]:
        for key in (thread_id, reply_to_msg_id, to_addr):
            if key and key in self._thread_context:
                return self._thread_context[key]
        return {}

    def _message_id_domain(self) -> str:
        address = str(self._resolved.get("address") or "")
        if "@" not in address:
            return "localhost"
        return address.rsplit("@", 1)[-1] or "localhost"

    def _trim_seen_uids(self) -> None:
        if len(self._seen_uids) <= self._seen_uids_max:
            return
        try:
            sorted_uids = sorted(self._seen_uids, key=lambda item: int(item))
            self._seen_uids = set(sorted_uids[-self._seen_uids_max // 2:])
        except (TypeError, ValueError):
            keep = list(self._seen_uids)[-self._seen_uids_max // 2:]
            self._seen_uids = set(keep)

    @staticmethod
    def _local_media_path(media_url: str) -> Optional[Path]:
        value = str(media_url or "").strip()
        if value.startswith("file://"):
            from urllib.parse import unquote
            value = unquote(value[len("file://"):])
        if value.startswith(("http://", "https://")):
            return None
        path = Path(value)
        return path if path.is_file() else None

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                TimeoutError,
                ConnectionError,
                OSError,
                smtplib.SMTPServerDisconnected,
                smtplib.SMTPConnectError,
                smtplib.SMTPHeloError,
                socket.timeout,
            ),
        )
