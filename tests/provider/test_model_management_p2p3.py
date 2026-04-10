"""
Tests for model management module (Phase 2 & Phase 3)

Covers:
- model_catalog.py: Provider metadata and model definitions
- model_manager.py: Model listing, settings (flocks.json backed), default models
- cost_calculator.py: Cost calculation
- usage.py: Usage recording and statistics (SQLite backed)
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from flocks.provider.types import (
    AuthMethod,
    DefaultModelConfig,
    ModelDefinition,
    ModelFeature,
    ModelSetting,
    ModelType,
    PriceConfig,
    UsageCost,
)


# ==================== Helpers ====================


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def reset_provider_runtime():
    """Reset provider registry and apply config from the user flocks.json."""
    from flocks.provider.provider import Provider

    Provider._initialized = False
    Provider._providers = {}
    Provider._models = {}
    run_async(Provider.apply_config())
    return Provider


@pytest.fixture
def setup_storage(temp_dir):
    """Initialize storage with a temp database (for usage records)."""
    from flocks.storage.storage import Storage
    Storage._initialized = False
    Storage._db_path = None
    run_async(Storage.init(db_path=temp_dir / "test.db"))
    yield
    Storage._initialized = False
    Storage._db_path = None


@pytest.fixture
def temp_project(tmp_path, monkeypatch):
    """Create a temporary user config directory with provider models in flocks.json."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    from flocks.config.config import Config
    Config._global_config = None
    Config._cached_config = None
    config_file = config_dir / "flocks.json"
    config_file.write_text(json.dumps({
        "provider": {
            "anthropic": {
                "npm": "@ai-sdk/anthropic",
                "options": {
                    "baseURL": "https://api.anthropic.com",
                },
                "models": {
                    "claude-sonnet-4-6": {"name": "Claude Sonnet 4.6"},
                    "claude-opus-4-6": {"name": "Claude Opus 4.6"},
                },
            }
        }
    }, indent=2))
    return config_dir


# ==================== model_catalog.py ====================


