"""
Alibaba Cloud Model Studio (阿里云百炼) provider implementation.

DashScope provides OpenAI-compatible API.
Docs: https://help.aliyun.com/zh/model-studio/developer-reference/compatibility-of-openai-with-dashscope
"""

from flocks.provider.sdk.openai_base import OpenAIBaseProvider


class AlibabaProvider(OpenAIBaseProvider):
    """Alibaba Cloud Model Studio provider (OpenAI-compatible via DashScope).

    Models are loaded from catalog.json (CATALOG_ID = "alibaba") and
    user-added custom models from flocks.json by the parent
    OpenAIBaseProvider.get_models().
    """

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ENV_API_KEY = ["DASHSCOPE_API_KEY", "ALIBABA_API_KEY"]
    ENV_BASE_URL = "DASHSCOPE_BASE_URL"
    CATALOG_ID = "alibaba"

    def __init__(self):
        super().__init__(provider_id="alibaba", name="阿里云百炼 (Alibaba)")
