"""Inbound email parsing and sender safety helpers."""

from __future__ import annotations

import email as email_lib
import re
from dataclasses import dataclass
from email.header import decode_header
from typing import Any, Optional

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.media_filename import sanitize_filename

from .config import normalize_email_address


_NOREPLY_PATTERNS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@",
    "automated@", "auto-confirm", "auto-reply", "automailer",
)

_AUTOMATED_HEADERS = {
    "Auto-Submitted": lambda v: v.lower() != "no",
    "Precedence": lambda v: v.lower() in {"bulk", "list", "junk"},
    "X-Auto-Response-Suppress": lambda v: bool(v),
    "List-Unsubscribe": lambda v: bool(v),
}

_AUTH_METHOD_RE = re.compile(r"\b(dmarc|dkim|spf)\s*=\s*([a-z]+)", re.IGNORECASE)
_AUTH_PROP_RE = re.compile(
    r"\b(header\.from|header\.d|smtp\.mailfrom|smtp\.from|envelope-from)\s*=\s*([^\s;]+)",
    re.IGNORECASE,
)


@dataclass
class ParsedEmail:
    inbound: InboundMessage
    subject: str
    message_id: str
    references: str


def decode_header_value(raw: str) -> str:
    parts = decode_header(raw or "")
    decoded: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded).strip()


def extract_email_address(raw: str) -> str:
    match = re.search(r"<([^>]+)>", raw or "")
    if match:
        return normalize_email_address(match.group(1))
    return normalize_email_address(raw or "")


def extract_sender_name(raw: str, fallback: str) -> str:
    decoded = decode_header_value(raw)
    if "<" in decoded:
        decoded = decoded.split("<", 1)[0].strip().strip('"')
    return decoded or fallback


def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    replacements = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text_body(msg: email_lib.message.Message) -> str:
    if msg.is_multipart():
        for preferred in ("text/plain", "text/html"):
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))
                if "attachment" in disposition or content_type != preferred:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                return strip_html(text) if preferred == "text/html" else text
        return ""

    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    text = payload.decode(charset, errors="replace")
    return strip_html(text) if msg.get_content_type() == "text/html" else text


def attachment_summaries(
    msg: email_lib.message.Message,
    *,
    skip_attachments: bool,
) -> list[str]:
    if skip_attachments or not msg.is_multipart():
        return []

    summaries: list[str] = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in disposition and "inline" not in disposition:
            continue
        content_type = part.get_content_type()
        if content_type in {"text/plain", "text/html"} and "attachment" not in disposition:
            continue
        filename = part.get_filename()
        filename = decode_header_value(filename) if filename else "attachment"
        filename = sanitize_filename(filename, fallback="attachment")
        summaries.append(f"{filename} ({content_type})")
    return summaries


def is_automated_sender(address: str, headers: dict[str, str]) -> bool:
    addr = normalize_email_address(address)
    if any(pattern in addr for pattern in _NOREPLY_PATTERNS):
        return True
    for header, check in _AUTOMATED_HEADERS.items():
        value = headers.get(header, "")
        if value and check(value):
            return True
    return False


def _domain_of(address: str) -> str:
    _, _, domain = normalize_email_address(address).rpartition("@")
    return domain.strip().lower()


def _domains_aligned(a: str, b: str) -> bool:
    a = (a or "").strip().lower().rstrip(".")
    b = (b or "").strip().lower().rstrip(".")
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def verify_sender_authentication(
    msg: email_lib.message.Message,
    from_addr: str,
    *,
    authserv_id: str = "",
) -> tuple[bool, str]:
    from_domain = _domain_of(from_addr)
    if not from_domain:
        return False, "missing From domain"

    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False, "no Authentication-Results header"

    normalized_expected = normalize_email_address(authserv_id).lower()
    if not normalized_expected:
        return False, "no authserv-id configured"

    trusted = None
    for raw in headers:
        value = " ".join(str(raw).split())
        serv = value.split(";", 1)[0].strip()
        if not _domains_aligned(serv, normalized_expected) and serv.lower() != normalized_expected:
            continue
        trusted = value
        break
    if trusted is None:
        return False, "no Authentication-Results from trusted authserv-id"

    methods = {m.lower(): r.lower() for m, r in _AUTH_METHOD_RE.findall(trusted)}
    props = {p.lower(): v.strip().strip('"') for p, v in _AUTH_PROP_RE.findall(trusted)}

    if methods.get("dmarc") == "pass":
        return True, "dmarc=pass"

    if methods.get("spf") == "pass":
        spf_domain = _domain_of(props.get("smtp.mailfrom", "")) or props.get("smtp.from", "") or props.get("envelope-from", "")
        spf_domain = _domain_of(spf_domain) if "@" in spf_domain else spf_domain
        if _domains_aligned(spf_domain, from_domain):
            return True, "spf=pass aligned"

    if methods.get("dkim") == "pass":
        dkim_domain = props.get("header.d", "") or _domain_of(props.get("header.from", ""))
        if _domains_aligned(dkim_domain, from_domain):
            return True, "dkim=pass aligned"

    return False, f"authentication failed ({trusted[:120]})"


def build_inbound_message(
    msg: email_lib.message.Message,
    *,
    uid: str,
    account_id: str,
    skip_attachments: bool,
) -> Optional[ParsedEmail]:
    sender_raw = msg.get("From", "")
    sender_addr = extract_email_address(sender_raw)
    if not sender_addr:
        return None

    subject = decode_header_value(msg.get("Subject", "(no subject)")) or "(no subject)"
    message_id = str(msg.get("Message-ID", "")).strip() or f"<imap-{uid}@local>"
    in_reply_to = str(msg.get("In-Reply-To", "")).strip()
    references = str(msg.get("References", "")).strip()
    body = extract_text_body(msg).strip()
    attachments = attachment_summaries(msg, skip_attachments=skip_attachments)

    text = body or "(empty email)"
    if attachments:
        text = f"{text}\n\n[Attachments]\n" + "\n".join(f"- {item}" for item in attachments)
    if subject and not subject.lower().startswith("re:"):
        text = f"[Subject: {subject}]\n\n{text}"

    return ParsedEmail(
        inbound=InboundMessage(
            channel_id="email",
            account_id=account_id,
            message_id=message_id,
            sender_id=sender_addr,
            sender_name=extract_sender_name(sender_raw, sender_addr),
            chat_id=sender_addr,
            chat_type=ChatType.DIRECT,
            text=text,
            reply_to_id=in_reply_to or None,
            thread_id=references or in_reply_to or message_id,
            raw={"uid": uid, "date": msg.get("Date", ""), "subject": subject},
        ),
        subject=subject,
        message_id=message_id,
        references=references,
    )
