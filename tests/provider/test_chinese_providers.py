"""
Tests for the curated provider model catalog.
"""

from flocks.provider.model_catalog import (
    get_provider_default_url,
    get_provider_meta,
    get_provider_model_definitions,
    get_provider_pricing_profile,
    get_raw_catalog,
    list_catalog_provider_ids,
)
from flocks.provider.provider import ModelCapabilities, ModelInfo, ProviderConfig
from flocks.provider.sdk.threatbook import ThreatBookCnLLMProvider


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

    def test_mainstream_reasoning_models_declare_thinking_level_maps(self):
        family_prefixes = (
            "gpt",
            "claude",
            "deepseek",
            "glm",
            "qwen",
            "kimi",
            "minimax",
        )
        missing = []

        for provider_id, provider in get_raw_catalog().items():
            for model_id, model in provider.get("models", {}).items():
                family = model.get("family", "").lower()
                if not family.startswith(family_prefixes):
                    continue
                if not model.get("capabilities", {}).get("thinking_level_map"):
                    missing.append(f"{provider_id}/{model_id}")

        assert missing == []

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
        assert gpt54.capabilities.thinking_level_map["xhigh"] == "xhigh"
        assert gpt54.capabilities.thinking_level_map["minimal"] is None
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
        assert opus.capabilities.thinking_level_map["max"] == "max"
        assert opus.capabilities.thinking_level_map["xhigh"] is None
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
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        }

        v4_flash = next(m for m in models if m.id == "deepseek-v4-flash")
        assert v4_flash.capabilities.supports_reasoning is True
        assert v4_flash.capabilities.interleaved["field"] == "reasoning_content"
        assert v4_flash.capabilities.thinking_level_map["xhigh"] == "max"

        v4_pro = next(m for m in models if m.id == "deepseek-v4-pro")
        assert v4_pro.capabilities.supports_reasoning is True
        assert v4_pro.capabilities.interleaved["field"] == "reasoning_content"

    def test_alibaba_catalog(self):
        meta = get_provider_meta("alibaba")
        assert meta is not None
        assert meta.name == "阿里云百炼 (Alibaba)"

        models = get_provider_model_definitions("alibaba")
        assert {m.id for m in models} == {
            "qwen3.8-max",
            "qwen3.7-plus",
            "qwen3.7-max",
            "qwen3.7-flash",
        }

        qwen38 = next(m for m in models if m.id == "qwen3.8-max")
        assert qwen38.capabilities.supports_vision is True
        assert qwen38.capabilities.supports_reasoning is True
        assert qwen38.capabilities.interleaved["field"] == "reasoning_content"
        assert qwen38.limits.context_window == 1000000
        assert qwen38.limits.max_output_tokens == 65536
        assert qwen38.pricing.currency == "CNY"

        max_model = next(m for m in models if m.id == "qwen3.7-max")
        assert max_model.capabilities.supports_reasoning is True
        assert max_model.capabilities.interleaved["field"] == "reasoning_content"
        assert max_model.limits.context_window == 1000000

        plus = next(m for m in models if m.id == "qwen3.7-plus")
        assert plus.capabilities.supports_vision is True
        assert plus.capabilities.supports_reasoning is True

        flash = next(m for m in models if m.id == "qwen3.7-flash")
        assert flash.capabilities.supports_vision is True
        assert flash.capabilities.supports_reasoning is True
        assert flash.capabilities.thinking_level_map == {"high": "enabled"}
        assert flash.capabilities.interleaved["field"] == "reasoning_content"
        assert flash.limits.context_window == 1000000
        assert flash.limits.max_output_tokens == 65536
        assert flash.pricing.currency == "CNY"

    def test_moonshot_catalog(self):
        models = get_provider_model_definitions("moonshot")
        assert {m.id for m in models} == {
            "kimi-k3",
            "kimi-k2.7-code",
            "kimi-k2.7-code-highspeed",
            "kimi-k2.5",
            "kimi-k2.6",
        }

        k3 = next(m for m in models if m.id == "kimi-k3")
        assert k3.capabilities.supports_vision is True
        assert k3.capabilities.supports_reasoning is True
        assert k3.capabilities.interleaved["field"] == "reasoning_content"
        assert k3.capabilities.thinking_level_map["low"] == "low"
        assert k3.capabilities.thinking_level_map["medium"] is None
        assert k3.pricing.input == 20.0
        assert k3.pricing.output == 100.0
        assert k3.pricing.cache_read == 2.0
        assert k3.limits.context_window == 1048576
        assert k3.limits.max_output_tokens == 131072

        k27 = next(m for m in models if m.id == "kimi-k2.7-code")
        assert k27.capabilities.supports_vision is True
        assert k27.capabilities.supports_reasoning is True
        assert k27.capabilities.interleaved["field"] == "reasoning_content"
        assert k27.pricing.input == 6.5
        assert k27.pricing.output == 27.0
        assert k27.pricing.cache_read == 1.3
        assert k27.limits.context_window == 262144
        assert k27.limits.max_output_tokens == 32768

        k27_highspeed = next(m for m in models if m.id == "kimi-k2.7-code-highspeed")
        assert k27_highspeed.capabilities.supports_vision is True
        assert k27_highspeed.capabilities.supports_reasoning is True
        assert k27_highspeed.capabilities.interleaved["field"] == "reasoning_content"
        assert k27_highspeed.pricing.input == 13.0
        assert k27_highspeed.pricing.output == 54.0
        assert k27_highspeed.pricing.cache_read == 2.6
        assert k27_highspeed.limits.context_window == 262144
        assert k27_highspeed.limits.max_output_tokens == 32768

        k26 = next(m for m in models if m.id == "kimi-k2.6")
        assert k26.capabilities.supports_vision is True
        assert k26.capabilities.supports_reasoning is True
        assert k26.capabilities.interleaved["field"] == "reasoning_content"
        assert k26.capabilities.interleaved["placeholder"] == " "
        assert k26.pricing.currency == "CNY"
        assert k26.pricing.cache_read == 1.3
        assert k26.limits.context_window == 256000

        k25 = next(m for m in models if m.id == "kimi-k2.5")
        assert k25.capabilities.supports_reasoning is True
        assert k25.capabilities.interleaved["field"] == "reasoning_content"

    def test_zhipu_catalog(self):
        models = get_provider_model_definitions("zhipu")
        assert {m.id for m in models} == {
            "glm-5",
            "glm-4.7",
            "glm-5-turbo",
        }

        turbo = next(m for m in models if m.id == "glm-5-turbo")
        assert turbo.capabilities.interleaved["field"] == "reasoning_content"
        assert turbo.pricing.output == 26.0
        assert turbo.limits.context_window == 202752
        glm47 = next(m for m in models if m.id == "glm-4.7")
        assert glm47.capabilities.supports_reasoning is True
        assert glm47.capabilities.interleaved["field"] == "reasoning_content"
        assert glm47.capabilities.thinking_level_map == {"high": "enabled"}

    def test_minimax_catalog(self):
        models = get_provider_model_definitions("minimax")
        assert {m.id for m in models} == {
            "minimax-m3",
            "minimax-m2.7",
            "minimax-m2.5",
        }
        m3 = next(m for m in models if m.id == "minimax-m3")
        assert m3.capabilities.supports_reasoning is True
        assert m3.capabilities.interleaved["field"] == "reasoning_details"
        assert m3.capabilities.thinking_level_map == {"high": "adaptive"}
        assert m3.limits.context_window == 1000000
        assert m3.limits.max_output_tokens == 128000
        m27 = next(m for m in models if m.id == "minimax-m2.7")
        assert m27.capabilities.supports_reasoning is True
        assert m27.capabilities.interleaved["field"] == "reasoning_details"
        assert m27.limits.context_window == 196608
        assert m27.limits.max_output_tokens == 128000
        m25 = next(m for m in models if m.id == "minimax-m2.5")
        assert m25.limits.context_window == 196608
        assert m25.limits.max_output_tokens == 128000

    def test_stepfun_catalog(self):
        models = get_provider_model_definitions("stepfun")
        assert len(models) == 1
        model = models[0]
        assert model.id == "step-3.5-flash"
        assert model.capabilities.supports_reasoning is True
        assert model.capabilities.interleaved["field"] == "reasoning_content"
        assert model.pricing.currency == "CNY"
        assert model.limits.max_output_tokens == 256000

    def test_threatbook_cn_llm_catalog(self):
        meta = get_provider_meta("threatbook-cn-llm")
        assert meta is not None
        assert "THREATBOOK_CN_LLM_API_KEY" in meta.env_vars
        assert get_provider_default_url("threatbook-cn-llm") == "https://llm.threatbook.cn/v1"
        models = get_provider_model_definitions("threatbook-cn-llm")
        assert {m.id for m in models} == {
            "kimi-k2.7-code",
            "minimax-m3",
            "minimax-m2.7",
            "minimax-m2.5",
            "GLM-5",
            "qwen3.6-plus",
            "qwen3-max",
            "kimi-k2.6",
            "deepseek-v4-flash",
            "deepseek-v4-flash-0731",
        }

        assert models[0].id == "deepseek-v4-flash-0731"
        kimi_code = next(m for m in models if m.id == "kimi-k2.7-code")
        assert kimi_code.capabilities.supports_vision is True
        assert kimi_code.capabilities.supports_reasoning is True
        assert kimi_code.capabilities.interleaved["field"] == "reasoning_content"
        assert kimi_code.pricing.currency == "CNY"
        assert kimi_code.pricing.cache_read == 1.3
        assert kimi_code.pricing.input == 6.5
        assert kimi_code.pricing.output == 27.0
        assert kimi_code.limits.context_window == 256000
        assert kimi_code.limits.max_input_tokens == 224000
        assert kimi_code.limits.max_output_tokens == 16000

        qwen = next(m for m in models if m.id == "qwen3.6-plus")
        assert qwen.capabilities.supports_vision is True

        m3 = next(m for m in models if m.id == "minimax-m3")
        assert m3.capabilities.supports_vision is True
        assert m3.capabilities.supports_reasoning is True
        assert m3.capabilities.interleaved["field"] == "reasoning_details"
        router_m3 = get_provider_pricing_profile(
            "threatbook-cn-llm",
            "router",
            "minimax-m3",
        )
        assert router_m3 is not None
        assert router_m3.price_version == "2026061601"
        assert router_m3.price_tiers is not None
        assert [tier.max_input_tokens for tier in router_m3.price_tiers] == [
            512000,
            None,
        ]
        assert [
            (tier.input, tier.output) for tier in router_m3.price_tiers
        ] == [(4.2, 16.8), (8.4, 33.6)]

        flash_cn = next(m for m in models if m.id == "deepseek-v4-flash")
        assert flash_cn.pricing.input == 1.0
        assert flash_cn.pricing.output == 2.0
        assert flash_cn.pricing.currency == "CNY"
        assert flash_cn.limits.context_window == 1000000
        assert flash_cn.limits.max_output_tokens == 384000
        raw_models = get_raw_catalog()["threatbook-cn-llm"]["models"]
        assert raw_models["deepseek-v4-flash-0731"] == {
            **raw_models["deepseek-v4-flash"],
            "name": "deepseek-v4-flash-0731",
            "limits": {
                **raw_models["deepseek-v4-flash"]["limits"],
                "max_input_tokens": 1000000,
            },
            "pricing": {
                **raw_models["deepseek-v4-flash"]["pricing"],
                "cache_read": 0.2,
            },
        }

        # Router pricing is synchronized onto the existing dev model set only;
        # model discovery/naming remains governed by the local catalog.
        expected_router_prices = {
            "GLM-5": (4.0, 18.0, "2026061601"),
            "minimax-m2.7": (2.1, 8.4, "2026061601"),
            "minimax-m2.5": (2.1, 8.4, "2026061601"),
            "qwen3.6-plus": (2.0, 12.0, "2026061601"),
            "qwen3-max": (2.5, 10.0, "2026061601"),
            "kimi-k2.6": (6.5, 27.0, "2026061601"),
            "deepseek-v4-flash": (1.0, 2.0, "2026061601"),
            "minimax-m3": (4.2, 16.8, "2026061601"),
            "kimi-k2.7-code": (6.5, 27.0, "2026072001"),
            "deepseek-v4-flash-0731": (1.0, 2.0, "2026080301"),
        }
        assert set(expected_router_prices) == {model.id for model in models}
        assert set(
            get_raw_catalog()["threatbook-cn-llm"]["pricing_profiles"]["router"]
        ) == set(expected_router_prices)
        for model in models:
            expected_input, expected_output, expected_version = (
                expected_router_prices[model.id]
            )
            router_pricing = get_provider_pricing_profile(
                "threatbook-cn-llm",
                "router",
                model.id,
            )
            assert router_pricing is not None
            assert router_pricing.input == expected_input
            assert router_pricing.output == expected_output
            assert router_pricing.price_version == expected_version
            assert router_pricing.cache_read is None
            assert router_pricing.cache_read_uses_input is True
            assert router_pricing.reasoning_uses_output is True
            assert router_pricing.cost_rounding_places == 6

        kimi = next(m for m in models if m.id == "kimi-k2.6")
        assert kimi.capabilities.supports_vision is True
        assert kimi.capabilities.supports_reasoning is True
        assert kimi.capabilities.interleaved["field"] == "reasoning_content"
        assert kimi.pricing.currency == "CNY"
        assert kimi.pricing.cache_read == 1.3
        assert kimi.pricing.input == 6.5
        assert kimi.pricing.output == 27.0
        assert kimi.limits.context_window == 256000
        assert kimi.limits.max_input_tokens == 224000
        assert kimi.limits.max_output_tokens == 16000

    def test_threatbook_cn_selects_price_profile_by_key_type(self):
        configured_model = ModelInfo(
            id="minimax-m3",
            name="minimax-m3",
            provider_id="threatbook-cn-llm",
            capabilities=ModelCapabilities(),
            pricing={
                "input": 4.23,
                "output": 16.8,
                "cache_read": 0.5,
                "currency": "CNY",
            },
        )

        legacy = ThreatBookCnLLMProvider()
        legacy._config_models = [configured_model]
        legacy.configure(ProviderConfig(
            provider_id=legacy.id,
            api_key="legacy-test-key",
            base_url="https://llm.threatbook.cn/v1",
        ))
        legacy_price = legacy.get_model_definitions()[0].pricing
        assert legacy_price is not None
        assert legacy_price.input == 4.23
        assert legacy_price.cache_read == 0.5
        assert legacy_price.price_tiers is None

        router = ThreatBookCnLLMProvider()
        router._config_models = [configured_model]
        router.configure(ProviderConfig(
            provider_id=router.id,
            api_key="fr_test-key",
            base_url="https://llm.threatbook.cn/v1",
        ))
        router_price = router.get_model_definitions()[0].pricing
        assert router_price is not None
        assert router_price.input == 4.2
        assert router_price.output == 16.8
        assert router_price.cache_read is None
        assert router_price.cache_read_uses_input is True
        assert router_price.reasoning_uses_output is True
        assert router_price.cost_rounding_places == 6
        assert router_price.price_version == "2026061601"
        assert router_price.price_tiers is not None
        assert router_price.price_tiers[1].input == 8.4
        assert router._config.base_url == "https://llm.threatbook.cn/v1"

    def test_threatbook_io_llm_catalog(self):
        meta = get_provider_meta("threatbook-io-llm")
        assert meta is not None
        assert "THREATBOOK_IO_LLM_API_KEY" in meta.env_vars
        assert get_provider_default_url("threatbook-io-llm") == "https://llm.threatbook.io/v1"
        models = get_provider_model_definitions("threatbook-io-llm")
        assert {m.id for m in models} == {
            "kimi-k2.7-code",
            "minimax-m3",
            "minimax-m2.7",
            "minimax-m2.5",
            "GLM-5",
            "qwen3.6-plus",
            "qwen3-max",
            "deepseek-v4-flash",
            "deepseek-v4-flash-0731",
        }

        assert models[0].id == "deepseek-v4-flash-0731"
        kimi_code = next(m for m in models if m.id == "kimi-k2.7-code")
        assert kimi_code.capabilities.supports_vision is True
        assert kimi_code.capabilities.supports_reasoning is True
        assert kimi_code.capabilities.interleaved["field"] == "reasoning_content"
        assert kimi_code.pricing.currency == "CNY"
        assert kimi_code.pricing.cache_read == 1.3
        assert kimi_code.pricing.input == 6.5
        assert kimi_code.pricing.output == 27.0
        assert kimi_code.limits.context_window == 256000
        assert kimi_code.limits.max_input_tokens == 224000
        assert kimi_code.limits.max_output_tokens == 16000

        qwen = next(m for m in models if m.id == "qwen3.6-plus")
        assert qwen.capabilities.supports_vision is True

        flash_io = next(m for m in models if m.id == "deepseek-v4-flash")
        assert flash_io.pricing.input == 1.0
        assert flash_io.pricing.output == 2.0
        assert flash_io.pricing.currency == "CNY"
        assert flash_io.limits.context_window == 1000000
        assert flash_io.limits.max_output_tokens == 384000
        raw_models = get_raw_catalog()["threatbook-io-llm"]["models"]
        assert raw_models["deepseek-v4-flash-0731"] == {
            **raw_models["deepseek-v4-flash"],
            "name": "deepseek-v4-flash-0731",
            "limits": {
                **raw_models["deepseek-v4-flash"]["limits"],
                "max_input_tokens": 1000000,
            },
            "pricing": {
                **raw_models["deepseek-v4-flash"]["pricing"],
                "cache_read": 0.2,
            },
        }

        m27 = next(m for m in models if m.id == "minimax-m2.7")
        assert m27.capabilities.interleaved["field"] == "reasoning_details"
        assert m27.pricing.currency == "CNY"
        assert m27.pricing.input == 2.1
        assert m27.limits.context_window == 196608
