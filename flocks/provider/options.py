"""
Provider-specific options builder.

Centralises the logic for assembling thinking / reasoning / token-limit
kwargs that get forwarded to each provider's ``chat_stream`` call.

Both ``SessionRunner`` (session/runner.py) and ``AgentExecutor``
(agent/runtime/executor.py) delegate to :func:`build_provider_options`
so that provider rules are maintained in exactly one place.
"""

from typing import Any, Dict, Optional

from flocks.utils.log import Log

log = Log.create(service="provider.options")

# ---------------------------------------------------------------------------
# Defaults (override via function args or, later, config)
# ---------------------------------------------------------------------------
DEFAULT_THINKING_BUDGET = 16000
DEFAULT_OUTPUT_BUFFER = 8192


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


def build_provider_options(
    provider_id: str,
    model_id: str,
    *,
    reasoning_enabled: Optional[bool] = None,
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
    resolve_max_tokens:
        If *True*, fall back to the model's configured ``max_tokens`` when
        no provider-specific logic has already set it.

    Returns
    -------
    Dict that can be unpacked as ``**kwargs`` into the provider call.
    """
    options: Dict[str, Any] = {}
    model_lower = model_id.lower()
    reasoning_enabled = (
        _coerce_optional_bool(reasoning_enabled)
        if reasoning_enabled is not None
        else _resolve_reasoning_enabled(provider_id, model_id)
    )

    # -- Claude extended thinking (any provider, including proxies) ----------
    if "claude" in model_lower:
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

    # -- Qwen reasoning (ThreatBook-hosted or Alibaba DashScope) -------------
    elif provider_id in ("threatbook-cn-llm", "threatbook-io-llm", "alibaba", "moonshot"):
        if "qwen3-max" in model_lower or "qwen3.6-plus" in model_lower:
            options["extra_body"] = {
                "enable_thinking": True if reasoning_enabled is None else reasoning_enabled
            }
        elif "kimi-k2.5" in model_lower or "kimi-k2.6" in model_lower:
            # ThreatBook CN defaults hybrid-thinking models to reasoning-on.
            # Other compatible providers keep direct reply as the default.
            default_enabled = provider_id == "threatbook-cn-llm"
            options["extra_body"] = {
                "enable_thinking": default_enabled if reasoning_enabled is None else reasoning_enabled
            }

    # -- Amazon Bedrock reasoning -------------------------------------------
    elif provider_id == "amazon-bedrock":
        if reasoning_enabled is not False and "anthropic" in model_lower:
            options["reasoningConfig"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
        elif reasoning_enabled is not False and "nova" in model_lower:
            options["reasoningConfig"] = {
                "type": "enabled",
                "maxReasoningEffort": "high",
            }

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

