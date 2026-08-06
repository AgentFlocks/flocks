"""Parse syslog lines (RFC 5424, RFC 3164, and Sangfor SE) without external deps."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

_PRI_RE = re.compile(r"^<(\d{1,3})>")
# After stripping PRI: MMM DD hh:mm:ss hostname tag: msg
_RFC3164_REST_RE = re.compile(
    r"^([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s*(.*)$",
    re.DOTALL,
)
# Non-standard but common: <PRI>ISO_TS HOSTNAME APP[PID]: msg  (no RFC5424 version)
_ISO3164_REST_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)"  # ISO timestamp
    r"\s+(\S+)"                                                           # hostname
    r"\s+(\S+?)\s*:\s*"                                                   # app_name/tag:
    r"([\s\S]*)$",                                                        # message
    re.DOTALL,
)
_SE_ISO_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_SE_SPACE_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\s*\d{2}:\s*\d{2}")


def _pri_parts(pri: int) -> tuple[int, int]:
    facility = pri >> 3
    severity = pri & 7
    return facility, severity


def _normalize_ts(ts: Optional[str]) -> str:
    if not ts:
        return ""
    ts = ts.strip()
    # RFC5424 full-date
    if "T" in ts:
        try:
            # Zulu
            if ts.endswith("Z"):
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat()
            return datetime.fromisoformat(ts).isoformat()
        except ValueError:
            return ts
    # RFC3164: Oct 11 22:14:15 (no year — use current year best-effort)
    try:
        now = datetime.now()
        dt = datetime.strptime(f"{now.year} {ts}", "%Y %b %d %H:%M:%S")
        return dt.isoformat()
    except ValueError:
        return ts


def parse_syslog(raw: str, format_hint: str = "auto") -> Dict[str, Any]:
    """
    Parse one syslog payload into a dict suitable for workflow inputs.

    format_hint: "auto" | "rfc3164" | "rfc5424" | "se"
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else raw
    text = text.strip()
    if not text:
        return {
            "raw": text,
            "facility": 0,
            "severity": 0,
            "timestamp": "",
            "hostname": "",
            "app_name": "",
            "message": "",
            "format": "empty",
        }

    m_pri = _PRI_RE.match(text)
    if not m_pri:
        if format_hint == "se" or (format_hint == "auto" and _looks_like_se(text)):
            return _parse_se(text, raw=text, facility=1, severity=6)
        return {
            "raw": text,
            "facility": 0,
            "severity": 0,
            "timestamp": "",
            "hostname": "",
            "app_name": "",
            "message": text,
            "format": "unparsed",
        }

    pri = int(m_pri.group(1))
    facility, severity = _pri_parts(pri)
    rest = text[m_pri.end() :]

    if format_hint == "se" or (format_hint == "auto" and _looks_like_se(rest)):
        return _parse_se(rest, raw=text, facility=facility, severity=severity)
    if format_hint == "rfc3164":
        return _parse_rfc3164(rest, raw=text, facility=facility, severity=severity)
    if format_hint == "rfc5424":
        return _parse_rfc5424(rest, raw=text, facility=facility, severity=severity)

    # auto: RFC5424 if second token is a single digit version number
    if rest and rest[0].isdigit():
        first_space = rest.find(" ")
        if first_space > 0 and rest[:first_space].isdigit():
            return _parse_rfc5424(rest, raw=text, facility=facility, severity=severity)
        # Non-standard: <PRI>ISO_TS HOSTNAME APP[PID]: msg (no version number)
        if first_space > 0 and "T" in rest[:first_space]:
            m_iso = _ISO3164_REST_RE.match(rest)
            if m_iso:
                return _parse_iso3164(m_iso, raw=text, facility=facility, severity=severity)

    return _parse_rfc3164(rest, raw=text, facility=facility, severity=severity)


def _looks_like_se(rest: str) -> bool:
    parts = rest.strip().split("|!", 3)
    return (
        len(parts) == 4
        and parts[1].strip() in {"secevent", "alarm"}
        and parts[3].lstrip().startswith(("{", "["))
    )


def _normalize_se_ts(prefix: str) -> str:
    iso_match = _SE_ISO_TS_RE.search(prefix)
    if iso_match:
        return _normalize_ts(iso_match.group(0))
    space_match = _SE_SPACE_TS_RE.search(prefix)
    if space_match:
        timestamp = re.sub(r"\s*:\s*", ":", space_match.group(0))
        try:
            return datetime.fromisoformat(timestamp).isoformat()
        except ValueError:
            pass
    return prefix.strip()