class TestModelCatalog:
    """Test model catalog definitions."""

    def test_anthropic_meta(self):
        from flocks.provider.model_catalog import get_provider_meta
        meta = get_provider_meta("anthropic")
        assert meta is not None
        assert meta.id == "anthropic"
        assert meta.name == "Anthropic"
        assert AuthMethod.API_KEY in meta.supported_auth_methods
        assert AuthMethod.SUBSCRIPTION in meta.supported_auth_methods
        assert len(meta.credential_schemas) == 2

    def test_openai_meta(self):
        from flocks.provider.model_catalog import get_provider_meta
        meta = get_provider_meta("openai")
        assert meta is not None
        assert ModelType.LLM in meta.supported_model_types

    def test_google_meta(self):
        from flocks.provider.model_catalog import get_provider_meta
        meta = get_provider_meta("google")
        assert meta is not None
        assert meta.id == "google"

    def test_unknown_provider_returns_none(self):
        from flocks.provider.model_catalog import get_provider_meta
        assert get_provider_meta("nonexistent") is None

    def test_anthropic_models(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("anthropic")
        assert len(models) == 2
        ids = [m.id for m in models]
        assert "claude-sonnet-4-6" in ids
        assert "claude-opus-4-6" in ids

    def test_openai_models_are_llm_only(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("openai")
        types = [m.model_type for m in models]
        assert ModelType.LLM in types
        assert all(t == ModelType.LLM for t in types)

    def test_model_has_pricing(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("anthropic")
        sonnet4 = next(m for m in models if m.id == "claude-sonnet-4-6")
        assert sonnet4.pricing is not None
        assert sonnet4.pricing.input == 3.0
        assert sonnet4.pricing.output == 15.0

    def test_model_has_parameter_rules(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("anthropic")
        sonnet4 = next(m for m in models if m.id == "claude-sonnet-4-6")
        assert len(sonnet4.parameter_rules) >= 2
        names = [r.name for r in sonnet4.parameter_rules]
        assert "temperature" in names
        assert "max_tokens" in names

    def test_model_has_features(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("anthropic")
        sonnet4 = next(m for m in models if m.id == "claude-sonnet-4-6")
        assert ModelFeature.TOOL_CALL in sonnet4.capabilities.features
        assert ModelFeature.VISION in sonnet4.capabilities.features
        assert ModelFeature.REASONING in sonnet4.capabilities.features

    def test_google_gemini_multimodal(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("google")
        flash = next(m for m in models if m.id == "gemini-2.5-flash")
        assert flash.capabilities.supports_vision is True
        assert flash.limits.context_window == 1048576

    def test_openai_gpt5_reasoning(self):
        from flocks.provider.model_catalog import get_provider_model_definitions
        models = get_provider_model_definitions("openai")
        gpt5 = next(m for m in models if m.id == "gpt-5.4")
        assert gpt5.capabilities.supports_reasoning is True
        assert ModelFeature.REASONING in gpt5.capabilities.features


# ==================== model_manager.py (now sync, flocks.json backed) ====================


class TestModelManager:
    """Test model management service — settings stored in flocks.json."""

    def test_list_all_models(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        reset_provider_runtime()

        manager = ModelManager()
        models = manager.list_models()
        # Should have models from all registered providers
        assert len(models) > 0

    def test_list_models_by_provider(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        reset_provider_runtime()

        manager = ModelManager()
        models = manager.list_models(provider_id="anthropic")
        assert all(m.provider_id == "anthropic" for m in models)
        assert len(models) == 2

    def test_list_models_by_type(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        reset_provider_runtime()

        manager = ModelManager()
        embed_models = manager.list_models(model_type=ModelType.TEXT_EMBEDDING)
        assert embed_models == []

    def test_get_model(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        reset_provider_runtime()

        manager = ModelManager()
        model = manager.get_model("anthropic", "claude-sonnet-4-6")
        assert model is not None
        assert model.id == "claude-sonnet-4-6"
        assert model.provider_id == "anthropic"

    def test_get_model_not_found(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        reset_provider_runtime()

        manager = ModelManager()
        model = manager.get_model("anthropic", "nonexistent-model")
        assert model is None

    def test_model_settings_crud(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        manager = ModelManager()

        # Initially no setting
        setting = manager.get_setting("openai", "gpt-4o")
        assert setting is None

        # Create setting
        setting = manager.update_setting(
            "openai", "gpt-4o", enabled=False,
            default_parameters={"temperature": 0.5},
        )
        assert setting.enabled is False
        assert setting.default_parameters["temperature"] == 0.5

        # Update setting
        setting = manager.update_setting(
            "openai", "gpt-4o", enabled=True,
        )
        assert setting.enabled is True
        # Previous default_parameters should be preserved
        assert setting.default_parameters.get("temperature") == 0.5

    def test_default_model_crud(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        manager = ModelManager()

        # Initially empty
        defaults = manager.get_all_defaults()
        assert len(defaults) == 0

        # Set default
        result = manager.set_default_model(
            ModelType.LLM, "anthropic", "claude-sonnet-4-6",
        )
        assert result.model_type == ModelType.LLM
        assert result.provider_id == "anthropic"

        # Get default
        default = manager.get_default_model(ModelType.LLM)
        assert default is not None
        assert default.model_id == "claude-sonnet-4-6"

        # Set another type
        manager.set_default_model(
            ModelType.TEXT_EMBEDDING, "openai", "text-embedding-3-small",
        )
        defaults = manager.get_all_defaults()
        assert len(defaults) == 2

        # Delete
        deleted = manager.delete_default_model(ModelType.LLM)
        assert deleted is True
        defaults = manager.get_all_defaults()
        assert len(defaults) == 1

    def test_enabled_only_filter(self, temp_project):
        from flocks.provider.model_manager import ModelManager
        reset_provider_runtime()

        manager = ModelManager()

        # Disable a model
        manager.update_setting(
            "anthropic", "claude-opus-4-6", enabled=False,
        )

        all_models = manager.list_models(provider_id="anthropic")
        enabled_models = manager.list_models(
            provider_id="anthropic", enabled_only=True,
        )
        assert len(enabled_models) < len(all_models)
        disabled_ids = [m.id for m in enabled_models]
        assert "claude-opus-4-6" not in disabled_ids

    def test_settings_persisted_in_flocks_json(self, temp_project):
        """Verify that model settings are persisted in flocks.json."""
        from flocks.provider.model_manager import ModelManager
        from flocks.config.config_writer import ConfigWriter

        manager = ModelManager()
        manager.update_setting("openai", "gpt-4o", enabled=False)

        # Verify it's in flocks.json
        data = ConfigWriter._read_raw()
        assert "model_settings" in data
        assert "openai/gpt-4o" in data["model_settings"]
        assert data["model_settings"]["openai/gpt-4o"]["enabled"] is False

    def test_default_model_persisted_in_flocks_json(self, temp_project):
        """Verify that default models are persisted in flocks.json."""
        from flocks.provider.model_manager import ModelManager
        from flocks.config.config_writer import ConfigWriter

        manager = ModelManager()
        manager.set_default_model(ModelType.LLM, "anthropic", "claude-sonnet-4-6")

        # Verify it's in flocks.json
        data = ConfigWriter._read_raw()
        assert "default_models" in data
        assert "llm" in data["default_models"]
        assert data["default_models"]["llm"]["provider_id"] == "anthropic"


# ==================== cost_calculator.py ====================


class TestCostCalculator:
    """Test cost calculation."""

    def test_basic_calculation(self):
        from flocks.provider.cost_calculator import CostCalculator
        pricing = PriceConfig(input=3.0, output=15.0)

        cost = CostCalculator.calculate(
            input_tokens=1000,
            output_tokens=500,
            pricing=pricing,
        )
        # 1000 / 1M * 3.0 = 0.003
        # 500 / 1M * 15.0 = 0.0075
        assert abs(cost.input_cost - 0.003) < 0.0001
        assert abs(cost.output_cost - 0.0075) < 0.0001
        assert abs(cost.total_cost - 0.0105) < 0.0001

    def test_with_cache(self):
        from flocks.provider.cost_calculator import CostCalculator
        pricing = PriceConfig(input=3.0, output=15.0, cache_read=0.3)

        cost = CostCalculator.calculate(
            input_tokens=10000,
            output_tokens=1000,
            pricing=pricing,
            cached_tokens=8000,
        )
        # Billable input: 10000 - 8000 = 2000
        # input: 2000/1M * 3.0 = 0.006
        # output: 1000/1M * 15.0 = 0.015
        # cache: 8000/1M * 0.3 = 0.0024
        assert abs(cost.input_cost - 0.006) < 0.0001
        assert abs(cost.output_cost - 0.015) < 0.0001
        assert abs(cost.cache_cost - 0.0024) < 0.0001
        assert abs(cost.total_cost - 0.0234) < 0.0001

    def test_with_cache_write(self):
        from flocks.provider.cost_calculator import CostCalculator
        pricing = PriceConfig(
            input=3.0,
            output=15.0,
            cache_read=0.3,
            cache_write=3.75,
        )

        cost = CostCalculator.calculate(
            input_tokens=10000,
            output_tokens=1000,
            pricing=pricing,
            cached_tokens=8000,
            cache_write_tokens=2000,
        )
        # Billable input: 10000 - 8000 = 2000 => 0.006
        # output: 1000 / 1M * 15.0 = 0.015
        # cache read: 8000 / 1M * 0.3 = 0.0024
        # cache write: 2000 / 1M * 3.75 = 0.0075
        assert abs(cost.input_cost - 0.006) < 0.0001
        assert abs(cost.output_cost - 0.015) < 0.0001
        assert abs(cost.cache_cost - 0.0099) < 0.0001
        assert abs(cost.total_cost - 0.0309) < 0.0001

    def test_zero_tokens(self):
        from flocks.provider.cost_calculator import CostCalculator
        pricing = PriceConfig(input=3.0, output=15.0)
        cost = CostCalculator.calculate(0, 0, pricing)
        assert cost.total_cost == 0.0

    def test_no_cache_pricing(self):
        from flocks.provider.cost_calculator import CostCalculator
        pricing = PriceConfig(input=3.0, output=15.0)
        cost = CostCalculator.calculate(1000, 1000, pricing, cached_tokens=500)
        # cache_read is None, so no cache cost
        assert cost.cache_cost == 0.0


# ==================== usage.py (recording & stats — SQLite dynamic data) ====================


class TestUsageTracking:
    """Test usage recording and statistics (SQLite-backed)."""

    def test_record_usage_basic(self, setup_storage):
        from flocks.server.routes.usage import RecordUsageRequest, record_usage

        req = RecordUsageRequest(
            provider_id="anthropic",
            model_id="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=500,
        )
        record = run_async(record_usage(req))
        assert record.id is not None
        assert record.provider_id == "anthropic"
        assert record.total_tokens == 1500
        assert record.total_cost == 0.0  # No pricing provided

    def test_record_usage_with_pricing(self, setup_storage):
        from flocks.server.routes.usage import RecordUsageRequest, record_usage

        req = RecordUsageRequest(
            provider_id="anthropic",
            model_id="claude-sonnet-4-20250514",
            input_tokens=10000,
            output_tokens=2000,
            cached_tokens=5000,
            cache_write_tokens=1000,
            pricing=PriceConfig(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
        )
        record = run_async(record_usage(req))
        assert record.total_cost > 0
        assert record.input_cost > 0
        assert record.output_cost > 0
        assert abs(record.total_cost - 0.05025) < 0.0001

    def test_backfill_skips_existing_live_record_with_same_message_id(self, setup_storage, monkeypatch):
        from flocks.provider import usage_service
        from flocks.provider.usage_service import (
            RecordUsageRequest,
            backfill_usage_records,
            get_usage_records,
            record_usage,
        )
        from flocks.session.message import (
            AssistantMessageInfo,
            MessagePath,
            MessageWithParts,
            TokenCache,
            TokenUsage,
            UserMessageInfo,
        )
        from flocks.session.session import SessionInfo, SessionTime

        usage_service._auto_backfill_complete = False

        session = SessionInfo(
            id="ses_backfill_live",
            projectID="proj_backfill_live",
            directory="/tmp/backfill-live",
            title="Backfill Live Session",
            time=SessionTime(created=1_000, updated=2_000),
        )
        user = UserMessageInfo(
            id="msg_user_live",
            sessionID=session.id,
            role="user",
            time={"created": 1_000},
            agent="rex",
            model={"providerID": "anthropic", "modelID": "claude-sonnet"},
        )
        assistant = AssistantMessageInfo(
            id="msg_assistant_live",
            sessionID=session.id,
            role="assistant",
            time={"created": 1_100, "completed": 1_200},
            parentID=user.id,
            modelID="claude-sonnet",
            providerID="anthropic",
            mode="standard",
            agent="rex",
            path=MessagePath(cwd="/tmp/backfill-live", root="/tmp/backfill-live"),
            tokens=TokenUsage(input=11, output=7, reasoning=3, cache=TokenCache(read=5, write=2)),
            cost=1.25,
        )
        message = MessageWithParts(info=assistant, parts=[])

        run_async(record_usage(RecordUsageRequest(
            provider_id="anthropic",
            model_id="claude-sonnet",
            session_id=session.id,
            message_id=assistant.id,
            input_tokens=11,
            output_tokens=7,
            cached_tokens=5,
            cache_write_tokens=2,
            reasoning_tokens=3,
            pricing=PriceConfig(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75),
        )))

        async def fake_list_all():
            return [session]

        async def fake_list_with_parts(session_id: str, include_archived: bool = False):
            assert session_id == session.id
            assert include_archived is False
            return [MessageWithParts(info=user, parts=[]), message]

        monkeypatch.setattr(usage_service.Session, "list_all", fake_list_all)
        monkeypatch.setattr(usage_service.Message, "list_with_parts", fake_list_with_parts)

        result = run_async(backfill_usage_records())
        records = run_async(get_usage_records(session_ids=[session.id]))

        assert result.inserted_records == 0
        assert result.skipped_existing == 1
        assert len(records) == 1
        assert records[0].source == "live"

    def test_record_multiple_and_query(self, setup_storage):
        from flocks.server.routes.usage import RecordUsageRequest, record_usage

        # Record several entries
        for i in range(3):
            run_async(record_usage(RecordUsageRequest(
                provider_id="anthropic",
                model_id="claude-sonnet-4-20250514",
                input_tokens=1000 * (i + 1),
                output_tokens=500 * (i + 1),
                pricing=PriceConfig(input=3.0, output=15.0),
            )))

        run_async(record_usage(RecordUsageRequest(
            provider_id="openai",
            model_id="gpt-4o",
            input_tokens=2000,
            output_tokens=1000,
            pricing=PriceConfig(input=2.5, output=10.0),
        )))

        # Query stats
        from flocks.server.routes.usage import get_usage_stats
        stats = run_async(get_usage_stats())

        assert stats.summary.total_requests == 4
        assert stats.summary.total_tokens > 0
        assert stats.summary.total_cost > 0

        # By provider
        assert len(stats.by_provider) == 2
        provider_ids = [p.provider_id for p in stats.by_provider]
        assert "anthropic" in provider_ids
        assert "openai" in provider_ids

        # By model
        assert len(stats.by_model) == 2

    def test_query_with_provider_filter(self, setup_storage):
        from flocks.server.routes.usage import RecordUsageRequest, record_usage, get_usage_stats

        run_async(record_usage(RecordUsageRequest(
            provider_id="anthropic", model_id="m1",
            input_tokens=100, output_tokens=50,
        )))
        run_async(record_usage(RecordUsageRequest(
            provider_id="openai", model_id="m2",
            input_tokens=200, output_tokens=100,
        )))

        stats = run_async(get_usage_stats(provider_id="anthropic"))
        assert stats.summary.total_requests == 1
        assert len(stats.by_provider) == 1
        assert stats.by_provider[0].provider_id == "anthropic"

    def test_query_groups_costs_by_currency(self, setup_storage):
        from flocks.server.routes.usage import RecordUsageRequest, record_usage, get_usage_stats

        run_async(record_usage(RecordUsageRequest(
            provider_id="anthropic",
            model_id="m1",
            input_tokens=1000,
            output_tokens=500,
            pricing=PriceConfig(input=3.0, output=15.0, currency="USD"),
        )))
        run_async(record_usage(RecordUsageRequest(
            provider_id="threatbook",
            model_id="m2",
            input_tokens=1000,
            output_tokens=500,
            pricing=PriceConfig(input=2.1, output=8.4, currency="CNY"),
        )))

        stats = run_async(get_usage_stats())

        assert stats.summary.currency == "MIXED"
        assert stats.summary.total_cost == 0.0
        assert {(item.currency, item.total_cost > 0) for item in stats.summary.cost_by_currency} == {
            ("USD", True),
            ("CNY", True),
        }

    def test_backfill_usage_records_is_idempotent(self, setup_storage, monkeypatch):
        from flocks.provider import usage_service
        from flocks.provider.usage_service import backfill_usage_records, get_usage_records
        from flocks.session.message import (
            AssistantMessageInfo,
            MessagePath,
            MessageWithParts,
            TokenCache,
            TokenUsage,
            UserMessageInfo,
        )
        from flocks.session.session import SessionInfo, SessionTime

        usage_service._auto_backfill_complete = False

        session = SessionInfo(
            id="ses_backfill",
            projectID="proj_backfill",
            directory="/tmp/backfill",
            title="Backfill Session",
            time=SessionTime(created=1_000, updated=2_000),
        )
        user = UserMessageInfo(
            id="msg_user",
            sessionID=session.id,
            role="user",
            time={"created": 1_000},
            agent="rex",
            model={"providerID": "anthropic", "modelID": "claude-sonnet"},
        )
        assistant = AssistantMessageInfo(
            id="msg_assistant",
            sessionID=session.id,
            role="assistant",
            time={"created": 1_100, "completed": 1_200},
            parentID=user.id,
            modelID="claude-sonnet",
            providerID="anthropic",
            mode="standard",
            agent="rex",
            path=MessagePath(cwd="/tmp/backfill", root="/tmp/backfill"),
            tokens=TokenUsage(input=11, output=7, reasoning=3, cache=TokenCache(read=5, write=2)),
            cost=1.25,
        )
        message = MessageWithParts(info=assistant, parts=[])

        async def fake_list_all():
            return [session]

        async def fake_list_with_parts(session_id: str, include_archived: bool = False):
            assert session_id == session.id
            assert include_archived is False
            return [MessageWithParts(info=user, parts=[]), message]

        monkeypatch.setattr(usage_service.Session, "list_all", fake_list_all)
        monkeypatch.setattr(usage_service.Message, "list_with_parts", fake_list_with_parts)

        first = run_async(backfill_usage_records())
        second = run_async(backfill_usage_records())
        records = run_async(get_usage_records(session_ids=[session.id]))

        assert first.inserted_records == 1
        assert second.skipped_existing == 1
        assert len(records) == 1
        assert records[0].message_id == assistant.id
        assert records[0].cache_write_tokens == 2
        assert records[0].source == "backfill"
