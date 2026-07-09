from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from typing import Any, Optional


def normalize_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
    }


def verify_timestamp(
    headers: Mapping[str, Any],
    *,
    timestamp_header: str,
    max_skew_seconds: int = 300,
) -> bool:
    normalized = normalize_headers(headers)
    timestamp_raw = normalized.get(timestamp_header.lower(), "").strip()
    if not timestamp_raw:
        return True
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False
    return abs(time.time() - timestamp) <= max_skew_seconds


def verify_signature(
    body: bytes,
    headers: Mapping[str, Any],
    *,
    timestamp_header: str,
    nonce_header: str,
    signature_header: str,
    secret: str,
) -> bool:
    if not secret:
        return True
    normalized = normalize_headers(headers)
    timestamp = normalized.get(timestamp_header.lower(), "")
    nonce = normalized.get(nonce_header.lower(), "")
    signature = normalized.get(signature_header.lower(), "")
    if not signature:
        return False
    payload = f"{timestamp}{nonce}{secret}".encode() + body
    expected = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(expected, signature)


def build_replay_key(
    headers: Mapping[str, Any],
    data: dict,
    *,
    event_id_path: tuple[str, str] = ("header", "event_id"),
    request_id_header: str = "x-lark-request-id",
    nonce_header: str = "x-lark-request-nonce",
    timestamp_header: str = "x-lark-request-timestamp",
) -> Optional[str]:
    normalized = normalize_headers(headers)
    event_container = data.get(event_id_path[0], {}) or {}
    event_id = str(event_container.get(event_id_path[1], "")).strip()
    if event_id:
        return f"replay:event:{event_id}"
    request_id = (normalized.get(request_id_header.lower(), "") or "").strip()
    if request_id:
        return f"replay:req:{request_id}"
    nonce = (normalized.get(nonce_header.lower(), "") or "").strip()
    timestamp = (normalized.get(timestamp_header.lower(), "") or "").strip()
    if nonce and timestamp:
        return f"replay:nonce:{timestamp}:{nonce}"
    return None
