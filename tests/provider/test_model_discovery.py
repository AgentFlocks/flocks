"""Model discovery against simulated upstream APIs, without provider mutation."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from flocks.config.config import Config, ConfigInfo
from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider


@pytest.fixture
def discovery_runtime(tmp_path, monkeypatch):
    """Isolate both saved configuration and the in-memory provider registry."""
    from flocks.provider import model_discovery

    config_file = tmp_path / "flocks.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ConfigWriter, "_get_config_path", lambda: config_file)
    monkeypatch.setattr(Provider, "_providers", {})
    monkeypatch.setattr(Provider, "_models", {})
    monkeypatch.setattr(Provider, "_initialized", True)
    for name in ("OPENAI_API_KEY", "LLM_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "LLM_API_BASE"):
        monkeypatch.delenv(name, raising=False)

    async def get_config():
        return ConfigInfo.model_validate(json.loads(config_file.read_text()))

    monkeypatch.setattr(Config, "get", get_config)
    secrets = MagicMock()
    secrets.get.return_value = None
    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: secrets)
    writes = MagicMock(side_effect=AssertionError("Discovery must not write config"))
    monkeypatch.setattr(ConfigWriter, "_write_raw", writes)
    apply_config = AsyncMock(side_effect=AssertionError("Discovery must not mutate runtime"))
    monkeypatch.setattr(Provider, "apply_config", apply_config)

    return SimpleNamespace(
        module=model_discovery,
        config_file=config_file,
        secrets=secrets,
        writes=writes,
        apply_config=apply_config,
    )


@pytest.fixture
def upstream(discovery_runtime, monkeypatch):
    """Keep the real HTTP parser, streaming limits, and redirect behavior."""
    real_client = httpx.AsyncClient
    requests = []
    options = []
    state = SimpleNamespace(handler=lambda request: httpx.Response(200, json={"data": []}))

    def dispatch(request):
        requests.append(request)
        return state.handler(request)

    def factory(*args, **kwargs):
        options.append(copy.copy(kwargs))
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(discovery_runtime.module.httpx, "AsyncClient", factory)
    state.requests = requests
    state.options = options
    monkeypatch.setattr(discovery_runtime.module, "_validate_resolved_addresses", AsyncMock())
    return state


def save_provider(runtime, provider_id, *, npm="@ai-sdk/openai-compatible", options=None, models=None):
    data = {
        "provider": {
            provider_id: {
                "npm": npm,
                "options": options or {},
                "models": models or {},
            }
        }
    }
    runtime.config_file.write_text(json.dumps(data), encoding="utf-8")


OPENAI_CATALOG_IDS = [
    "openai-compatible",
    "threatbook-cn-llm",
    "threatbook-io-llm",
    "openai",
    "xai",
    "deepseek",
    "alibaba",
    "moonshot",
    "zhipu",
    "minimax",
    "stepfun",
    "cherry",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", OPENAI_CATALOG_IDS)
async def test_catalog_compatible_providers_fetch_from_supplied_endpoint(
    provider_id,
    discovery_runtime,
    upstream,
):
    upstream.handler = lambda request: httpx.Response(
        200,
        json={
            "data": [
                {"id": "vendor/model:latest", "name": "Private Model"},
            ]
        },
    )

    result = await discovery_runtime.module.discover_models(
        provider_id,
        api_key="new-secret",
        base_url="https://gateway.example.test/prefix/v1/",
    )

    assert result["status"] == "success"
    assert result["source"] == "api"
    assert result["code"] == "models_discovered"
    assert result["truncated"] is False
    datetime.fromisoformat(result["fetched_at"].replace("Z", "+00:00"))
    assert [model["id"] for model in result["models"]] == ["vendor/model:latest"]
    assert result["models"][0]["already_added"] is False
    assert result["models"][0]["selectable"] is True
    assert str(upstream.requests[0].url) == "https://gateway.example.test/prefix/v1/models"
    assert upstream.requests[0].headers["authorization"] == "Bearer new-secret"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["threatbook-cn-llm", "threatbook-io-llm"])
@pytest.mark.parametrize("listing_status", [404, 405, 501])
async def test_threatbook_reads_authenticated_model_list_without_using_a_real_model(
    provider_id, listing_status, discovery_runtime, upstream
):
    def respond(request):
        if request.method == "GET":
            return httpx.Response(listing_status)
        assert request.url.path == "/prefix/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer candidate-key"
        body = json.loads(request.content)
        assert body["model"].startswith("__flocks_model_list_probe_")
        assert body["messages"] == [{"role": "user", "content": ""}]
        assert body["max_tokens"] == 1 and body["stream"] is False
        return httpx.Response(400, json={"error": {
            "type": "MODEL_NOT_FOUND", "data": {"supported_models": ["live-new-model", "live-other-model"]},
        }})

    upstream.handler = respond
    result = await discovery_runtime.module.discover_models(
        provider_id, api_key="candidate-key", base_url="https://gateway.example.test/prefix/v1"
    )
    assert result["status"] == "success" and result["source"] == "api"
    assert [model["id"] for model in result["models"]] == ["live-new-model", "live-other-model"]
    assert [request.method for request in upstream.requests] == ["GET", "POST"]
    assert all(request.url.host == "gateway.example.test" for request in upstream.requests)
    assert Provider._providers == {} and discovery_runtime.config_file.read_text() == "{}"
    discovery_runtime.writes.assert_not_called()
    discovery_runtime.apply_config.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["threatbook-cn-llm", "threatbook-io-llm"])
@pytest.mark.parametrize("status", [400, 401])
async def test_threatbook_rejects_bad_credentials_from_its_authenticated_list(
    provider_id, status, discovery_runtime, upstream
):
    upstream.handler = lambda request: httpx.Response(404) if request.method == "GET" else httpx.Response(
        status, json={"error": {"type": "AUTH_ERROR"}}
    )
    result = await discovery_runtime.module.discover_models(provider_id, api_key="bad-key")
    assert result["status"] == "error" and result["code"] == "authentication_failed"
    assert result["source"] == "none" and result["models"] == []
    discovery_runtime.writes.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"error": {"type": "MODEL_NOT_FOUND"}},
    {"error": {"type": "MODEL_NOT_FOUND", "data": {"supported_models": "not-a-list"}}},
    {"error": {"type": "MODEL_NOT_FOUND", "data": {"supported_models": [{"id": "wrong-shape"}]}}},
    {"error": {"type": "OTHER_ERROR", "data": {"supported_models": ["untrusted"]}}},
])
async def test_threatbook_does_not_accept_unexpected_validation_payload(payload, discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(404) if request.method == "GET" else httpx.Response(400, json=payload)
    result = await discovery_runtime.module.discover_models("threatbook-cn-llm", api_key="candidate-key")
    assert result["status"] == "error" and result["code"] == "invalid_response"
    assert result["models"] == []


@pytest.mark.asyncio
async def test_other_providers_do_not_use_the_threatbook_probe(discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(404)
    await discovery_runtime.module.discover_models("openai", api_key="candidate-key")
    assert len(upstream.requests) == 1 and upstream.requests[0].method == "GET"


@pytest.mark.asyncio
async def test_anthropic_cursor_pagination_and_auth_headers(discovery_runtime, upstream):
    def respond(request):
        if "after_id" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "claude-first", "display_name": "First"}],
                    "has_more": True,
                    "last_id": "claude-first",
                },
            )
        assert request.url.params["after_id"] == "claude-first"
        return httpx.Response(
            200,
            json={
                "data": [{"id": "claude-second", "display_name": "Second"}],
                "has_more": False,
                "last_id": "claude-second",
            },
        )

    upstream.handler = respond
    result = await discovery_runtime.module.discover_models("anthropic", api_key="anthropic-test")

    assert result["status"] == "success"
    assert [m["id"] for m in result["models"]] == ["claude-first", "claude-second"]
    assert [m["name"] for m in result["models"]] == ["First", "Second"]
    assert all(request.url.path == "/v1/models" for request in upstream.requests)
    assert upstream.requests[0].headers["x-api-key"] == "anthropic-test"
    assert "anthropic-version" in upstream.requests[0].headers


@pytest.mark.asyncio
async def test_google_pagination_preserves_unknown_metadata_and_blocks_embedding(discovery_runtime, upstream):
    def respond(request):
        assert request.url.path == "/v1beta/models"
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-private",
                            "displayName": "Private Gemini",
                            "inputTokenLimit": 64000,
                            "outputTokenLimit": 4096,
                            "supportedGenerationMethods": ["generateContent"],
                        },
                    ],
                    "nextPageToken": "next-page",
                },
            )
        assert request.url.params["pageToken"] == "next-page"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/text-embedding-private", "supportedGenerationMethods": ["embedContent"]},
                ]
            },
        )

    upstream.handler = respond
    result = await discovery_runtime.module.discover_models("google", api_key="google-test")

    assert result["status"] == "success"
    models = {m["id"]: m for m in result["models"]}
    assert models["gemini-private"]["name"] == "Private Gemini"
    assert models["gemini-private"]["context_window"] == 64000
    assert models["gemini-private"]["max_output_tokens"] == 4096
    assert models["gemini-private"].get("supports_vision") is None
    assert models["gemini-private"]["selectable"] is True
    assert models["text-embedding-private"]["selectable"] is False
    assert models["text-embedding-private"]["unavailable_reason"]


@pytest.mark.asyncio
async def test_cohere_uses_v1_list_endpoint_with_chat_filter_and_page_token(discovery_runtime, upstream):
    def respond(request):
        assert request.url.path == "/v1/models"
        assert request.url.params["endpoint"] == "chat"
        if "page_token" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "command-private", "endpoints": ["chat"], "context_length": 32000},
                    ],
                    "next_page_token": "page-two",
                },
            )
        assert request.url.params["page_token"] == "page-two"
        return httpx.Response(200, json={"models": [{"name": "command-other", "endpoints": ["chat"]}]})

    upstream.handler = respond
    result = await discovery_runtime.module.discover_models("cohere", api_key="cohere-test")

    assert result["status"] == "success"
    assert [m["id"] for m in result["models"]] == ["command-private", "command-other"]
    assert result["models"][0]["context_window"] == 32000
    assert upstream.requests[0].headers["authorization"] == "Bearer cohere-test"


@pytest.mark.asyncio
async def test_azure_catalog_is_never_importable_as_deployment(discovery_runtime, upstream):
    result = await discovery_runtime.module.discover_models(
        "azure-openai",
        api_key="azure-test",
        base_url="https://resource.openai.azure.com/",
    )

    assert result["source"] == "catalog"
    assert result["status"] == "fallback"
    assert result["models"]
    assert all(not m["selectable"] and m["unavailable_reason"] for m in result["models"])
    assert not upstream.requests


@pytest.mark.asyncio
async def test_ollama_native_tags_preserve_namespace_and_tag_without_auth(discovery_runtime, upstream):
    from flocks.provider.sdk.ollama import OllamaProvider

    Provider._providers["ollama"] = OllamaProvider()
    upstream.handler = lambda request: httpx.Response(
        200,
        json={
            "models": [
                {"name": "acme/llama:latest", "model": "acme/llama:latest", "details": {"family": "llama"}},
            ]
        },
    )

    result = await discovery_runtime.module.discover_models("ollama", base_url="http://127.0.0.1:11434/v1")

    assert result["status"] == "success"
    assert result["models"][0]["id"] == "acme/llama:latest"
    assert upstream.requests[0].url.path == "/api/tags"
    assert "authorization" not in upstream.requests[0].headers


@pytest.mark.asyncio
async def test_configured_sdk_provider_outside_catalog_can_discover(discovery_runtime, upstream):
    from flocks.provider.sdk.mistral import MistralProvider

    Provider._providers["mistral"] = MistralProvider()
    save_provider(
        discovery_runtime,
        "mistral",
        npm="@ai-sdk/mistral",
        options={
            "apiKey": "sdk-key",
            "baseURL": "https://mistral.example.test/v1",
        },
    )
    upstream.handler = lambda request: httpx.Response(200, json={"data": [{"id": "private-sdk-model"}]})

    result = await discovery_runtime.module.discover_models("mistral")

    assert result["status"] == "success"
    assert result["models"][0]["id"] == "private-sdk-model"
    assert upstream.requests[0].headers["authorization"] == "Bearer sdk-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id", ["openai-compatible", "custom-internal"])
async def test_keyless_gateway_can_use_saved_url(provider_id, discovery_runtime, upstream):
    save_provider(discovery_runtime, provider_id, options={"baseURL": "http://gateway.test/v1"})
    upstream.handler = lambda request: httpx.Response(200, json={"data": [{"id": "local-model"}]})

    result = await discovery_runtime.module.discover_models(provider_id)

    assert result["status"] == "success"
    assert "authorization" not in upstream.requests[0].headers


@pytest.mark.asyncio
async def test_saved_credentials_are_reused_and_request_values_take_precedence(discovery_runtime, upstream):
    save_provider(
        discovery_runtime,
        "custom-saved",
        options={
            "apiKey": "saved-secret",
            "baseURL": "https://saved.test/v1",
        },
        models={"private-existing": {"name": "My Name", "supports_tools": False}},
    )
    upstream.handler = lambda request: httpx.Response(
        200,
        json={
            "data": [
                {"id": "private-existing"},
                {"id": "private-new"},
            ]
        },
    )
    original_config = discovery_runtime.config_file.read_bytes()
    original_registry = dict(Provider._providers)

    saved = await discovery_runtime.module.discover_models("custom-saved")
    requested = await discovery_runtime.module.discover_models(
        "custom-saved",
        api_key="request-secret",
        base_url="https://requested.test/api/v1",
    )

    assert saved["status"] == requested["status"] == "success"
    assert upstream.requests[0].headers["authorization"] == "Bearer saved-secret"
    assert upstream.requests[0].url.host == "saved.test"
    assert upstream.requests[1].headers["authorization"] == "Bearer request-secret"
    assert upstream.requests[1].url.host == "requested.test"
    assert saved["models"][0]["already_added"] is True
    assert saved["models"][1]["already_added"] is False
    assert discovery_runtime.config_file.read_bytes() == original_config
    assert Provider._providers == original_registry
    discovery_runtime.writes.assert_not_called()
    discovery_runtime.apply_config.assert_not_awaited()
    discovery_runtime.secrets.set.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (429, "rate_limited"),
        (500, "upstream_error"),
    ],
)
async def test_upstream_failures_do_not_masquerade_as_catalog_success(status, code, discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(
        status,
        json={
            "error": {"message": "upstream-secret echo should never be shown", "token": "upstream-secret"},
        },
    )

    result = await discovery_runtime.module.discover_models("openai", api_key="upstream-secret")

    assert result["status"] == "error"
    assert result["code"] == code
    assert result["source"] != "catalog"
    assert result["models"] == []
    assert "upstream-secret" not in json.dumps(result)
    assert "echo should never be shown" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ReadTimeout, "connection_timeout"),
        (httpx.ConnectError, "connection_error"),
    ],
)
async def test_transport_failures_are_structured_and_redacted(failure, code, discovery_runtime, upstream):
    def respond(request):
        raise failure("URL contains sensitive-test-key", request=request)

    upstream.handler = respond
    result = await discovery_runtime.module.discover_models("openai", api_key="sensitive-test-key")

    assert result["status"] == "error"
    assert result["code"] == code
    assert "sensitive-test-key" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"<html>not JSON</html>", b'{"unexpected": []}'])
async def test_malformed_upstream_response_is_not_success(payload, discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(200, content=payload)

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "error"
    assert result["code"] == "invalid_response"
    assert result["models"] == []


@pytest.mark.asyncio
async def test_empty_api_result_is_success_without_catalog_replacement(discovery_runtime, upstream):
    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "success"
    assert result["source"] == "api"
    assert result["models"] == []


@pytest.mark.asyncio
async def test_redirect_never_forwards_credentials_to_another_host(discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(302, headers={"location": "https://attacker.test/models"})

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "error"
    assert len(upstream.requests) == 1
    assert upstream.requests[0].url.host != "attacker.test"


@pytest.mark.asyncio
async def test_oversized_stream_is_rejected_before_json_processing(discovery_runtime, upstream):
    body = b" " * (discovery_runtime.module.MAX_RESPONSE_BYTES + 1)
    upstream.handler = lambda request: httpx.Response(200, content=body)

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "error"
    assert result["code"] == "response_too_large"


@pytest.mark.asyncio
async def test_non_http_url_is_rejected_without_network(discovery_runtime, upstream):
    result = await discovery_runtime.module.discover_models(
        "openai-compatible",
        base_url="file:///etc/passwd",
    )

    assert result["status"] == "error"
    assert result["code"] == "invalid_base_url"
    assert not upstream.requests


@pytest.mark.asyncio
async def test_missing_url_and_key_are_distinguished(discovery_runtime, upstream):
    missing_url = await discovery_runtime.module.discover_models("openai-compatible")
    missing_key = await discovery_runtime.module.discover_models("openai")

    assert missing_url["code"] == "missing_base_url"
    assert missing_key["code"] == "missing_credentials"
    assert not upstream.requests


@pytest.mark.asyncio
async def test_catalog_metadata_is_not_fabricated_for_unknown_upstream_model(discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(200, json={"data": [{"id": "unreleased-private-model"}]})

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    candidate = result["models"][0]
    for field in ("context_window", "max_output_tokens", "supports_vision", "supports_tools", "supports_reasoning"):
        assert candidate.get(field) is None


@pytest.mark.asyncio
async def test_environment_credentials_work_without_a_saved_secret(discovery_runtime, upstream, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://environment.test/prefix/v1")

    result = await discovery_runtime.module.discover_models("openai")

    assert result["status"] == "success"
    assert upstream.requests[0].headers["authorization"] == "Bearer environment-key"
    assert upstream.requests[0].url.host == "environment.test"
    discovery_runtime.secrets.set.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_saved_secret_is_resolved_without_reveal(discovery_runtime, upstream):
    discovery_runtime.secrets.get.side_effect = lambda name: {
        "openai_api_key": "legacy-key",
    }.get(name)

    result = await discovery_runtime.module.discover_models("openai")

    assert result["status"] == "success"
    assert upstream.requests[0].headers["authorization"] == "Bearer legacy-key"
    assert "legacy-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_resolved_nonstandard_secret_reference_is_used(discovery_runtime, upstream, monkeypatch):
    save_provider(
        discovery_runtime,
        "custom-resolved",
        options={
            "apiKey": "{secret:team-specific-key}",
            "baseURL": "https://resolved.test/v1",
        },
    )
    resolved = ConfigInfo.model_validate(
        {
            "provider": {
                "custom-resolved": {
                    "options": {
                        "apiKey": "resolved-team-secret",
                        "baseURL": "https://resolved.test/v1",
                    }
                }
            }
        }
    )
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=resolved))

    result = await discovery_runtime.module.discover_models("custom-resolved")

    assert result["status"] == "success"
    assert upstream.requests[0].headers["authorization"] == "Bearer resolved-team-secret"


@pytest.mark.asyncio
async def test_external_next_url_is_never_followed_and_result_reports_truncation(discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(
        200,
        json={
            "data": [{"id": "first-model"}],
            "next_url": "https://attacker.test/models",
        },
    )

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "success"
    assert result["truncated"] is True
    assert len(upstream.requests) == 1
    assert result["models"][0]["id"] == "first-model"


@pytest.mark.asyncio
async def test_repeated_cursor_is_bounded_and_deduplicated(discovery_runtime, upstream):
    upstream.handler = lambda request: httpx.Response(
        200,
        json={
            "data": [{"id": "same-model"}],
            "has_more": True,
            "last_id": "same-model",
        },
    )

    result = await discovery_runtime.module.discover_models("anthropic", api_key="test-key")

    assert result["status"] == "success"
    assert result["truncated"] is True
    assert len(upstream.requests) <= discovery_runtime.module.MAX_PAGES
    assert [m["id"] for m in result["models"]] == ["same-model"]


@pytest.mark.asyncio
async def test_model_count_limit_is_explicit(discovery_runtime, upstream, monkeypatch):
    monkeypatch.setattr(discovery_runtime.module, "MAX_MODELS", 2)
    upstream.handler = lambda request: httpx.Response(
        200,
        json={
            "data": [
                {"id": "first"},
                {"id": "second"},
                {"id": "third"},
            ]
        },
    )

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "success"
    assert result["truncated"] is True
    assert [m["id"] for m in result["models"]] == ["first", "second"]


@pytest.mark.asyncio
async def test_second_page_failure_is_not_replaced_by_reference_catalog(discovery_runtime, upstream):
    def respond(request):
        if "after_id" in request.url.params:
            return httpx.Response(500, json={"error": "failed second page"})
        return httpx.Response(
            200,
            json={
                "data": [{"id": "first-page"}],
                "has_more": True,
                "last_id": "first-page",
            },
        )

    upstream.handler = respond
    result = await discovery_runtime.module.discover_models("anthropic", api_key="test-key")

    assert result["status"] == "error"
    assert result["code"] == "upstream_error"
    assert result["source"] != "catalog"
    assert result["models"] == []


@pytest.mark.asyncio
async def test_invalid_compressed_response_is_a_structured_error(discovery_runtime, upstream):
    class BrokenGzipStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"invalid gzip bytes"

    upstream.handler = lambda request: httpx.Response(
        200,
        headers={"content-encoding": "gzip"},
        stream=BrokenGzipStream(),
    )

    result = await discovery_runtime.module.discover_models("openai", api_key="test-key")

    assert result["status"] == "error"
    assert result["code"] == "invalid_response"
    assert result["models"] == []
