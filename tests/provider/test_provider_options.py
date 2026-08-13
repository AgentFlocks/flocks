from flocks.provider import options as provider_options
from flocks.provider.interleaved import (
    REASONING_TRANSPORT_ANTHROPIC_MESSAGES,
    REASONING_TRANSPORT_GENERIC_CHAT,
)

DEEPSEEK_THINKING_EXTRA_BODY = {
    "thinking": {"type": "enabled"},
    "reasoning_effort": "high",
}
GLM_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled", "clear_thinking": False}}
KIMI_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled"}}
MIMO_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled"}}


class TestBuildProviderOptions:
    def test_claude_reasoning_can_be_disabled(self):
        options = provider_options.build_provider_options(
            "anthropic",
            "claude-sonnet-4-6",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert "thinking" not in options

    def test_threatbook_qwen_enables_thinking_by_default(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "qwen3.6-plus",
            resolve_max_tokens=False,
        )

        assert options["extra_body"]["enable_thinking"] is True

    def test_threatbook_qwen_respects_reasoning_toggle(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "qwen3.6-plus",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"]["enable_thinking"] is False

    def test_threatbook_kimi_hybrid_models_use_official_thinking_payload(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "kimi-k2.6",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_moonshot_kimi_hybrid_models_use_official_thinking_payload(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k2.6",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_moonshot_kimi_k27_forces_official_thinking_payload(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k2.7-code",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_moonshot_kimi_k27_highspeed_forces_official_thinking_payload(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k2.7-code-highspeed",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_moonshot_kimi_k3_uses_default_reasoning_effort(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "high"}

    def test_kimi_k27_forces_thinking_even_when_toggle_is_disabled(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "kimi-k2.7-code-highspeed",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_kimi_k27_overrides_configured_disabled_thinking(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_default_extra_body",
            lambda *_args: {
                "thinking": {"type": "disabled"},
                "custom_option": True,
            },
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "kimi-k2.7-code",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {
            "thinking": {"type": "enabled"},
            "custom_option": True,
        }

    def test_kimi_k3_uses_reasoning_effort_instead_of_thinking(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "high"}
        assert "thinking" not in options["extra_body"]

    def test_kimi_k3_respects_supported_reasoning_effort(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            reasoning_effort="low",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "low"}

    def test_kimi_k3_preserves_configured_extra_body_reasoning_effort(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            provider_options,
            "_resolve_default_extra_body",
            lambda *_args: {"reasoning_effort": "high"},
        )

        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "high"}

    def test_kimi_k3_explicit_reasoning_effort_overrides_configured_default(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            provider_options,
            "_resolve_default_extra_body",
            lambda *_args: {"reasoning_effort": "high"},
        )

        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            reasoning_effort="low",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "low"}

    def test_openai_compatible_qwen_models_enable_thinking_by_default(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "qwen3-235b-a22b-thinking",
            resolve_max_tokens=False,
        )

        assert options["extra_body"]["enable_thinking"] is True

    def test_openai_compatible_kimi_models_use_official_thinking_payload(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "placeholder": " ",
                "cross_provider_policy": "placeholder",
            },
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "kimi-k2-thinking-turbo",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_minimax_models_use_reasoning_split(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_details",
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            "minimax",
            "minimax-m3",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_split": True}

    def test_deepseek_models_use_official_thinking_payload(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "placeholder": " ",
                "cross_provider_policy": "placeholder",
            },
        )

        options = provider_options.build_provider_options(
            "deepseek",
            "deepseek-v4-pro",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == DEEPSEEK_THINKING_EXTRA_BODY

    def test_deepseek_v4_maps_xhigh_reasoning_effort_to_max(self):
        options = provider_options.build_provider_options(
            "deepseek",
            "deepseek-v4-flash",
            reasoning_effort="xhigh",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
        }

    def test_deepseek_effort_is_merged_with_configured_extra_body(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            provider_options,
            "_resolve_default_extra_body",
            lambda *_args: {"custom_option": True},
        )

        options = provider_options.build_provider_options(
            "deepseek",
            "deepseek-v4-pro",
            reasoning_effort="xhigh",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
            "custom_option": True,
        }

    def test_deepseek_models_emit_disabled_thinking_when_reasoning_disabled(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "placeholder": " ",
                "cross_provider_policy": "placeholder",
            },
        )

        options = provider_options.build_provider_options(
            "deepseek",
            "deepseek-v4-pro",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_glm_models_use_official_thinking_payload(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            "zhipu",
            "glm-4.7",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == GLM_THINKING_EXTRA_BODY

    def test_glm_models_emit_disabled_thinking_when_reasoning_disabled(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            "zhipu",
            "glm-4.7",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_mimo_models_use_official_thinking_payload(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "placeholder": " ",
                "cross_provider_policy": "placeholder",
            },
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "mimo-v2.5-pro",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == MIMO_THINKING_EXTRA_BODY

    def test_claude_thinking_depends_on_transport_not_capability(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "thinking",
                "echo": "tool_calls",
                "cross_provider_policy": "preserve",
            },
        )
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_transport",
            lambda *_args: REASONING_TRANSPORT_GENERIC_CHAT,
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "claude-sonnet-4-6",
            resolve_max_tokens=False,
        )

        assert "thinking" not in options

    def test_anthropic_transport_enables_claude_thinking(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args: {
                "field": "thinking",
                "echo": "tool_calls",
                "cross_provider_policy": "preserve",
            },
        )
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_transport",
            lambda *_args: REASONING_TRANSPORT_ANTHROPIC_MESSAGES,
        )

        options = provider_options.build_provider_options(
            "anthropic",
            "claude-sonnet-4-6",
            resolve_max_tokens=False,
        )

        assert options["thinking"]["type"] == "enabled"

    def test_kimi_hybrid_models_respect_explicit_reasoning_toggle(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "kimi-k2.5",
            reasoning_enabled=True,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_kimi_hybrid_models_emit_disabled_thinking_when_reasoning_disabled(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "kimi-k2.5",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_model_setting_enable_thinking_is_applied(self, monkeypatch):
        monkeypatch.setattr(provider_options, "_resolve_reasoning_enabled", lambda *_args: True)

        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k2.6",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == KIMI_THINKING_EXTRA_BODY

    def test_model_setting_enable_thinking_applies_without_interleaved(self, monkeypatch):
        monkeypatch.setattr(provider_options, "_resolve_reasoning_enabled", lambda *_args: True)
        monkeypatch.setattr(provider_options, "_resolve_interleaved_capability", lambda *_args: None)
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_transport",
            lambda *_args: REASONING_TRANSPORT_GENERIC_CHAT,
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "custom-thinking-model",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"enable_thinking": True}

    def test_openai_reasoning_can_be_disabled(self):
        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert "reasoningEffort" not in options

    def test_openai_reasoning_defaults_to_high(self):
        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            resolve_max_tokens=False,
        )

        assert options["reasoningEffort"] == "high"

    def test_openai_uses_configured_reasoning_effort(self, monkeypatch):
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_effort",
            lambda *_args: "low",
        )

        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            resolve_max_tokens=False,
        )

        assert options["reasoningEffort"] == "low"

    def test_openai_clamps_unsupported_global_reasoning_effort_for_model(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_effort",
            lambda *_args: "max",
        )

        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            resolve_max_tokens=False,
        )

        assert options["reasoningEffort"] == "xhigh"

    def test_openai_unknown_reasoning_model_omits_unverified_effort(self):
        options = provider_options.build_provider_options(
            "openai",
            "o3",
            reasoning_effort="max",
            resolve_max_tokens=False,
        )

        assert "reasoningEffort" not in options

    def test_openai_clamps_minimal_up_to_low_for_gpt_54(self):
        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            reasoning_effort="minimal",
            resolve_max_tokens=False,
        )

        assert options["reasoningEffort"] == "low"

    def test_azure_openai_uses_catalog_effort_map(self):
        options = provider_options.build_provider_options(
            "azure-openai",
            "gpt-5.4",
            reasoning_effort="max",
            resolve_max_tokens=False,
        )

        assert options["reasoningEffort"] == "xhigh"

    def test_claude_clamps_xhigh_up_to_max(self):
        options = provider_options.build_provider_options(
            "anthropic",
            "claude-sonnet-4-6",
            reasoning_effort="xhigh",
            resolve_max_tokens=False,
        )

        assert options["output_config"] == {"effort": "max"}

    def test_invalid_global_effort_falls_back_to_model_high(self):
        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            reasoning_effort="turbo",
            resolve_max_tokens=False,
        )

        assert options["reasoningEffort"] == "high"

    def test_unknown_kimi_k3_gateway_omits_unverified_effort(self):
        options = provider_options.build_provider_options(
            "openai-compatible",
            "kimi-k3",
            reasoning_effort="max",
            resolve_max_tokens=False,
        )

        assert "extra_body" not in options

    def test_toggle_only_models_accept_all_global_efforts(self):
        cases = (
            ("zhipu", "glm-5", GLM_THINKING_EXTRA_BODY),
            ("alibaba", "qwen3.5-flash-02-23", {"enable_thinking": True}),
            ("minimax", "minimax-m3", {"reasoning_split": True}),
            ("moonshot", "kimi-k2.6", KIMI_THINKING_EXTRA_BODY),
        )

        for provider_id, model_id, expected_extra_body in cases:
            for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
                options = provider_options.build_provider_options(
                    provider_id,
                    model_id,
                    reasoning_effort=effort,
                    resolve_max_tokens=False,
                )

                assert options["extra_body"] == expected_extra_body

    def test_kimi_k3_clamps_reasoning_effort_up_before_down(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            reasoning_effort="xhigh",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "max"}

    def test_kimi_k3_clamps_unsupported_global_medium_to_high(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_effort",
            lambda *_args: "medium",
        )

        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k3",
            resolve_max_tokens=False,
        )

        assert options["extra_body"] == {"reasoning_effort": "high"}
