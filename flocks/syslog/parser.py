"""Parse syslog lines (RFC 5424 and BSD / RFC 3164 style) without external deps."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

_PRI_RE = re.compile(r"^<(\d{1,3})>")
# After stripping PRI: MMM DD hh:mm:ss hostname tag: msg
_RFC3164_REST_RE = re.compile(
    r"^([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s*(.*)$",
    re.DOTALL,
)


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

    format_hint: "auto" | "rfc3164" | "rfc5424"
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

    if format_hint == "rfc3164":
        return _parse_rfc3164(rest, raw=text, facility=facility, severity=severity)
    if format_hint == "rfc5424":
        return _parse_rfc5424(rest, raw=text, facility=facility, severity=severity)

    # auto: RFC5424 if second token is a digit version
    if rest and rest[0].isdigit():
        first_space = rest.find(" ")
        if first_space > 0 and rest[:first_space].isdigit():
            return _parse_rfc5424(rest, raw=text, facility=facility, severity=severity)

    return _parse_rfc3164(rest, raw=text, facility=facility, severity=severity)


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
