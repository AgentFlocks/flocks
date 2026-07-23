"""
Provider-specific options builder.

Centralises the logic for assembling thinking / reasoning / token-limit
kwargs that get forwarded to each provider's ``chat_stream`` call.

Both ``SessionRunner`` (session/runner.py) and ``AgentExecutor``
(agent/runtime/executor.py) delegate to :func:`build_provider_options`
so that provider rules are maintained in exactly one place.
"""

from typing import Any, Dict, Optional, Tuple

from flocks.provider.interleaved import (
    REASONING_TRANSPORT_ANTHROPIC_MESSAGES,
    REASONING_TRANSPORT_GENERIC_CHAT,
    resolve_interleaved_capability,
    resolve_reasoning_transport,
)

from flocks.utils.log import Log

log = Log.create(service="provider.options")

# ---------------------------------------------------------------------------
# Defaults (override via function args or, later, config)
# ---------------------------------------------------------------------------
DEFAULT_THINKING_BUDGET = 16000
DEFAULT_OUTPUT_BUFFER = 8192
DEFAULT_KIMI_K3_REASONING_EFFORT = "max"
KIMI_K3_REASONING_EFFORTS = frozenset({"low", "high", "max"})

_GENERIC_CHAT_REASONING_EXTRA_BODY_KEYS = {
    "reasoning_content": "enable_thinking",
    "reasoning_details": "reasoning_split",
}


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    """Coerce config values to bool while preserving None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def _resolve_reasoning_enabled(provider_id: str, model_id: str) -> Optional[bool]:
    """Read model-level default reasoning settings from flocks.json."""
    try:
        from flocks.provider.model_manager import get_model_manager

        setting = get_model_manager().get_setting(provider_id, model_id)
        if not setting:
            return None

        default_parameters = setting.default_parameters or {}
        return _coerce_optional_bool(default_parameters.get("enable_thinking"))
    except Exception as exc:
        log.debug("options.reasoning_setting_lookup_failed", {
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(exc),
        })
        return None


def _resolve_reasoning_effort(provider_id: str, model_id: str) -> Optional[str]:
    """Read a model-level reasoning effort from flocks.json."""
    try:
        from flocks.provider.model_manager import get_model_manager

        setting = get_model_manager().get_setting(provider_id, model_id)
        if not setting:
            return None

        value = (setting.default_parameters or {}).get("reasoning_effort")
        return value.strip().lower() if isinstance(value, str) else None
    except Exception as exc:
        log.debug("options.reasoning_effort_setting_lookup_failed", {
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(exc),
        })
        return None


def _resolve_default_extra_body(provider_id: str, model_id: str) -> Optional[Dict[str, Any]]:
    """Read model-level OpenAI-compatible extra_body from flocks.json."""
    try:
        from flocks.provider.model_manager import get_model_manager

        setting = get_model_manager().get_setting(provider_id, model_id)
        if not setting:
            return None

        default_parameters = setting.default_parameters or {}
        extra_body = default_parameters.get("extra_body")
        return dict(extra_body) if isinstance(extra_body, dict) else None
    except Exception as exc:
        log.debug("options.extra_body_setting_lookup_failed", {
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(exc),
        })
        return None


def _lookup_raw_model_metadata(provider_id: str, model_id: str) -> Optional[Any]:
    """Return provider/model metadata without applying inferred defaults."""
    try:
        from flocks.provider.provider import Provider

        provider = Provider.get(provider_id)
        if provider is not None:
            try:
                for model in provider.get_model_definitions():
                    if getattr(model, "id", None) == model_id:
                        return model
            except Exception as exc:
                log.debug("options.raw_model_lookup.definitions_failed", {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "error": str(exc),
                })

            for model in getattr(provider, "_config_models", []):
                if getattr(model, "id", None) == model_id:
                    return model

            try:
                for model in provider.get_models():
                    if getattr(model, "id", None) == model_id:
                        return model
            except Exception as exc:
                log.debug("options.raw_model_lookup.runtime_failed", {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "error": str(exc),
                })

        return Provider.get_model(model_id)
    except Exception as exc:
        log.debug("options.raw_model_lookup_failed", {
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(exc),
        })
        return None


def _resolve_provider_base_url(provider_id: str) -> Optional[str]:
    """Return the active provider base URL when configured."""
    try:
        from flocks.provider.provider import Provider

        provider = Provider.get(provider_id)
        if provider is None:
            return None
        provider_config = getattr(provider, "_config", None)
        return (
            getattr(provider_config, "base_url", None)
            or getattr(provider, "_base_url", None)
        )
    except Exception as exc:
        log.debug("options.provider_base_url_lookup_failed", {
            "provider_id": provider_id,
            "error": str(exc),
        })
        return None


def _resolve_model_reasoning_context(
    provider_id: str,
    model_id: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Resolve replay capability and transport through separate paths."""
    model = _lookup_raw_model_metadata(provider_id, model_id)
    capabilities = getattr(model, "capabilities", None) if model else None
    explicit_interleaved = (
        getattr(capabilities, "interleaved", None) if capabilities else None
    )
    base_url = _resolve_provider_base_url(provider_id)
    interleaved = resolve_interleaved_capability(
        provider_id=provider_id,
        model_id=model_id,
        explicit_capability=explicit_interleaved,
        base_url=base_url,
    )
    transport = resolve_reasoning_transport(
        provider_id=provider_id,
        model_id=model_id,
        base_url=base_url,
    )
    return interleaved, transport


