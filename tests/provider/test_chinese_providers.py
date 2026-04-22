"""
Tests for the curated provider model catalog.
"""

from flocks.provider.model_catalog import (
    get_provider_default_url,
    get_provider_meta,
    get_provider_model_definitions,
    list_catalog_provider_ids,
)


class TestCuratedCatalogProviders:
    """Verify provider-level catalog curation."""

    def test_provider_ids_match_curated_list(self):
        assert set(list_catalog_provider_ids()) == {
            "openai-compatible",
            "threatbook-cn-llm",
            "threatbook-io-llm",
            "google",
            "openai",
            "anthropic",
            "xai",
            "cohere",
            "azure-openai",
            "deepseek",
            "alibaba",
            "moonshot",
            "zhipu",
            "minimax",
            "stepfun",
            "cherry",
        }

    def test_removed_provider_ids_are_absent(self):
        for provider_id in {
            "mistral",
            "groq",
            "together",
            "siliconflow",
            "volcengine",
            "tencent",
            "baichuan",
            "yi",
            "ollama",
        }:
            assert get_provider_meta(provider_id) is None
            assert get_provider_model_definitions(provider_id) == []


class TestCuratedCatalogModels:
    """Verify key models, pricing, and limits."""

    def test_openai_compatible_catalog(self):
        meta = get_provider_meta("openai-compatible")
        assert meta is not None
        assert meta.id == "openai-compatible"
        assert get_provider_model_definitions("openai-compatible") == []

    def test_google_catalog(self):
        meta = get_provider_meta("google")
        assert meta is not None
        assert "GOOGLE_API_KEY" in meta.env_vars

        models = get_provider_model_definitions("google")
        ids = {m.id for m in models}
        assert ids == {
            "gemini-3.1-pro-preview",
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
        }

        pro_preview = next(m for m in models if m.id == "gemini-3.1-pro-preview")
        assert pro_preview.limits.context_window == 1048576
        assert pro_preview.pricing.output == 12.0

    def test_openai_catalog(self):
        models = get_provider_model_definitions("openai")
        ids = {m.id for m in models}
        assert ids == {
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2",
            "gpt-5-mini",
        }

        gpt54 = next(m for m in models if m.id == "gpt-5.4")
        assert gpt54.capabilities.supports_reasoning is True
        assert gpt54.limits.max_output_tokens == 1050000
        assert gpt54.pricing.input == 2.5

    def test_anthropic_catalog(self):
        meta = get_provider_meta("anthropic")
        assert meta is not None
        assert "ANTHROPIC_API_KEY" in meta.env_vars

        models = get_provider_model_definitions("anthropic")
        ids = {m.id for m in models}
        assert ids == {"claude-sonnet-4-6", "claude-opus-4-6"}

        opus = next(m for m in models if m.id == "claude-opus-4-6")
        assert opus.capabilities.supports_vision is True
        assert opus.pricing.output == 25.0

    def test_xai_catalog(self):
        models = get_provider_model_definitions("xai")
        assert {m.id for m in models} == {
            "grok-4.1-fast",
            "grok-4.20-beta",
            "grok-4.20-multi-agent-beta",
            "grok-4",
        }

        grok_fast = next(m for m in models if m.id == "grok-4.1-fast")
        assert grok_fast.limits.context_window == 2000000
        assert grok_fast.pricing.output == 0.5

    def test_cohere_catalog(self):
        models = get_provider_model_definitions("cohere")
        assert {m.id for m in models} == {
            "command-r-08-2024",
            "command-r-plus-08-2024",
            "command-r7b-12-2024",
        }

    def test_azure_openai_catalog(self):
        models = get_provider_model_definitions("azure-openai")
        assert {m.id for m in models} == {
            "gpt-5.4",
            "gpt-5.3-codex",
            "gpt-5.2",
            "gpt-5-mini",
        }

    def test_deepseek_catalog(self):
        meta = get_provider_meta("deepseek")
        assert meta is not None
        assert "DEEPSEEK_API_KEY" in meta.env_vars

        models = get_provider_model_definitions("deepseek")
        assert {m.id for m in models} == {
            "deepseek-chat",
            "deepseek-reasoner",
        }

        r1 = next(m for m in models if m.id == "deepseek-reasoner")
        assert r1.capabilities.supports_reasoning is True
        assert r1.pricing.currency == "CNY"
        assert r1.pricing.output == 16.0

    def test_alibaba_catalog(self):
        models = get_provider_model_definitions("alibaba")
        assert {m.id for m in models} == {
            "qwen3-235b-a22b-2507",
            "qwen3.5-flash-02-23",
        }

        flash = next(m for m in models if m.id == "qwen3.5-flash-02-23")
        assert flash.limits.context_window == 1000000
        assert flash.pricing.currency == "CNY"

    def test_moonshot_catalog(self):
        models = get_provider_model_definitions("moonshot")
        assert {m.id for m in models} == {
            "kimi-k2.5",
            "kimi-k2.6",
            "kimi-k2-thinking",
            "kimi-k2",
        }

        k26 = next(m for m in models if m.id == "kimi-k2.6")
        assert k26.capabilities.supports_reasoning is True
        assert k26.pricing.currency == "CNY"
        assert k26.pricing.cache_read == 1.3
        assert k26.limits.context_window == 256000

        thinking = next(m for m in models if m.id == "kimi-k2-thinking")
        assert thinking.capabilities.supports_reasoning is True

    def test_zhipu_catalog(self):
        models = get_provider_model_definitions("zhipu")
        assert {m.id for m in models} == {
            "glm-5",
            "glm-4.7",
            "glm-5-turbo",
        }

        turbo = next(m for m in models if m.id == "glm-5-turbo")
        assert turbo.pricing.output == 26.0
        assert turbo.limits.context_window == 202752

    def test_minimax_catalog(self):
        models = get_provider_model_definitions("minimax")
        assert {m.id for m in models} == {
            "minimax-m2.7",
            "minimax-m2.5",
        }

    def test_stepfun_catalog(self):
        models = get_provider_model_definitions("stepfun")
        assert len(models) == 1
        model = models[0]
        assert model.id == "step-3.5-flash"
        assert model.pricing.currency == "CNY"
        assert model.limits.max_output_tokens == 256000

    def test_threatbook_cn_llm_catalog(self):
        meta = get_provider_meta("threatbook-cn-llm")
        assert meta is not None
        assert "THREATBOOK_CN_LLM_API_KEY" in meta.env_vars
        assert get_provider_default_url("threatbook-cn-llm") == "https://llm.threatbook.cn/v1"
        models = get_provider_model_definitions("threatbook-cn-llm")
        assert {m.id for m in models} == {
            "minimax-m2.7",
            "minimax-m2.5",
            "GLM-5",
            "qwen3.6-plus",
            "qwen3-max",
            "kimi-k2.6",
        }

        kimi = next(m for m in models if m.id == "kimi-k2.6")
        assert kimi.capabilities.supports_reasoning is True
        assert kimi.pricing.currency == "CNY"
        assert kimi.pricing.cache_read == 1.3
        assert kimi.pricing.input == 6.5
        assert kimi.pricing.output == 27.0
        assert kimi.limits.context_window == 256000
        assert kimi.limits.max_input_tokens == 224000
        assert kimi.limits.max_output_tokens == 16000

    def test_threatbook_io_llm_catalog(self):
        meta = get_provider_meta("threatbook-io-llm")
        assert meta is not None
        assert "THREATBOOK_IO_LLM_API_KEY" in meta.env_vars
        assert get_provider_default_url("threatbook-io-llm") == "https://llm.threatbook.io/v1"
        models = get_provider_model_definitions("threatbook-io-llm")
        assert {m.id for m in models} == {
            "minimax-m2.7",
            "minimax-m2.5",
            "GLM-5",
            "qwen3.6-plus",
            "qwen3-max",
        }

        m27 = next(m for m in models if m.id == "minimax-m2.7")
        assert m27.pricing.currency == "CNY"
        assert m27.pricing.input == 2.1
        assert m27.limits.context_window == 196608
