"""Shared helpers for LLM hook request/response payload handling."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from flocks.provider.provider import ChatMessage


class StreamingTextReplacementBuffer:
    """Incrementally replace streamed placeholders without leaking partial tokens."""

    def __init__(self, replacements: List[Tuple[str, str]]):
        self._replacements = [
            (pattern, value)
            for pattern, value in sorted(replacements, key=lambda item: len(item[0]), reverse=True)
            if pattern
        ]
        self._buffer = ""
        self._prefixes: set[str] = set()
        self._max_pattern_len = 0
        for pattern, _value in self._replacements:
            self._max_pattern_len = max(self._max_pattern_len, len(pattern))
            for index in range(1, len(pattern)):
                self._prefixes.add(pattern[:index])

    @property
    def enabled(self) -> bool:
        return bool(self._replacements)

    def feed(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        self._buffer += text
        keep = self._pending_suffix_length(self._buffer)
        if keep:
            flush_text = self._buffer[:-keep]
            self._buffer = self._buffer[-keep:]
        else:
            flush_text = self._buffer
            self._buffer = ""
        return restore_text_with_replacements(flush_text, self._replacements)

    def flush(self) -> str:
        if not self.enabled or not self._buffer:
            return ""
        flush_text = self._buffer
        self._buffer = ""
        return restore_text_with_replacements(flush_text, self._replacements)

    def _pending_suffix_length(self, text: str) -> int:
        max_keep = min(len(text), max(self._max_pattern_len - 1, 0))
        for length in range(max_keep, 0, -1):
            if text[-length:] in self._prefixes:
                return length
        return 0


def serialize_chat_message(message: ChatMessage) -> Dict[str, Any]:
    payload = message.model_dump(exclude_none=True)
    if not payload.get("custom_settings"):
        payload.pop("custom_settings", None)
    return payload


def stream_text_replacements_from_hook_output(output: Dict[str, Any]) -> List[Tuple[str, str]]:
    redaction = output.get("redaction") if isinstance(output, dict) else None
    raw_items = redaction.get("streamTextReplacements") if isinstance(redaction, dict) else None
    if not isinstance(raw_items, list):
        return []

    replacements: List[Tuple[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        placeholder = item.get("placeholder")
        value = item.get("value")
        if isinstance(placeholder, str) and isinstance(value, str):
            replacements.append((placeholder, value))
    return replacements


def restore_text_with_replacements(text: str, replacements: List[Tuple[str, str]]) -> str:
    restored = text
    for pattern, value in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        if pattern:
            restored = restored.replace(pattern, value)
    return restored


def restore_value_with_replacements(value: Any, replacements: List[Tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return restore_text_with_replacements(value, replacements)
    if isinstance(value, list):
        return [restore_value_with_replacements(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: restore_value_with_replacements(item, replacements)
            for key, item in value.items()
        }
    return value


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)


def strip_think_blocks(text: str) -> str:
    if not isinstance(text, str) or "<think" not in text.lower():
        return text
    without_blocks = _THINK_BLOCK_RE.sub("", text)
    return _THINK_TAG_RE.sub("", without_blocks).strip()


def apply_hook_request_output(
    messages: List[ChatMessage],
    provider_options: Dict[str, Any],
    output: Dict[str, Any],
) -> tuple[List[ChatMessage], Dict[str, Any]]:
    updated_request = output.get("request") if isinstance(output, dict) else None
    if not isinstance(updated_request, dict):
        return messages, provider_options

    updated_messages = messages
    raw_messages = updated_request.get("messages")
    if isinstance(raw_messages, list):
        updated_messages = [
            message
            if isinstance(message, ChatMessage)
            else ChatMessage.model_validate(message)
            for message in raw_messages
        ]

    updated_provider_options = provider_options
    raw_provider_options = updated_request.get("providerOptions")
    if isinstance(raw_provider_options, dict):
        updated_provider_options = dict(raw_provider_options)

    return updated_messages, updated_provider_options
