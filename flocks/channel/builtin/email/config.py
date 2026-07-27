"""Configuration helpers for the Email channel."""

from __future__ import annotations

import re
from typing import Any


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def coerce_int(value: Any, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(str(value).strip(), 10)
    except (TypeError, ValueError):
        return default


def coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def coerce_security_mode(value: Any) -> str:
    mode = coerce_str(value).lower().strip()
    if mode in {"ssl", "starttls", "insecure"}:
        return mode
    return ""


def default_security(port: int, protocol: str) -> str:
    if protocol == "imap":
        if port == 993:
            return "ssl"
        if port == 143:
            return "starttls"
    elif protocol == "smtp":
        if port == 465:
            return "ssl"
        if port == 587:
            return "starttls"
    return ""


def normalize_email_address(raw: str) -> str:
    value = coerce_str(raw).lower()
    if value.startswith("mailto:"):
        value = value[len("mailto:"):].strip()
    if "<" in value and ">" in value:
        value = value.split("<", 1)[1].split(">", 1)[0].strip()
    return value


def parse_allowed_senders(config: dict[str, Any]) -> set[str]:
    raw = config.get("allowFrom")
    if raw is None:
        raw = config.get("allowedSenders")
    if raw is None:
        raw = config.get("allowedUsers")

    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        values = []

    return {
        normalize_email_address(str(item))
        for item in values
        if normalize_email_address(str(item))
    }


def is_valid_email(raw: str) -> bool:
    return bool(EMAIL_RE.fullmatch(normalize_email_address(raw)))


def resolved_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return normalized Email channel config with defaults applied."""
    imap_port = coerce_int(config.get("imapPort") or config.get("imap_port"), 993)
    smtp_port = coerce_int(config.get("smtpPort") or config.get("smtp_port"), 587)
    imap_security = coerce_security_mode(config.get("imapSecurity") or config.get("imap_security"))
    smtp_security = coerce_security_mode(config.get("smtpSecurity") or config.get("smtp_security"))
    if not imap_security:
        imap_security = default_security(imap_port, "imap")
    if not smtp_security:
        smtp_security = default_security(smtp_port, "smtp")

    return {
        **config,
        "address": normalize_email_address(coerce_str(config.get("address"))),
        "password": coerce_str(config.get("password")),
        "imapHost": coerce_str(config.get("imapHost") or config.get("imap_host")),
        "imapPort": imap_port,
        "imapSecurity": imap_security,
        "smtpHost": coerce_str(config.get("smtpHost") or config.get("smtp_host")),
        "smtpPort": smtp_port,
        "smtpSecurity": smtp_security,
        "pollIntervalSeconds": max(
            coerce_int(
                config.get("pollIntervalSeconds") or config.get("pollInterval") or config.get("poll_interval"),
                15,
            ),
            5,
        ),
        "skipExistingOnStart": coerce_bool(config.get("skipExistingOnStart"), True),
        "skipAttachments": coerce_bool(config.get("skipAttachments"), True),
        "allowAll": coerce_bool(config.get("allowAll"), False),
        "allowInsecureConnections": coerce_bool(config.get("allowInsecureConnections"), False),
        "requireAuthenticatedSender": coerce_bool(
            config.get("requireAuthenticatedSender"),
            True,
        ),
        "authservId": coerce_str(config.get("authservId") or config.get("authserv_id")),
        "defaultSubject": coerce_str(config.get("defaultSubject")) or "Flocks Agent",
    }