def _resolve_interleaved_capability(
    provider_id: str,
    model_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve the active model's interleaved replay capability."""
    interleaved, _transport = _resolve_model_reasoning_context(provider_id, model_id)
    return interleaved


def _resolve_reasoning_transport(provider_id: str, model_id: str) -> str:
    """Resolve the active model's request transport family."""
    _interleaved, transport = _resolve_model_reasoning_context(provider_id, model_id)
    return transport


def _build_generic_chat_extra_body(
    provider_id: str,
    model_id: str,
    interleaved_capability: Optional[Dict[str, Any]],
    reasoning_enabled: Optional[bool],
    reasoning_effort: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Build OpenAI-compatible reasoning params for the active replay field."""
    provider_lower = provider_id.lower()
    model_lower = model_id.lower()
    enabled = reasoning_enabled is not False

    if "kimi-k3" in model_lower:
        effort = reasoning_effort or DEFAULT_KIMI_K3_REASONING_EFFORT
        if effort not in KIMI_K3_REASONING_EFFORTS:
            log.warning(
                "options.kimi_k3.invalid_reasoning_effort",
                {
                    "model_id": model_id,
                    "reasoning_effort": effort,
                    "fallback": DEFAULT_KIMI_K3_REASONING_EFFORT,
                },
            )
            effort = DEFAULT_KIMI_K3_REASONING_EFFORT
        return {"reasoning_effort": effort}

    if "deepseek" in model_lower or provider_lower == "deepseek":
        return {
            "thinking": (
                {"type": "enabled"}
                if enabled
                else {"type": "disabled"}
            )
        }

    if "glm" in model_lower or provider_lower == "zhipu":
        return {
            "thinking": (
                {"type": "enabled", "clear_thinking": False}
                if enabled
                else {"type": "disabled"}
            )
        }

    if "mimo" in model_lower:
        return {
            "thinking": (
                {"type": "enabled"}
                if enabled
                else {"type": "disabled"}
            )
        }

    if "kimi-k2.7" in model_lower:
        # K2.7 cannot disable thinking. Send the enabled form explicitly
        # because OpenAI-compatible gateways may not apply Moonshot's default.
        return {"thinking": {"type": "enabled"}}

    if "kimi" in model_lower:
        return {
            "thinking": (
                {"type": "enabled"}
                if enabled
                else {"type": "disabled"}
            )
        }

    if isinstance(interleaved_capability, dict):
        field = interleaved_capability.get("field")
        key = _GENERIC_CHAT_REASONING_EXTRA_BODY_KEYS.get(field)
        if key:
            return {key: enabled}

    if reasoning_enabled is True:
        return {"enable_thinking": True}

    return None


def build_provider_options(
    provider_id: str,
    model_id: str,
    *,
    reasoning_enabled: Optional[bool] = None,
    reasoning_effort: Optional[str] = None,
    thinking_budget: int = DEFAULT_THINKING_BUDGET,
    resolve_max_tokens: bool = True,
) -> Dict[str, Any]:
    """Build provider-specific kwargs for a chat / chat_stream call.

    Parameters
    ----------
    provider_id:
        Canonical provider identifier (e.g. ``"anthropic"``, ``"openai"``).
    model_id:
        The model being called (e.g. ``"claude-sonnet-4"``).
    thinking_budget:
        Token budget for extended-thinking / reasoning where applicable.
    reasoning_effort:
        Kimi K3 reasoning effort (``low``, ``high``, or ``max``).
    resolve_max_tokens:
        If *True*, fall back to the model's configured ``max_tokens`` when
        no provider-specific logic has already set it.

    Returns
    -------
    Dict that can be unpacked as ``**kwargs`` into the provider call.
    """
    options: Dict[str, Any] = {}
    model_lower = model_id.lower()
    interleaved_capability = _resolve_interleaved_capability(provider_id, model_id)
    reasoning_transport = _resolve_reasoning_transport(provider_id, model_id)
    reasoning_effort_explicit = isinstance(reasoning_effort, str)
    reasoning_enabled = (
        _coerce_optional_bool(reasoning_enabled)
        if reasoning_enabled is not None
        else _resolve_reasoning_enabled(provider_id, model_id)
    )
    reasoning_effort = (
        reasoning_effort.strip().lower()
        if isinstance(reasoning_effort, str)
        else _resolve_reasoning_effort(provider_id, model_id)
    )
    configured_extra_body = _resolve_default_extra_body(provider_id, model_id)
    interleaved_enabled = interleaved_capability is not None
    if interleaved_enabled and reasoning_enabled is None:
        reasoning_enabled = True

    # -- Anthropic Messages thinking -----------------------------------------
    if (
        reasoning_transport == REASONING_TRANSPORT_ANTHROPIC_MESSAGES
        and "claude" in model_lower
    ):
        # Use the model's catalog API limit as max_tokens so the full output
        # capacity is available after thinking.  Provider.get_model() returns
        # the catalog entry (catalog takes priority over flocks.json overrides
        # per anthropic.py get_model_definitions), so api_limit reflects the
        # real Anthropic limit (e.g. 64 000 for claude-sonnet-4-20250514).
        if reasoning_enabled is not False:
            api_limit = _get_catalog_model_max_tokens(model_id)
            effective_budget = min(thinking_budget, api_limit // 2) if api_limit else thinking_budget
            options["thinking"] = {
                "type": "enabled",
                "budget_tokens": effective_budget,
            }
            options["max_tokens"] = api_limit if api_limit else (thinking_budget + DEFAULT_OUTPUT_BUFFER)

    # -- OpenAI reasoning (o1 / o3 / gpt-5) --------------------------------
    elif provider_id == "openai":
        if reasoning_enabled is not False and any(tag in model_lower for tag in ("o1", "o3", "gpt-5")):
            options["reasoningEffort"] = "medium"

    # -- Google Gemini thinking ---------------------------------------------
    elif provider_id == "google":
        if reasoning_enabled is not False and "gemini" in model_lower:
            if "2.5" in model_lower:
                options["thinkingConfig"] = {
                    "includeThoughts": True,
                    "thinkingBudget": thinking_budget,
                }
            elif "gemini-3" in model_lower:
                options["thinkingConfig"] = {
                    "includeThoughts": True,
                    "thinkingLevel": "high",
                }

    # -- Groq thinking ------------------------------------------------------
    elif provider_id == "groq":
        if reasoning_enabled is not False:
            options["thinkingLevel"] = "high"

    # -- Generic-chat (OpenAI-compat) interleaved thinking --------------------
    # The Anthropic branch above handles ``anthropic_messages`` transport.
    # Generic-chat endpoints expose provider-specific extra_body toggles:
    # most reasoning_content models use enable_thinking, while MiniMax's
    # OpenAI-compatible interleaved format uses reasoning_split so the model
    # returns reasoning_details that can be replayed in later tool turns.
    elif reasoning_transport == REASONING_TRANSPORT_GENERIC_CHAT and (
        configured_extra_body
        or interleaved_enabled
        or reasoning_enabled is True
        or "kimi-k2.7" in model_lower
        or "kimi-k3" in model_lower
    ):
        automatic_extra_body = _build_generic_chat_extra_body(
            provider_id,
            model_id,
            interleaved_capability,
            reasoning_enabled,
            reasoning_effort,
        )
        extra_body = dict(configured_extra_body or automatic_extra_body or {})
        if "kimi-k3" in model_lower:
            # K3 is always a reasoning model and uses the top-level
            # reasoning_effort field instead of the K2.x thinking object.
            extra_body.pop("thinking", None)
            if reasoning_effort_explicit:
                extra_body["reasoning_effort"] = (automatic_extra_body or {}).get(
                    "reasoning_effort",
                    DEFAULT_KIMI_K3_REASONING_EFFORT,
                )
            else:
                configured_effort = extra_body.get("reasoning_effort")
                if configured_effort not in KIMI_K3_REASONING_EFFORTS:
                    extra_body["reasoning_effort"] = (automatic_extra_body or {}).get(
                        "reasoning_effort",
                        DEFAULT_KIMI_K3_REASONING_EFFORT,
                    )
        elif "kimi-k2.7" in model_lower:
            # K2.7 rejects disabled thinking. Normalize configured values so
            # direct and gateway-backed endpoints always request reasoning.
            extra_body["thinking"] = {"type": "enabled"}
        if extra_body:
            options["extra_body"] = extra_body
            log.debug("options.thinking_params.resolved", {
                "provider_id": provider_id,
                "model_id": model_id,
                "extra_body_keys": list(options["extra_body"].keys()),
            })

    # -- max_tokens fallback from model config ------------------------------
    if resolve_max_tokens and "max_tokens" not in options:
        _apply_max_tokens_from_config(options, provider_id, model_id)

    return options


def _get_catalog_model_max_tokens(model_id: str) -> Optional[int]:
    """Return the catalog-level max_output_tokens for *model_id*, or None.

    Uses ``Provider.get_model()`` which resolves against the global model
    registry.  For the Anthropic provider the registry is populated with
    catalog entries taking priority over flocks.json overrides (see
    ``AnthropicProvider.get_model_definitions``), so the value returned here
    is the real Anthropic API limit, not the conservative flocks.json value.
    """
    try:
        from flocks.provider.provider import Provider
        model_info = Provider.get_model(model_id)
        if model_info and model_info.capabilities and model_info.capabilities.max_tokens:
            return model_info.capabilities.max_tokens
    except Exception:
        pass
    return None

def _apply_max_tokens_from_config(
    options: Dict[str, Any],
    provider_id: str,
    model_id: str,
) -> None:
    """Set ``max_tokens`` from provider / model config when available."""
    from flocks.provider.provider import Provider

    provider = Provider.get(provider_id)
    if not provider:
        return

    model_info = None
    for m in getattr(provider, "_config_models", []):
        if m.id == model_id:
            model_info = m
            break
    if model_info is None:
        model_info = Provider.get_model(model_id)

    if model_info and model_info.capabilities and model_info.capabilities.max_tokens:
        options["max_tokens"] = model_info.capabilities.max_tokens
        log.debug("options.max_tokens.from_config", {
            "model_id": model_id,
            "max_tokens": model_info.capabilities.max_tokens,
        })
