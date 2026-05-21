from flocks.provider import options as provider_options
from flocks.provider.interleaved import (
    REASONING_TRANSPORT_ANTHROPIC_MESSAGES,
    REASONING_TRANSPORT_GENERIC_CHAT,
)


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

    def test_threatbook_kimi_hybrid_models_enable_thinking_by_default(self):
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "kimi-k2.6",
            resolve_max_tokens=False,
        )

        assert options["extra_body"]["enable_thinking"] is True

    def test_moonshot_kimi_hybrid_models_enable_thinking_by_default(self):
        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k2.6",
            resolve_max_tokens=False,
        )

        assert options["extra_body"]["enable_thinking"] is True

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

    def test_openai_compatible_kimi_models_enable_thinking_by_default(self, monkeypatch):
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

        assert options["extra_body"]["enable_thinking"] is True

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

        assert options["extra_body"]["enable_thinking"] is True

    def test_model_setting_enable_thinking_is_applied(self, monkeypatch):
        monkeypatch.setattr(provider_options, "_resolve_reasoning_enabled", lambda *_args: True)

        options = provider_options.build_provider_options(
            "moonshot",
            "kimi-k2.6",
            resolve_max_tokens=False,
        )

        assert options["extra_body"]["enable_thinking"] is True

    def test_openai_reasoning_can_be_disabled(self):
        options = provider_options.build_provider_options(
            "openai",
            "gpt-5.4",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )

        assert "reasoningEffort" not in options
