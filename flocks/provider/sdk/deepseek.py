"""
DeepSeek provider implementation.

DeepSeek provides OpenAI-compatible API with additional reasoning_content support.
Docs: https://platform.deepseek.com/api-docs

Reasoning extraction and streaming are handled by the base class
(OpenAIBaseProvider) via the shared extract_reasoning_content() utility.
"""

from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.openai_base import OpenAIBaseProvider, format_openai_messages


class DeepSeekProvider(OpenAIBaseProvider):
    """DeepSeek provider (OpenAI-compatible) with reasoning support.

    Inherits chat() and chat_stream() from OpenAIBaseProvider.
    Reasoning content (e.g. DeepSeek R1) is automatically extracted
    by the base class.

    Models are loaded from catalog.json (CATALOG_ID = "deepseek") and
    user-added custom models from flocks.json by the parent
    OpenAIBaseProvider.get_models().
    """

    DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
    ENV_API_KEY = ["DEEPSEEK_API_KEY"]
    ENV_BASE_URL = "DEEPSEEK_BASE_URL"
    CATALOG_ID = "deepseek"

    def __init__(self):
        super().__init__(provider_id="deepseek", name="DeepSeek")

    @staticmethod
    def _format_messages(messages: list[ChatMessage]) -> list:
        """DeepSeek requires assistant reasoning_content on replayed tool turns."""
        return format_openai_messages(
            messages,
            include_reasoning=True,
            reasoning_field="reasoning_content",
        )
