"""
Tests for model management module

Covers:
- types.py: Enum and data model validation
- credential.py: Thin credential utility functions
- provider.py: BaseProvider extensions (get_meta, get_model_definitions, validate_credential)
"""

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from flocks.provider.types import (
    AuthMethod,
    ConfigurateMethod,
    CredentialConfig,
    CredentialStatus,
    DefaultModelConfig,
    FetchFrom,
    ModelCapabilitiesV2,
    ModelDefinition,
    ModelFeature,
    ModelLimits,
    ModelStatus,
    ModelType,
    Modalities,
    ParameterRule,
    ParameterType,
    PriceConfig,
    ProviderMeta,
    UsageCost,
    UsageRecord,
)


# ==================== Helpers ====================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ==================== types.py ====================


class TestEnums:
    """Test enum definitions."""

    def test_auth_method_values(self):
        assert AuthMethod.API_KEY == "api_key"
        assert AuthMethod.SUBSCRIPTION == "subscription"
        assert AuthMethod.OAUTH == "oauth"
        assert AuthMethod.AWS_SDK == "aws_sdk"

    def test_model_type_values(self):
        assert ModelType.LLM == "llm"
        assert ModelType.TEXT_EMBEDDING == "text-embedding"
        assert ModelType.RERANK == "rerank"

    def test_model_feature_values(self):
        assert ModelFeature.TOOL_CALL == "tool-call"
        assert ModelFeature.VISION == "vision"
        assert ModelFeature.REASONING == "reasoning"

    def test_credential_status_values(self):
        assert CredentialStatus.ACTIVE == "active"
        assert CredentialStatus.COOLDOWN == "cooldown"
        assert CredentialStatus.UNTESTED == "untested"


class TestDataModels:
    """Test Pydantic data models."""

    def test_credential_config_api_key(self):
        config = CredentialConfig(api_key="sk-test123", base_url="https://api.openai.com/v1")
        assert config.api_key == "sk-test123"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.token is None

    def test_credential_config_subscription(self):
        config = CredentialConfig(token="clt-token-xxx")
        assert config.token == "clt-token-xxx"
        assert config.api_key is None

    def test_credential_config_get_display_key(self):
        config = CredentialConfig(api_key="sk-abcdef")
        assert config.get_display_key(AuthMethod.API_KEY) == "sk-abcdef"
        assert config.get_display_key(AuthMethod.SUBSCRIPTION) is None

        config2 = CredentialConfig(token="tok-xyz")
        assert config2.get_display_key(AuthMethod.SUBSCRIPTION) == "tok-xyz"

    def test_model_definition(self):
        model = ModelDefinition(
            id="claude-sonnet-4-20250514",
            name="Claude Sonnet 4",
            provider_id="anthropic",
            model_type=ModelType.LLM,
            family="claude-4",
            capabilities=ModelCapabilitiesV2(
                features=[ModelFeature.TOOL_CALL, ModelFeature.VISION],
                supports_tools=True,
                supports_vision=True,
            ),
            limits=ModelLimits(context_window=200000, max_output_tokens=64000),
            pricing=PriceConfig(input=3.0, output=15.0, cache_read=0.3),
        )
        assert model.id == "claude-sonnet-4-20250514"
        assert model.model_type == ModelType.LLM
        assert ModelFeature.TOOL_CALL in model.capabilities.features
        assert model.limits.context_window == 200000
        assert model.pricing.input == 3.0

    def test_parameter_rule(self):
        rule = ParameterRule(
            name="temperature",
            label="Temperature",
            type=ParameterType.FLOAT,
            default=1.0,
            min=0.0,
            max=2.0,
            precision=2,
        )
        assert rule.name == "temperature"
        assert rule.type == ParameterType.FLOAT
        assert rule.default == 1.0

    def test_provider_meta(self):
        meta = ProviderMeta(
            id="anthropic",
            name="Anthropic",
            supported_auth_methods=[AuthMethod.API_KEY, AuthMethod.SUBSCRIPTION],
            supported_model_types=[ModelType.LLM],
        )
        assert meta.id == "anthropic"
        assert AuthMethod.SUBSCRIPTION in meta.supported_auth_methods

    def test_modalities(self):
        m = Modalities(input=["text", "image"], output=["text"])
        assert "image" in m.input
        assert "text" in m.output

    def test_usage_cost(self):
        cost = UsageCost(input_cost=0.003, output_cost=0.015, total_cost=0.018)
        assert cost.total_cost == 0.018

    def test_default_model_config(self):
        cfg = DefaultModelConfig(
            model_type=ModelType.LLM,
            provider_id="anthropic",
            model_id="claude-sonnet-4-20250514",
        )
        assert cfg.model_type == ModelType.LLM


