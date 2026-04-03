"""
Model catalog — loads provider and model definitions from catalog.json.

The static data file ``catalog.json`` (same directory) is the single source
of truth for built-in provider metadata, default URLs, NPM packages, and
model definitions.

This module re-hydrates the JSON into typed Pydantic objects for use by the
rest of the system (Provider SDKs, routes, etc.).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from flocks.provider.types import (
    AuthMethod,
    ConfigurateMethod,
    CredentialFieldSchema,
    CredentialSchema,
    ModelCapabilitiesV2,
    ModelDefinition,
    ModelFeature,
    ModelLimits,
    ModelStatus,
    ModelType,
    ParameterRule,
    ParameterType,
    PriceConfig,
    ProviderMeta,
)

# ==================== Common Parameter Rules ====================

TEMPERATURE_RULE = ParameterRule(
    name="temperature", label="Temperature", type=ParameterType.FLOAT,
    default=1.0, min=0.0, max=2.0, precision=2,
    help_text="Controls randomness. Lower = more deterministic.",
)

TOP_P_RULE = ParameterRule(
    name="top_p", label="Top P", type=ParameterType.FLOAT,
    default=1.0, min=0.0, max=1.0, precision=2,
    help_text="Nucleus sampling threshold.",
)

MAX_TOKENS_RULE = ParameterRule(
    name="max_tokens", label="Max Tokens", type=ParameterType.INT,
    default=4096, min=1, max=128000,
    help_text="Maximum number of output tokens.",
)


def _llm_params(max_output: int = 4096) -> list:
    """Standard LLM parameter rules."""
    return [
        TEMPERATURE_RULE,
        TOP_P_RULE,
        ParameterRule(
            name="max_tokens", label="Max Tokens", type=ParameterType.INT,
            default=min(4096, max_output), min=1, max=max_output,
        ),
    ]


# ==================== JSON Loader ====================

_CATALOG_JSON_PATH = Path(__file__).parent / "catalog.json"


def _load_catalog_json() -> Dict[str, Any]:
    """Load catalog.json once and return as raw dict."""
    with open(_CATALOG_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_credential_schemas(raw_schemas: list) -> List[CredentialSchema]:
    """Parse credential_schemas from JSON into typed objects."""
    result = []
    for s in raw_schemas:
        fields = []
        for f in s.get("fields", []):
            fields.append(CredentialFieldSchema(
                name=f["name"],
                label=f.get("label", f["name"]),
                type=f.get("type", "text"),
                required=f.get("required", False),
                placeholder=f.get("placeholder"),
                help_text=f.get("help_text"),
                options=f.get("options"),
            ))
        result.append(CredentialSchema(
            auth_method=AuthMethod(s["auth_method"]),
            fields=fields,
        ))
    return result


def _parse_provider_meta(provider_id: str, raw: Dict[str, Any]) -> ProviderMeta:
    """Parse a provider entry into ProviderMeta."""
    auth_methods = []
    for s in raw.get("credential_schemas", []):
        try:
            auth_methods.append(AuthMethod(s["auth_method"]))
        except ValueError:
            pass
    if not auth_methods:
        auth_methods = [AuthMethod.API_KEY]

    return ProviderMeta(
        id=provider_id,
        name=raw["name"],
        description=raw.get("description"),
        supported_auth_methods=auth_methods,
        credential_schemas=_parse_credential_schemas(raw.get("credential_schemas", [])),
        env_vars=raw.get("env_vars", []),
    )


def _parse_model_definitions(
    provider_id: str, raw_models: Dict[str, Any]
) -> List[ModelDefinition]:
    """Parse models dict from JSON into ModelDefinition list."""
    result = []
    for model_id, m in raw_models.items():
        caps_raw = m.get("capabilities", {})
        limits_raw = m.get("limits", {})
        pricing_raw = m.get("pricing")

        features = []
        if caps_raw.get("supports_tools"):
            features.append(ModelFeature.TOOL_CALL)
        if caps_raw.get("supports_vision"):
            features.append(ModelFeature.VISION)
        if caps_raw.get("supports_reasoning"):
            features.append(ModelFeature.REASONING)

        capabilities = ModelCapabilitiesV2(
            features=features,
            supports_tools=caps_raw.get("supports_tools", False),
            supports_vision=caps_raw.get("supports_vision", False),
            supports_reasoning=caps_raw.get("supports_reasoning", False),
            supports_streaming=caps_raw.get("supports_streaming", True),
        )

        limits = ModelLimits(
            context_window=limits_raw.get("context_window", 128000),
            max_output_tokens=limits_raw.get("max_output_tokens", 4096),
        )

        pricing = None
        if pricing_raw:
            pricing = PriceConfig(
                input=pricing_raw.get("input", 0.0),
                output=pricing_raw.get("output", 0.0),
                cache_read=pricing_raw.get("cache_read"),
                cache_write=pricing_raw.get("cache_write"),
                currency=pricing_raw.get("currency", "USD"),
            )

        model_type_str = m.get("model_type", "llm")
        try:
            model_type = ModelType(model_type_str)
        except ValueError:
            model_type = ModelType.LLM

        status_str = m.get("status", "active")
        try:
            model_status = ModelStatus(status_str)
        except ValueError:
            model_status = ModelStatus.ACTIVE

        max_out = limits_raw.get("max_output_tokens", 4096)

        result.append(ModelDefinition(
            id=model_id,
            name=m["name"],
            provider_id=provider_id,
            model_type=model_type,
            family=m.get("family"),
            status=model_status,
            capabilities=capabilities,
            limits=limits,
            pricing=pricing,
            parameter_rules=_llm_params(max_out),
        ))
    return result


# ==================== Catalog Singleton ====================

_raw_catalog: Optional[Dict[str, Any]] = None
_parsed_catalog: Optional[Dict[str, Dict]] = None


def _ensure_loaded():
    """Lazy-load and parse the catalog."""
    global _raw_catalog, _parsed_catalog
    if _raw_catalog is not None:
        return

    _raw_catalog = _load_catalog_json()
    _parsed_catalog = {}

    for pid, raw in _raw_catalog.items():
        _parsed_catalog[pid] = {
            "meta": _parse_provider_meta(pid, raw),
            "models": _parse_model_definitions(pid, raw.get("models", {})),
        }


def get_catalog() -> Dict[str, Dict]:
    """Get the full parsed catalog (provider_id -> {meta, models})."""
    _ensure_loaded()
    assert _parsed_catalog is not None
    return _parsed_catalog


def get_raw_catalog() -> Dict[str, Any]:
    """Get the raw JSON catalog dict."""
    _ensure_loaded()
    assert _raw_catalog is not None
    return _raw_catalog


# Legacy alias — used by existing code that imports _CATALOG directly
@property
def _CATALOG():
    return get_catalog()


# Make _CATALOG work as a module-level dict-like via __getattr__
# so `from flocks.provider.model_catalog import _CATALOG` still works.
class _CatalogProxy:
    """Lazy proxy so _CATALOG works as a module-level dict."""

    def __getitem__(self, key):
        return get_catalog()[key]

    def __contains__(self, key):
        return key in get_catalog()

    def __iter__(self):
        return iter(get_catalog())

    def items(self):
        return get_catalog().items()

    def keys(self):
        return get_catalog().keys()

    def values(self):
        return get_catalog().values()

    def get(self, key, default=None):
        return get_catalog().get(key, default)

    def __len__(self):
        return len(get_catalog())


_CATALOG = _CatalogProxy()


# ==================== Public API ====================


def get_provider_meta(provider_id: str) -> Optional[ProviderMeta]:
    """Get provider metadata from catalog."""
    entry = get_catalog().get(provider_id)
    return entry["meta"] if entry else None


def get_provider_model_definitions(provider_id: str) -> List[ModelDefinition]:
    """Get model definitions from catalog."""
    entry = get_catalog().get(provider_id)
    return list(entry["models"]) if entry else []


def get_provider_npm(provider_id: str) -> str:
    """Get NPM SDK package name for a provider."""
    raw = get_raw_catalog().get(provider_id, {})
    return raw.get("npm", "@ai-sdk/openai-compatible")


def get_provider_default_url(provider_id: str) -> Optional[str]:
    """Get default base URL for a provider."""
    raw = get_raw_catalog().get(provider_id, {})
    return raw.get("default_base_url")


def list_catalog_provider_ids() -> List[str]:
    """List all provider IDs in the catalog."""
    _ensure_loaded()
    assert _raw_catalog is not None
    return list(_raw_catalog.keys())


def sync_catalog_models_to_config() -> int:
    """Sync new models from catalog.json into flocks.json for existing providers.

    When catalog.json gains new models (e.g. after a code update), providers
    already configured in flocks.json won't see them because flocks.json only
    gets a snapshot at first-add time.  This function adds any missing catalog
    models to existing provider entries so they appear automatically.

    Only *new* model IDs are added; existing model entries are never modified
    or removed, preserving user customizations.

    NOTE: If a user manually removes a catalog model from flocks.json, it will
    be re-added on next startup.  This is intentional — there is currently no
    UI for per-model deletion, and catalog models should always be visible.

    Returns:
        Number of models added across all providers.
    """
    from flocks.config.config_writer import ConfigWriter

    data = ConfigWriter._read_raw()
    providers = data.get("provider", {})
    if not providers:
        return 0

    catalog_provider_ids = set(list_catalog_provider_ids())
    dirty = False
    total_added = 0

    for provider_id, pconfig in providers.items():
        if provider_id not in catalog_provider_ids:
            continue

        existing_models = pconfig.get("models")
        if not isinstance(existing_models, dict):
            continue

        catalog_defs = get_provider_model_definitions(provider_id)
        if not catalog_defs:
            continue

        added = 0
        for m in catalog_defs:
            if m.id not in existing_models:
                existing_models[m.id] = {"name": m.name}
                added += 1

        if added:
            dirty = True
            total_added += added

    if dirty:
        ConfigWriter._write_raw(data)

    return total_added