def _parse_se(
    rest: str,
    *,
    raw: str,
    facility: int,
    severity: int,
) -> Dict[str, Any]:
    parts = rest.strip().split("|!", 3)
    if len(parts) != 4:
        return {
            "raw": raw,
            "facility": facility,
            "severity": severity,
            "timestamp": "",
            "hostname": "",
            "app_name": "",
            "message": rest.strip(),
            "format": "se",
            "log_type": "",
            "client_ip": "",
            "data": None,
        }

    timestamp, log_type, client_ip, message = (part.strip() for part in parts)
    try:
        data = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        data = None

    return {
        "raw": raw,
        "facility": facility,
        "severity": severity,
        "timestamp": _normalize_se_ts(timestamp),
        "hostname": client_ip,
        "app_name": log_type,
        "message": message,
        "format": "se",
        "log_type": log_type,
        "client_ip": client_ip,
        "data": data,
    }


def _next_rfc5424_token(s: str) -> tuple[str, str]:
    """Pop one syslog field from *s*; structured data may start with '['."""
    s = s.lstrip()
    if not s:
        return "", ""
    if s[0] == "[":
        depth = 0
        for j, c in enumerate(s):
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return s[: j + 1], s[j + 1 :].lstrip()
        return s, ""
    sp = s.find(" ")
    if sp == -1:
        return s, ""
    return s[:sp], s[sp + 1 :].lstrip()


def _parse_rfc5424(
    rest: str,
    *,
    raw: str,
    facility: int,
    severity: int,
) -> Dict[str, Any]:
    s = rest.lstrip()
    if not s:
        return _parse_rfc3164(rest, raw=raw, facility=facility, severity=severity)

    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    version = s[:i].strip()
    s = s[i:].lstrip()
    if not version.isdigit():
        return _parse_rfc3164(rest, raw=raw, facility=facility, severity=severity)

    ts, s = _next_rfc5424_token(s)
    hostname, s = _next_rfc5424_token(s)
    app_name, s = _next_rfc5424_token(s)
    _procid, s = _next_rfc5424_token(s)
    _msgid, s = _next_rfc5424_token(s)
    _sdata, s = _next_rfc5424_token(s)
    msg = s.strip()

    return {
        "raw": raw,
        "facility": facility,
        "severity": severity,
        "timestamp": _normalize_ts(ts),
        "hostname": hostname if hostname != "-" else "",
        "app_name": app_name if app_name != "-" else "",
        "message": msg,
        "format": "rfc5424",
    }


def _parse_iso3164(
    m: "re.Match[str]",
    *,
    raw: str,
    facility: int,
    severity: int,
) -> Dict[str, Any]:
    """Handle non-standard <PRI>ISO_TS HOSTNAME APP[PID]: msg (no RFC5424 version)."""
    return {
        "raw": raw,
        "facility": facility,
        "severity": severity,
        "timestamp": _normalize_ts(m.group(1)),
        "hostname": m.group(2),
        "app_name": m.group(3),
        "message": m.group(4).strip(),
        "format": "iso3164",
    }


def _parse_rfc3164(
    rest: str,
    *,
    raw: str,
    facility: int,
    severity: int,
) -> Dict[str, Any]:
    m = _RFC3164_REST_RE.match(rest.strip())
    if m:
        ts = m.group(1)
        hostname = m.group(2)
        remainder = (m.group(3) or "").strip()
        app_name = ""
        message = remainder
        # TAG: message (tag is alphanumeric, often "sshd" or "su")
        if remainder and ":" in remainder:
            tag, _, body = remainder.partition(":")
            if tag and " " not in tag and tag.isprintable():
                app_name = tag.strip()
                message = body.strip()
        return {
            "raw": raw,
            "facility": facility,
            "severity": severity,
            "timestamp": _normalize_ts(ts),
            "hostname": hostname,
            "app_name": app_name,
            "message": message,
            "format": "rfc3164",
        }

    return {
        "raw": raw,
        "facility": facility,
        "severity": severity,
        "timestamp": "",
        "hostname": "",
        "app_name": "",
        "message": rest.strip(),
        "format": "rfc3164",
    }
