"""Tests for adaptive LLM stream timeout resolution."""

from types import SimpleNamespace

from flocks.session.streaming.timeouts import resolve_llm_stream_timeouts


def _provider(
    *,
    provider_id: str = "openai",
    base_url: str = "https://api.example.com/v1",
    provider_settings: dict | None = None,
    model_settings: dict | None = None,
):
    model = SimpleNamespace(
        id="test-model",
        custom_settings=model_settings or {},
    )
    return SimpleNamespace(
        id=provider_id,
        _base_url=base_url,
        _config=SimpleNamespace(
            base_url=base_url,
            custom_settings=provider_settings or {},
        ),
        get_models=lambda: [model],
    )


def test_cloud_provider_uses_safe_defaults(monkeypatch):
    monkeypatch.delenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", raising=False)
    monkeypatch.delenv("FLOCKS_LLM_STREAM_ONGOING_CHUNK_TIMEOUT_S", raising=False)

    timeouts = resolve_llm_stream_timeouts(_provider(), "test-model")

    assert timeouts.first_chunk_s == 120.0
    assert timeouts.ongoing_chunk_s == 300.0
    assert timeouts.is_local is False


def test_local_provider_gets_long_prefill_budget(monkeypatch):
    monkeypatch.delenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", raising=False)

    timeouts = resolve_llm_stream_timeouts(
        _provider(base_url="http://127.0.0.1:11434/v1"),
        "test-model",
    )

    assert timeouts.first_chunk_s == 1800.0
    assert timeouts.is_local is True


def test_private_network_endpoint_is_treated_as_local(monkeypatch):
    monkeypatch.delenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", raising=False)

    timeouts = resolve_llm_stream_timeouts(
        _provider(base_url="http://192.168.1.20:8000/v1"),
        "test-model",
    )

    assert timeouts.first_chunk_s == 1800.0
    assert timeouts.is_local is True


def test_environment_overrides_default(monkeypatch):
    monkeypatch.setenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", "240")
    monkeypatch.setenv("FLOCKS_LLM_STREAM_ONGOING_CHUNK_TIMEOUT_S", "420")

    timeouts = resolve_llm_stream_timeouts(_provider(), "test-model")

    assert timeouts.first_chunk_s == 240.0
    assert timeouts.ongoing_chunk_s == 420.0


def test_provider_config_overrides_environment(monkeypatch):
    monkeypatch.setenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", "240")
    provider = _provider(
        provider_settings={"stream_first_chunk_timeout_s": 360},
    )

    timeouts = resolve_llm_stream_timeouts(provider, "test-model")

    assert timeouts.first_chunk_s == 360.0


def test_model_config_overrides_provider_config(monkeypatch):
    monkeypatch.setenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", "240")
    provider = _provider(
        provider_settings={"stream_first_chunk_timeout_s": 360},
        model_settings={
            "stream_first_chunk_timeout_s": 480,
            "stream_ongoing_chunk_timeout_s": 600,
        },
    )

    timeouts = resolve_llm_stream_timeouts(provider, "test-model")

    assert timeouts.first_chunk_s == 480.0
    assert timeouts.ongoing_chunk_s == 600.0


def test_invalid_overrides_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("FLOCKS_LLM_STREAM_FIRST_CHUNK_TIMEOUT_S", "invalid")
    provider = _provider(
        provider_settings={"stream_first_chunk_timeout_s": -1},
        model_settings={"stream_first_chunk_timeout_s": 0},
    )

    timeouts = resolve_llm_stream_timeouts(provider, "test-model")

    assert timeouts.first_chunk_s == 120.0
