"""Flocks Router OpenAI-compatible provider implementations."""

from flocks.provider.sdk.openai_base import OpenAIBaseProvider


class FlocksRouterTestLLMProvider(OpenAIBaseProvider):
    """LLM provider for the isolated Flocks Router test environment."""

    DEFAULT_BASE_URL = "https://flocks-router-test.threatbook-inc.cn/v1"
    ENV_API_KEY = ["FLOCKS_ROUTER_TEST_API_KEY"]
    ENV_BASE_URL = "FLOCKS_ROUTER_TEST_BASE_URL"
    CATALOG_ID = "flocks-router-test"

    def __init__(self):
        super().__init__(
            provider_id="flocks-router-test",
            name="Flocks Router Test",
        )