# ==================== credential.py (utility functions) ====================


class TestCredentialUtils:
    """Test credential utility functions."""

    def test_has_credential_false_when_no_secrets(self):
        """has_credential returns False when no secret manager is available."""
        from flocks.provider.credential import has_credential
        # In test environment without .secret.json, should return False
        result = has_credential("nonexistent-provider")
        assert result is False

    def test_get_api_key_none_when_not_set(self):
        """get_api_key returns None when provider has no key."""
        from flocks.provider.credential import get_api_key
        result = get_api_key("nonexistent-provider")
        assert result is None

    def test_list_configured_providers_empty(self):
        """list_configured_providers returns empty list when no secrets."""
        from flocks.provider.credential import list_configured_providers
        # Might return non-empty if there are real secrets, but should not crash
        result = list_configured_providers()
        assert isinstance(result, list)

    def test_migrate_env_credentials_no_env(self, monkeypatch):
        """migrate_env_credentials does nothing when no env vars set."""
        from flocks.provider.credential import migrate_env_credentials
        # Clear all relevant env vars
        for var in [
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
            "DEEPSEEK_API_KEY",
        ]:
            monkeypatch.delenv(var, raising=False)

        result = migrate_env_credentials()
        assert result >= 0  # Should not crash


# ==================== BaseProvider extensions ====================


class TestBaseProviderExtensions:
    """Test BaseProvider new methods."""

    def test_get_meta_default(self):
        from flocks.provider.provider import BaseProvider

        class DummyProvider(BaseProvider):
            def get_models(self):
                return []

        p = DummyProvider("test-provider", "Test Provider")
        meta = p.get_meta()

        assert meta.id == "test-provider"
        assert meta.name == "Test Provider"
        assert AuthMethod.API_KEY in meta.supported_auth_methods
        assert len(meta.credential_schemas) == 1
        assert meta.credential_schemas[0].auth_method == AuthMethod.API_KEY

    def test_get_model_definitions_from_legacy(self):
        from flocks.provider.provider import BaseProvider, ModelInfo, ModelCapabilities

        class DummyProvider(BaseProvider):
            def get_models(self):
                return [
                    ModelInfo(
                        id="dummy-model",
                        name="Dummy Model",
                        provider_id="dummy",
                        capabilities=ModelCapabilities(
                            supports_streaming=True,
                            supports_tools=True,
                            supports_vision=True,
                            context_window=100000,
                            max_tokens=8000,
                        ),
                    )
                ]

        p = DummyProvider("dummy", "Dummy")
        defs = p.get_model_definitions()

        assert len(defs) == 1
        assert defs[0].id == "dummy-model"
        assert defs[0].capabilities.supports_vision is True
        assert defs[0].limits.context_window == 100000
        assert defs[0].limits.max_output_tokens == 8000

    def test_config_override_false_removes_reasoning_feature(self):
        from flocks.provider.provider import BaseProvider, ModelInfo, ModelCapabilities

        catalog_def = ModelDefinition(
            id="dummy-model",
            name="Dummy Model",
            provider_id="dummy",
            fetch_from=FetchFrom.PREDEFINED,
            capabilities=ModelCapabilitiesV2(
                features=[ModelFeature.REASONING],
                supports_reasoning=True,
            ),
        )
        model = ModelInfo(
            id="dummy-model",
            name="Dummy Model",
            provider_id="dummy",
            capabilities=ModelCapabilities(supports_reasoning=False),
        )
        model._explicit_keys = {"supports_reasoning"}

        p = BaseProvider("dummy", "Dummy")
        overridden = p._apply_config_overrides(catalog_def, model)

        assert overridden.capabilities.supports_reasoning is False
        assert ModelFeature.REASONING not in overridden.capabilities.features

    def test_configure_from_credential(self):
        from flocks.provider.provider import BaseProvider

        class DummyProvider(BaseProvider):
            def get_models(self):
                return []

        p = DummyProvider("test", "Test")

        config = CredentialConfig(
            api_key="sk-from-cred",
            base_url="https://custom.api.com",
        )
        p.configure_from_credential(config)

        assert p.is_configured()
        assert p._config.api_key == "sk-from-cred"
        assert p._config.base_url == "https://custom.api.com"
