"""Reasoning replay helpers for thinking/interleaved providers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flocks.provider.provider import ChatMessage


def _message_requires_echo(message: ChatMessage, interleaved: Dict[str, Any]) -> bool:
    """Return True when the current provider requires a replay field."""
    echo_mode = interleaved.get("echo", "when_present")
    if echo_mode == "all_assistant":
        return True
    if echo_mode == "tool_calls":
        return bool(message.tool_calls)
    if echo_mode == "when_present":
        return bool(message.reasoning_content or message.reasoning_details)
    return False


def _promotable_reasoning_text(message: ChatMessage) -> tuple[Optional[str], Optional[str]]:
    """Return the best-effort text form that can be promoted cross-provider."""
    if isinstance(message.reasoning_content, str) and message.reasoning_content:
        return message.reasoning_content, "promoted_reasoning_content"
    if isinstance(message.reasoning, str) and message.reasoning:
        return message.reasoning, "promoted_reasoning"
    return None, None


def _summary_reasoning_details(text: str) -> List[Dict[str, Any]]:
    """Wrap plain reasoning text into a generic details payload."""
    return [{"type": "reasoning.summary", "text": text}]


def prepare_reasoning_for_replay(
    *,
    provider_id: str,
    model_id: str,
    message: ChatMessage,
    interleaved: Optional[Dict[str, Any]],
) -> ChatMessage:
    """Prepare provider-facing reasoning fields for API replay.

    Hermes-style rules:
    - Preserve explicit provider-facing fields.
    - Upgrade stale empty-string placeholders when the model requires echo.
    - Promote internal ``reasoning`` only for providers configured to do so.
    - Use a placeholder instead of leaking another provider's CoT to strict
      echo providers such as DeepSeek/Kimi.
    """
    if message.role != "assistant" or not interleaved:
        return message

    prepared = message.model_copy(deep=True)
    field = interleaved.get("field", "reasoning_content")
    placeholder = interleaved.get("placeholder", " ")
    cross_provider_policy = interleaved.get("cross_provider_policy", "promote")
    requires_echo = _message_requires_echo(prepared, interleaved)
    promoted_text, promoted_source = _promotable_reasoning_text(prepared)

    if field == "reasoning_details":
        prepared.reasoning_content = None
        if prepared.reasoning_details:
            prepared.reasoning_source = prepared.reasoning_source or "native_reasoning_details"
            return prepared
        if cross_provider_policy == "promote" and promoted_text:
            prepared.reasoning_details = _summary_reasoning_details(promoted_text)
            prepared.reasoning_source = promoted_source or "promoted_reasoning"
            return prepared
        if requires_echo:
            prepared.reasoning_details = _summary_reasoning_details(placeholder)
            prepared.reasoning_source = "placeholder"
            return prepared
        return prepared

    prepared.reasoning_details = None
    if isinstance(prepared.reasoning_content, str):
        if prepared.reasoning_content == "" and requires_echo:
            prepared.reasoning_content = placeholder
            prepared.reasoning_source = "placeholder"
        return prepared

    if cross_provider_policy == "promote" and promoted_text:
        prepared.reasoning_content = promoted_text
        prepared.reasoning_source = promoted_source or "promoted_reasoning"
        return prepared

    if requires_echo:
        prepared.reasoning_content = placeholder
        prepared.reasoning_source = "placeholder"
        return prepared

    return prepared
