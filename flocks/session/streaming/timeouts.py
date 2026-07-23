"""Resolve adaptive timeout budgets for LLM streaming responses."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlparse


DEFAULT_FIRST_CHUNK_TIMEOUT_S = 120.0
DEFAULT_LOCAL_FIRST_CHUNK_TIMEOUT_S = 1800.0
DEFAULT_ONGOING_CHUNK_TIMEOUT_S = 300.0

FIRST_CHUNK_TIMEOUT_ENV = "FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S"
ONGOING_CHUNK_TIMEOUT_ENV = "FLOCKS_LLM_STREAM_ONGOING_CHUNK_TIMEOUT_S"

_FIRST_CHUNK_SETTING_KEYS = (
    "stream_first_chunk_timeout_s",
    "streamFirstChunkTimeoutSeconds",
)
_ONGOING_CHUNK_SETTING_KEYS = (
    "stream_ongoing_chunk_timeout_s",
    "streamOngoingChunkTimeoutSeconds",
)
_LOCAL_PROVIDER_IDS = frozenset({"local", "ollama"})
_LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})


@dataclass(frozen=True)
class LlmStreamTimeouts:
    """Effective timeout budgets for one LLM stream."""

    first_chunk_s: float
    ongoing_chunk_s: float
    is_local: bool


def _positive_float(value: Any) -> Optional[float]:
    """Return a positive finite float, or ``None`` for an invalid value."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed == float("inf") or parsed != parsed:
        return None
    return parsed


def _setting(
    settings: Mapping[str, Any],
    keys: tuple[str, ...],
) -> Optional[float]:
    for key in keys:
        if key in settings:
            value = _positive_float(settings[key])
            if value is not None:
                return value
    return None


def _provider_base_url(provider: Any) -> str:
    config = getattr(provider, "_config", None)
    candidates = (
        getattr(config, "base_url", None),
        getattr(provider, "_base_url", None),
        getattr(provider, "DEFAULT_BASE_URL", None),
    )
    return next(
        (value.strip() for value in candidates if isinstance(value, str) and value.strip()),
        "",
    )


def _is_local_endpoint(provider: Any) -> bool:
    provider_id = str(getattr(provider, "id", "") or "").strip().lower()
    if provider_id in _LOCAL_PROVIDER_IDS:
        return True

    hostname = (urlparse(_provider_base_url(provider)).hostname or "").lower()
    if not hostname:
        return False
    if hostname in _LOCAL_HOSTNAMES or hostname.endswith(".local"):
        return True

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def _provider_settings(provider: Any) -> Mapping[str, Any]:
    config = getattr(provider, "_config", None)
    settings = getattr(config, "custom_settings", None)
    return settings if isinstance(settings, Mapping) else {}


def _model_settings(provider: Any, model_id: str) -> Mapping[str, Any]:
    try:
        models = provider.get_models()
    except Exception:
        return {}
    for model in models or []:
        if getattr(model, "id", None) != model_id:
            continue
        settings = getattr(model, "custom_settings", None)
        return settings if isinstance(settings, Mapping) else {}
    return {}


def _resolve_timeout(
    *,
    model_settings: Mapping[str, Any],
    provider_settings: Mapping[str, Any],
    setting_keys: tuple[str, ...],
    environment_name: str,
    default: float,
) -> float:
    candidates = (
        _setting(model_settings, setting_keys),
        _setting(provider_settings, setting_keys),
        _positive_float(os.getenv(environment_name)),
    )
    return next((value for value in candidates if value is not None), default)


def resolve_llm_stream_timeouts(provider: Any, model_id: str) -> LlmStreamTimeouts:
    """Resolve model, provider, environment, and endpoint-aware timeouts.

    Precedence is model configuration, provider configuration, environment,
    then the built-in endpoint-aware default.
    """
    is_local = _is_local_endpoint(provider)
    provider_settings = _provider_settings(provider)
    model_settings = _model_settings(provider, model_id)
    first_chunk_default = DEFAULT_LOCAL_FIRST_CHUNK_TIMEOUT_S if is_local else DEFAULT_FIRST_CHUNK_TIMEOUT_S

    return LlmStreamTimeouts(
        first_chunk_s=_resolve_timeout(
            model_settings=model_settings,
            provider_settings=provider_settings,
            setting_keys=_FIRST_CHUNK_SETTING_KEYS,
            environment_name=FIRST_CHUNK_TIMEOUT_ENV,
            default=first_chunk_default,
        ),
        ongoing_chunk_s=_resolve_timeout(
            model_settings=model_settings,
            provider_settings=provider_settings,
            setting_keys=_ONGOING_CHUNK_SETTING_KEYS,
            environment_name=ONGOING_CHUNK_TIMEOUT_ENV,
            default=DEFAULT_ONGOING_CHUNK_TIMEOUT_S,
        ),
        is_local=is_local,
    )
