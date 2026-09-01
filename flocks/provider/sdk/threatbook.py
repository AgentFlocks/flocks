"""
ThreatBook LLM provider implementations.

ThreatBook provides OpenAI-compatible endpoints for accessing hosted models.
Models are loaded from catalog.json and user-added custom models from
flocks.json by the parent OpenAIBaseProvider.get_models().
"""

from flocks.provider.sdk.openai_base import OpenAIBaseProvider


class ThreatBookCnLLMProvider(OpenAIBaseProvider):
    """ThreatBook-China LLM provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://llm.threatbook.cn/v1"
    ENV_API_KEY = ["THREATBOOK_CN_LLM_API_KEY"]
    ENV_BASE_URL = "THREATBOOK_CN_LLM_BASE_URL"
    CATALOG_ID = "threatbook-cn-llm"

    def __init__(self):
        super().__init__(provider_id="threatbook-cn-llm", name="ThreatBook-cn-llm")


class ThreatBookIoLLMProvider(OpenAIBaseProvider):
    """ThreatBook international LLM provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://llm.threatbook.io/v1"
    ENV_API_KEY = ["THREATBOOK_IO_LLM_API_KEY"]
    ENV_BASE_URL = "THREATBOOK_IO_LLM_BASE_URL"
    CATALOG_ID = "threatbook-io-llm"

    def __init__(self):
        super().__init__(provider_id="threatbook-io-llm", name="ThreatBook-io-llm")
