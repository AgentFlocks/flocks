"""
Ollama provider implementation.

Dedicated Ollama provider with auto-discovery of locally running models.
Uses OpenAI-compatible API at http://localhost:11434/v1.
Docs: https://ollama.com
"""

from flocks.provider.sdk.openai_base import OpenAIBaseProvider


class OllamaProvider(OpenAIBaseProvider):
    """Ollama local model provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "http://localhost:11434/v1"
    ENV_API_KEY = []  # Ollama doesn't need an API key
    ENV_BASE_URL = "OLLAMA_BASE_URL"
    CATALOG_ID = "ollama"
    # No auth needed: the shared client builder substitutes this sentinel, so
    # Ollama gets the same size-capped, retry-free http client as every other
    # OpenAI-compatible provider instead of a bare AsyncOpenAI().
    ALLOW_NO_API_KEY = True
    NO_API_KEY_PLACEHOLDER = "ollama"

    def __init__(self):
        super().__init__(provider_id="ollama", name="Ollama")

    def is_configured(self) -> bool:
        """Ollama doesn't need an API key, just a running server."""
        return True

    def get_models(self):
        """Return models from flocks.json (_config_models) only.

        catalog.json is not consulted at runtime; it is only used when
        credentials are first saved to pre-populate flocks.json.
        """
        return list(getattr(self, "_config_models", []))
