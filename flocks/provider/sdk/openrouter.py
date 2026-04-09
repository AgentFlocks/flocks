"""
OpenRouter provider implementation

Based on @openrouter/ai-sdk-provider from Flocks's bundled providers
Multi-model routing provider
"""

from typing import List

from flocks.provider.provider import (
    ModelInfo,
    ModelCapabilities,
)
from flocks.provider.sdk.openai_base import OpenAIBaseProvider


class OpenRouterProvider(OpenAIBaseProvider):
    """OpenRouter provider - multi-model routing"""

    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    ENV_API_KEY = ["OPENROUTER_API_KEY"]
    ENV_BASE_URL = "OPENROUTER_BASE_URL"

    def __init__(self):
        super().__init__(provider_id="openrouter", name="OpenRouter")

    def _get_client(self):
        """Get or create AsyncOpenAI client with OpenRouter-specific headers."""
        if self._client is None:
            from openai import AsyncOpenAI

            api_key = self._config.api_key if self._config else self._api_key
            if not api_key:
                env_hint = self.ENV_API_KEY[0] if self.ENV_API_KEY else "API_KEY"
                raise ValueError(
                    f"{self.name} API key not configured. Set {env_hint}."
                )

            base_url = (
                self._config.base_url
                if self._config and self._config.base_url
                else self._base_url
            )

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                default_headers={
                    "HTTP-Referer": "https://opencode.ai/",
                    "X-Title": "opencode",
                },
            )
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Return user-configured models from flocks.json when available.

        Falls back to a set of popular OpenRouter models so the provider
        works out-of-the-box without any flocks.json configuration.
        """
        config_models = list(getattr(self, "_config_models", []))
        if config_models:
            return config_models

        return [
            ModelInfo(
                id="anthropic/claude-3.5-sonnet",
                name="Claude 3.5 Sonnet",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=True,
                    max_tokens=8192,
                    context_window=200000,
                ),
            ),
            ModelInfo(
                id="openai/gpt-4-turbo",
                name="GPT-4 Turbo",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=True,
                    max_tokens=4096,
                    context_window=128000,
                ),
            ),
            ModelInfo(
                id="google/gemini-pro-1.5",
                name="Gemini Pro 1.5",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=True,
                    max_tokens=8192,
                    context_window=1000000,
                ),
            ),
            ModelInfo(
                id="meta-llama/llama-3.1-70b-instruct",
                name="Llama 3.1 70B",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=False,
                    max_tokens=4096,
                    context_window=131072,
                ),
            ),
            ModelInfo(
                id="mistralai/mistral-large",
                name="Mistral Large",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=False,
                    max_tokens=8192,
                    context_window=128000,
                ),
            ),
        ]
