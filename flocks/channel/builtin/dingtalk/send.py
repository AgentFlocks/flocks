"""
DingTalk message-sending helpers — active outbound only.

Only the **enterprise app robot OAPI** path (``send_message_app``) is
supported.  Targets are routed automatically:
- ``user:`` / staffId → ``/v1.0/robot/oToMessages/batchSend``
- ``chat:`` / openConversationId → ``/v1.0/robot/groupMessages/send``

Long texts are chunked transparently.  Custom group-robot incoming webhooks
are intentionally not supported.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from flocks.channel.builtin.dingtalk.client import (
    DingTalkApiError,
    api_request_for_account,
)
from flocks.channel.builtin.dingtalk.config import (
    resolve_target_kind,
    strip_target_prefix,
)
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk.send")


_DEFAULT_TEXT_CHUNK_LIMIT = 4000
# DingTalk 群聊最长 5000 字符，单聊更宽，统一保守取 4000 与 Feishu 对齐


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def build_app_payload(text: str, render_mode: str) -> tuple[str, str]:
    """Return ``(msgKey, msgParam_json)`` for the app-mode robot APIs.

    DingTalk 的 ``msgKey`` / ``msgParam`` 字段对应消息模板:
    - ``sampleText`` → 纯文本
    - ``sampleMarkdown`` → markdown（含标题）
    """
    if render_mode == "plain":
        return "sampleText", json.dumps({"content": text}, ensure_ascii=False)

    title = _extract_title(text) or "通知"
    return (
        "sampleMarkdown",
        json.dumps({"title": title, "text": text}, ensure_ascii=False),
    )


def _extract_title(text: str) -> str:
    """Pick the first non-empty line as the markdown title (capped at 64 chars)."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:64]
    return ""


# ---------------------------------------------------------------------------
# Chunking (kept local so send.py is self-contained)
# ---------------------------------------------------------------------------

def _chunk_text(text: str, limit: int) -> list[str]:
    """Split long text into chunks, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining[:limit].rfind("\n")
        if cut <= limit // 4:
            cut = limit
        chunk = remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip("\n")
        if chunk:
            chunks.append(chunk)
    return chunks or [text]


# ---------------------------------------------------------------------------
# App-mode (enterprise robot) — OAPI v1.0
# ---------------------------------------------------------------------------

async def _send_app_one(
    *,
    config: dict,
    to: str,
    msg_key: str,
    msg_param: str,
    account_id: Optional[str],
) -> Dict[str, Any]:
    """Send a single non-chunked message via the app-mode robot APIs."""
    from flocks.channel.builtin.dingtalk.config import resolve_account_credentials

    _, _, robot_code = resolve_account_credentials(config, account_id)
    if not robot_code:
        # robotCode defaults to appKey/clientId, so an empty value means the
        # underlying credentials are missing — surface the actionable hint.
        raise ValueError(
            "DingTalk credentials not configured: provide appKey/clientId"
            " (and appSecret/clientSecret) in the channel config"
            + (f" for account '{account_id}'" if account_id else "")
        )

    bare = strip_target_prefix(to)
    if not bare:
        raise ValueError("DingTalk send: empty target")

    if resolve_target_kind(to) == "group":
        body = {
            "robotCode": robot_code,
            "openConversationId": bare,
            "msgKey": msg_key,
            "msgParam": msg_param,
        }
        data = await api_request_for_account(
            "POST", "/v1.0/robot/groupMessages/send",
            config=config, account_id=account_id, json_body=body,
        )
        return {
            "message_id": str(data.get("processQueryKey") or ""),
            "chat_id": bare,
        }

    body = {
        "robotCode": robot_code,
        "userIds": [bare],
        "msgKey": msg_key,
        "msgParam": msg_param,
    }
    data = await api_request_for_account(
        "POST", "/v1.0/robot/oToMessages/batchSend",
        config=config, account_id=account_id, json_body=body,
    )
    return {
        "message_id": str(data.get("processQueryKey") or ""),
        "chat_id": bare,
    }


async def send_message_app(
    *,
    config: dict,
    to: str,
    text: str,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a text/markdown message via the DingTalk enterprise robot OAPI.

    ``config["renderMode"]`` controls the payload:
    - ``"plain"`` → sampleText
    - otherwise  → sampleMarkdown (default)

    Long texts are automatically split into multiple sequential messages.
    Returns ``{"message_id": "...", "chat_id": "..."}`` for the *last* chunk.
    """
    render_mode = str(config.get("renderMode") or "auto").lower()
    chunk_limit = int(config.get("textChunkLimit", _DEFAULT_TEXT_CHUNK_LIMIT))
    chunks = _chunk_text(text, chunk_limit)

    last: Dict[str, Any] = {}
    for chunk in chunks:
        msg_key, msg_param = build_app_payload(chunk, render_mode)
        last = await _send_app_one(
            config=config, to=to, msg_key=msg_key, msg_param=msg_param,
            account_id=account_id,
        )
    return last


__all__ = [
    "DingTalkApiError",
    "build_app_payload",
    "send_message_app",
]
