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

        abort = abort_event or asyncio.Event()
        while not abort.is_set():
            try:
                messages = await loop.run_in_executor(None, self._fetch_new_messages)
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
        imap = imaplib.IMAP4_SSL(cfg["imapHost"], cfg["imapPort"], timeout=30)
        try:
            imap.login(cfg["address"], cfg["password"])
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
        imap = imaplib.IMAP4_SSL(cfg["imapHost"], cfg["imapPort"], timeout=30)
        try:
            imap.login(cfg["address"], cfg["password"])
            imap.select("INBOX")
            status, data = imap.uid("search", None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return parsed_messages

            for uid in data[0].split():
                if uid in self._seen_uids:
                    continue
                self._seen_uids.add(uid)
                self._trim_seen_uids()

                status, msg_data = imap.uid("fetch", uid, "(RFC822)")
                if status != "OK":
                    continue
                try:
                    raw_email = msg_data[0][1]
                except (IndexError, TypeError):
                    log.warning("email.imap.malformed_response", {"uid": uid.decode(errors="replace")})
                    continue
                if not isinstance(raw_email, (bytes, bytearray)):
                    log.warning("email.imap.non_bytes_payload", {"uid": uid.decode(errors="replace")})
                    continue

                message = email_lib.message_from_bytes(raw_email)
                parsed = self._parse_and_authorize(message, uid.decode(errors="replace"))
                if parsed is not None:
                    parsed_messages.append(parsed)
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
        if int(cfg["smtpPort"]) == 465:
            return smtplib.SMTP_SSL(
                cfg["smtpHost"],
                int(cfg["smtpPort"]),
                timeout=SMTP_CONNECT_TIMEOUT,
                context=context,
            )
        smtp = smtplib.SMTP(
            cfg["smtpHost"],
            int(cfg["smtpPort"]),
            timeout=SMTP_CONNECT_TIMEOUT,
        )
        try:
            smtp.starttls(context=context)
        except Exception:
            smtp.close()
            raise
        return smtp

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
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={attachment_path.name}",
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
