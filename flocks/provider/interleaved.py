"""Runtime inference for interleaved thinking replay."""

from __future__ import annotations

from typing import Any, Dict, Optional


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


def _lower(value: Optional[str]) -> str:
    return value.lower() if isinstance(value, str) else ""


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

    if "minimax" in mid or pid == "minimax":
        return dict(_PROMOTE_REASONING_DETAILS)

    if any(token in mid for token in ("qwen3", "qwq", "qwen-max")) or pid == "alibaba":
        return dict(_PROMOTE_REASONING_CONTENT)

    if any(token in mid for token in ("glm-5", "glm5")) or pid == "zhipu":
        return dict(_PROMOTE_REASONING_CONTENT)

    if any(token in mid for token in ("deepseek-reasoner", "deepseek-r1", "reasoner")):
        return dict(_STRICT_REASONING_CONTENT)
    if "deepseek.com" in burl and any(token in mid for token in ("r1", "reasoner", "thinking")):
        return dict(_STRICT_REASONING_CONTENT)

    if any(token in mid for token in ("kimi-k2.5", "kimi-k2.6", "kimi-k2-thinking", "mimo")):
        return dict(_STRICT_REASONING_CONTENT)

    return None


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

    inferred = infer_interleaved_capability(
        provider_id=provider_id,
        model_id=getattr(model, "id", ""),
        base_url=base_url,
    )
    if inferred:
        capabilities.interleaved = inferred
    return model
