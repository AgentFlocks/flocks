"""
ThreatBook LLM provider implementations.

ThreatBook provides OpenAI-compatible endpoints for accessing hosted models.
Models are loaded from catalog.json and user-added custom models from
flocks.json by the parent OpenAIBaseProvider.get_models().
"""

from flocks.provider.sdk.openai_base import OpenAIBaseProvider


class ThreatBookCnLLMProvider(OpenAIBaseProvider):
    """ThreatBook-China LLM provider (OpenAI-compatible)."""

    # This public compatibility endpoint accepts both legacy and Router-issued
    # keys. Router dispatch stays behind the service boundary, so no internal
    # Router origin needs to be persisted in config or exposed by the Web UI.
    DEFAULT_BASE_URL = "https://llm.threatbook.cn/v1"
    ENV_API_KEY = ["THREATBOOK_CN_LLM_API_KEY"]
    ENV_BASE_URL = "THREATBOOK_CN_LLM_BASE_URL"
    CATALOG_ID = "threatbook-cn-llm"

    def __init__(self):
        super().__init__(provider_id="threatbook-cn-llm", name="ThreatBook-cn-llm")

    def _uses_router_key(self) -> bool:
        """Return whether the active credential was issued by Flocks Router."""
        api_key = self._config.api_key if self._config else None
        if not api_key:
            # A built-in provider can receive a model-management request
            # before Provider.apply_config() has hydrated its runtime config.
            # Consult the canonical persisted credential before the env value
            # so cold-start behavior matches the eventual active config.
            from flocks.provider.credential import get_api_key

            api_key = get_api_key(self.id) or self._api_key
        return isinstance(api_key, str) and api_key.startswith("fr_")

    def _apply_config_overrides(self, catalog_def, model):
        """Keep legacy model overrides, but make Router pricing authoritative."""
        overridden = super()._apply_config_overrides(catalog_def, model)
        if not self._uses_router_key():
            return overridden

        from flocks.provider.model_catalog import get_provider_pricing_profile

        router_pricing = get_provider_pricing_profile(
            self.CATALOG_ID,
            "router",
            catalog_def.id,
        )
        if router_pricing is not None:
            overridden.pricing = router_pricing
        return overridden


class ThreatBookIoLLMProvider(OpenAIBaseProvider):
    """ThreatBook international LLM provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://llm.threatbook.io/v1"
    ENV_API_KEY = ["THREATBOOK_IO_LLM_API_KEY"]
    ENV_BASE_URL = "THREATBOOK_IO_LLM_BASE_URL"
    CATALOG_ID = "threatbook-io-llm"

    def __init__(self):
        super().__init__(provider_id="threatbook-io-llm", name="ThreatBook-io-llm")
