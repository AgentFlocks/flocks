"""
Inbound message parsing helpers for the iLink frame format.

These are pure functions over the raw frame dicts emitted by ``getupdates``,
suitable for unit-testing without an aiohttp session.
"""

from __future__ import annotations

from .config import ITEM_FILE, ITEM_IMAGE, ITEM_TEXT, ITEM_VIDEO, ITEM_VOICE


def extract_text(item_list: list) -> str:
    """Pull a flat text string out of an iLink ``item_list``.

    Handles plain text, replies / quotes (``ref_msg``), and voice-to-text
    transcription fallback.
    """
    for item in item_list:
        if item.get("type") == ITEM_TEXT:
            text = str((item.get("text_item") or {}).get("text") or "")
            ref = item.get("ref_msg") or {}
            ref_item = ref.get("message_item") or {}
            ref_type = ref_item.get("type")
            if ref_type in (ITEM_IMAGE, ITEM_VIDEO, ITEM_FILE, ITEM_VOICE):
                title = ref.get("title") or ""
                prefix = f"[引用媒体: {title}]\n" if title else "[引用媒体]\n"
                return f"{prefix}{text}".strip()
            if ref_item:
                parts: list[str] = []
                if ref.get("title"):
                    parts.append(str(ref["title"]))
                ref_text = extract_text([ref_item])
                if ref_text:
                    parts.append(ref_text)
                if parts:
                    return f"[引用: {' | '.join(parts)}]\n{text}".strip()
            return text
    for item in item_list:
        if item.get("type") == ITEM_VOICE:
            voice_text = str((item.get("voice_item") or {}).get("text") or "")
            if voice_text:
                return voice_text
    return ""


def guess_chat_type(message: dict, account_id: str) -> tuple[str, str]:
    """Return ``(chat_type, effective_chat_id)`` where chat_type ∈ ``"dm"`` | ``"group"``."""
    room_id = str(message.get("room_id") or message.get("chat_room_id") or "").strip()
    to_user_id = str(message.get("to_user_id") or "").strip()
    is_group = bool(room_id) or (
        to_user_id and account_id and to_user_id != account_id
        and message.get("msg_type") == 1
    )
    if is_group:
        return "group", room_id or to_user_id or str(message.get("from_user_id") or "")
    return "dm", str(message.get("from_user_id") or "")


def safe_id(value: object, keep: int = 8) -> str:
    """Truncate IDs for log output while keeping enough to be useful."""
    raw = str(value or "").strip()
    if not raw:
        return "?"
    return raw[:keep] if len(raw) > keep else raw
