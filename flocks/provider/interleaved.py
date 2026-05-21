"""Runtime inference for interleaved thinking replay."""

from __future__ import annotations

from typing import Any, Dict, Optional

REASONING_TRANSPORT_GENERIC_CHAT = "generic_chat"
REASONING_TRANSPORT_ANTHROPIC_MESSAGES = "anthropic_messages"


_STRICT_REASONING_CONTENT = {
    "field": "reasoning_content",
    "echo": "tool_calls",
    "placeholder": " ",
    "cross_provider_policy": "placeholder",
}

_PROMOTE_REASONING_CONTENT = {
    "field": "reasoning_content",
    "echo": "tool_calls",
    "cross_provider_policy": "promote",
}

_PROMOTE_REASONING_DETAILS = {
    "field": "reasoning_details",
    "echo": "tool_calls",
    "cross_provider_policy": "promote",
}

_ANTHROPIC_THINKING = {
    "field": "thinking",
    "echo": "tool_calls",
    "cross_provider_policy": "preserve",
}

_STRICT_REASONING_CONTENT_TOKENS = (
    "deepseek-reasoner",
    "deepseek-r1",
    "deepseek-v4",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "reasoner",
    "r1-0528",
    "kimi-k2.5",
    "kimi-k2.6",
    "kimi-k2-thinking",
    "kimi-k2-thinking-turbo",
    "k2-thinking",
    "k2-thinking-turbo",
    "mimo",
    "hunyuan-2.0-thinking",
    "hunyuan-t1",
)

_PROMOTE_REASONING_CONTENT_TOKENS = (
    "qwen3",
    "qwq",
    "qwen-max",
    "glm-4.5",
    "glm-4.6",
    "glm-4.7",
    "glm-5",
    "glm5",
    "glm-5.1",
    "glm-5v",
    "nemotron",
    "step-3.5",
    "step-3-5",
    "step-3.5-flash",
    "seed-2-0",
    "hermes-4",
    "sarvam",
    "agent-max",
    "code-max",
    "text-max",
    "gpt-oss",
    "laguna-",
    "gemma-4",
    "trinity-large-thinking",
)

_PROMOTE_REASONING_DETAILS_TOKENS = (
    "minimax",
    "gemini-3",
    "gemini-3.1",
)


def _lower(value: Optional[str]) -> str:
    return value.lower() if isinstance(value, str) else ""


def _matches_any(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def infer_interleaved_capability(
    *,
    provider_id: str,
    model_id: str,
    base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Infer interleaved replay policy for known reasoning model families.

    Explicit config and catalog metadata should take precedence. This helper is
    only a fallback for runtime-discovered or user-added models so the feature
    works without user-visible toggles.
    """
    pid = _lower(provider_id)
    mid = _lower(model_id)
    burl = _lower(base_url)

    if _matches_any(mid, *_PROMOTE_REASONING_DETAILS_TOKENS) or pid == "minimax":
        return dict(_PROMOTE_REASONING_DETAILS)

    if "claude" in mid or "anthropic" in pid or "anthropic.com" in burl:
        return dict(_ANTHROPIC_THINKING)

    if "gemini" in mid or pid == "google":
        return dict(_PROMOTE_REASONING_DETAILS)

    if (
        _matches_any(mid, *_PROMOTE_REASONING_CONTENT_TOKENS)
        or pid in {
            "alibaba",
            "zhipu",
            "stepfun",
        }
    ):
        return dict(_PROMOTE_REASONING_CONTENT)

    if _matches_any(mid, *_STRICT_REASONING_CONTENT_TOKENS):
        return dict(_STRICT_REASONING_CONTENT)
    if (
        "deepseek.com" in burl
        and _matches_any(mid, "r1", "reasoner", "thinking", "v4")
    ):
        return dict(_STRICT_REASONING_CONTENT)
    if any(token in burl for token in ("api.kimi.com", "moonshot", "xiaomimimo")):
        return dict(_STRICT_REASONING_CONTENT)
    if pid == "deepseek" and _matches_any(mid, "r1", "reasoner", "v4"):
        return dict(_STRICT_REASONING_CONTENT)
    if pid == "moonshot" and _matches_any(mid, "kimi", "k2.5", "k2.6", "thinking"):
        return dict(_STRICT_REASONING_CONTENT)

    if any(token in pid for token in ("hunyuan", "doubao", "sarvam")):
        return dict(_PROMOTE_REASONING_CONTENT)
    if pid in {
        "google-vertex-anthropic",
    }:
        return dict(_ANTHROPIC_THINKING)
    if pid in {
        "tencent",
    } and _matches_any(mid, "hunyuan", "thinking", "t1"):
        return dict(_STRICT_REASONING_CONTENT)

    return None


def resolve_interleaved_capability(
    *,
    provider_id: str,
    model_id: str,
    explicit_capability: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve replay capability without letting it choose the transport."""
    if isinstance(explicit_capability, dict):
        return dict(explicit_capability)

    inferred = infer_interleaved_capability(
        provider_id=provider_id,
        model_id=model_id,
        base_url=base_url,
    )
    return dict(inferred) if isinstance(inferred, dict) else None


def resolve_reasoning_transport(
    *,
    provider_id: str,
    model_id: str,
    base_url: Optional[str] = None,
) -> str:
    """Resolve the request protocol family independently from replay metadata.

    ``interleaved.field`` is a replay hint only. It must not decide which wire
    protocol serializes the request.
    """
    del model_id, base_url  # Reserved for future provider-specific routing.

    if _lower(provider_id) in {"anthropic", "google-vertex-anthropic"}:
        return REASONING_TRANSPORT_ANTHROPIC_MESSAGES
    return REASONING_TRANSPORT_GENERIC_CHAT


def apply_interleaved_capability_defaults(
    model: Any,
    *,
    provider_id: str,
    base_url: Optional[str] = None,
) -> Any:
    """Populate model.capabilities.interleaved when it is implicitly known."""
    capabilities = getattr(model, "capabilities", None)
    if capabilities is None or getattr(capabilities, "interleaved", None):
        return model

    resolved = resolve_interleaved_capability(
        provider_id=provider_id,
        model_id=getattr(model, "id", ""),
        base_url=base_url,
    )
    if resolved:
        capabilities.interleaved = resolved
    return model
