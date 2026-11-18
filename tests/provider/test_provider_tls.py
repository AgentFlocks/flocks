"""SSL settings of LLM provider clients: ``verify_ssl`` and ``ca_cert`` (flocks.provider.tls).

The handshake tests run real TLS against a gateway on 127.0.0.1 whose
certificate is made for the test: self-signed, issued by an internal CA, or
offered over RSA key exchange only, as older appliances do.
"""

from __future__ import annotations

import json
import ssl
from unittest.mock import MagicMock

import certifi
import httpx
import pytest

from flocks.config.config_writer import ConfigWriter
from flocks.provider import tls
from flocks.provider.provider import ChatMessage, Provider, ProviderConfig
from flocks.provider.sdk.anthropic import AnthropicProvider
from flocks.provider.sdk.azure import AzureProvider
from flocks.provider.sdk.openai import OpenAIProvider
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider
from tests.provider.tls_helpers import (
    RSA_KEY_EXCHANGE_ONLY,
    assert_no_verify_context,
    https_gateway,
    make_ca,
    make_server,
)


@pytest.fixture(scope="module")
def internal_ca():
    return make_ca()


# ---------------------------------------------------------------------------
# Settings -> httpx ``verify``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("settings", [None, {}, {"verify_ssl": True}, {"verify_ssl": True, "ca_cert": "   "}])
def test_without_settings_httpx_keeps_its_own_checks(settings):
    assert tls.httpx_verify(settings) is True
    assert tls.has_custom_tls(settings) is False


@pytest.mark.parametrize(
    "settings",
    [
        {"verify_ssl": False},
        {"verify_ssl": "false"},
        {"ssl_verify": 0},
        # The certificate is not used while the checks are off.
        {"verify_ssl": False, "ca_cert": "relative/path.pem"},
    ],
)
def test_checks_off_also_accepts_older_handshakes(settings):
    context = tls.httpx_verify(settings)

    assert_no_verify_context(context)
    assert context.minimum_version == ssl.TLSVersion.MINIMUM_SUPPORTED
    assert context.options & ssl.OP_LEGACY_SERVER_CONNECT
    cipher_names = {cipher["name"] for cipher in context.get_ciphers()}
    assert "AES128-GCM-SHA256" in cipher_names  # RSA key exchange, left out by CPython's default list
    assert tls.has_custom_tls(settings) is True


def test_checks_off_builds_no_deprecated_protocol_setting():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        tls.compat_context()


def test_ca_cert_is_trusted_besides_the_default_bundle(internal_ca):
    context = tls.httpx_verify({"ca_cert": internal_ca.cert_pem})

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    default_cas = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert context.cert_store_stats()["x509_ca"] == default_cas + 1
    assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_ca_cert_path_and_ssl_cert_file_are_both_trusted(internal_ca, tmp_path, monkeypatch):
    other_ca = make_ca()
    bundle = tmp_path / "system-bundle.pem"
    bundle.write_text(other_ca.cert_pem)
    ca_file = tmp_path / "corp-ca.pem"
    ca_file.write_text(internal_ca.cert_pem)
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    context = tls.httpx_verify({"ca_cert": str(ca_file)})
    assert context.cert_store_stats()["x509_ca"] == 2

    # trust_env off: certifi, as httpx itself does.
    context = tls.httpx_verify({"ca_cert": str(ca_file)}, trust_env=False)
    default_cas = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert context.cert_store_stats()["x509_ca"] == default_cas + 1


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("certs/corp-ca.pem", "绝对路径"),
        ("/nonexistent/flocks-test/corp-ca.pem", "找不到 CA 证书文件"),
        ("-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----\n", "无法解析"),
        ("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n", "不要粘贴私钥"),  # secret-guard: allow (test value)
    ],
)
def test_a_ca_cert_that_does_not_load_is_refused(value, message):
    with pytest.raises(tls.CACertError, match=message):
        tls.validate_ca_cert(value)
    with pytest.raises(tls.CACertError):
        tls.httpx_verify({"ca_cert": value})


def test_a_file_without_certificates_is_refused(tmp_path):
    empty = tmp_path / "empty.pem"
    empty.write_text("# nothing here\n")
    with pytest.raises(tls.CACertError):
        tls.validate_ca_cert(str(empty))


def test_validate_ca_cert_keeps_good_values(internal_ca, tmp_path):
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text(internal_ca.cert_pem)
    assert tls.validate_ca_cert(None) is None
    assert tls.validate_ca_cert("  \n") is None
    assert tls.validate_ca_cert("\n" + internal_ca.cert_pem) == internal_ca.cert_pem.strip()
    assert tls.validate_ca_cert(f" {ca_file} ") == str(ca_file)
    assert tls.validate_ca_cert(str(tmp_path)) == str(tmp_path)  # hashed directory, read lazily


def test_tls_errors_are_recognised_through_the_wrappers():
    cert_error = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    wrapped = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    wrapped.__cause__ = cert_error
    outer = RuntimeError("Connection error.")
    outer.__cause__ = wrapped
    assert tls.classify_tls_error(outer) == tls.TLS_CERTIFICATE_FAILED

    handshake = httpx.ConnectError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert handshake failure")
    assert tls.classify_tls_error(handshake) == tls.TLS_HANDSHAKE_FAILED
    assert "TLS 握手失败" in tls.tls_error_hint(handshake)

    assert tls.classify_tls_error(httpx.ConnectError("[Errno 61] Connection refused")) is None
    assert tls.tls_error_hint(ValueError("x")) is None


# ---------------------------------------------------------------------------
# Real handshakes through the provider clients
# ---------------------------------------------------------------------------


def _provider(cls, base_url: str, settings: dict):
    provider = cls()
    provider.configure(
        ProviderConfig(provider_id=provider.id, api_key="sk-test", base_url=base_url, custom_settings=settings)  # secret-guard: allow (test value)
    )
    return provider


async def _ask(provider, *, stream: bool) -> str:
    messages = [ChatMessage(role="user", content="capital of France?")]
    if stream:
        parts = []
        async for chunk in provider.chat_stream("gw-test-model", messages, max_tokens=10):
            if getattr(chunk, "delta", None):
                parts.append(chunk.delta)
        return "".join(parts)
    response = await provider.chat("gw-test-model", messages, max_tokens=10)
    return response.content


PROVIDERS = [OpenAICompatibleProvider, OpenAIProvider]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
async def test_checks_off_reach_a_gateway_that_offers_rsa_key_exchange_only(provider_cls, stream, tmp_path):
    with https_gateway(
        make_server(), tmp_path, ciphers=RSA_KEY_EXCHANGE_ONLY, maximum_version=ssl.TLSVersion.TLSv1_2
    ) as base_url:
        with pytest.raises(Exception) as caught:
            await _ask(_provider(provider_cls, base_url, {}), stream=stream)
        assert tls.classify_tls_error(caught.value) == tls.TLS_HANDSHAKE_FAILED

        assert await _ask(_provider(provider_cls, base_url, {"verify_ssl": False}), stream=stream) == "Paris"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
async def test_internal_ca_gateway_needs_the_ca_or_checks_off(provider_cls, stream, internal_ca, tmp_path):
    ca_file = tmp_path / "corp-ca.pem"
    ca_file.write_text(internal_ca.cert_pem)
    with https_gateway(make_server(internal_ca), tmp_path) as base_url:
        with pytest.raises(Exception) as caught:
            await _ask(_provider(provider_cls, base_url, {}), stream=stream)
        assert tls.classify_tls_error(caught.value) == tls.TLS_CERTIFICATE_FAILED

        for settings in ({"ca_cert": internal_ca.cert_pem}, {"ca_cert": str(ca_file)}, {"verify_ssl": False}):
            assert await _ask(_provider(provider_cls, base_url, settings), stream=stream) == "Paris"

        # Another CA does not make the gateway trusted.
        with pytest.raises(Exception) as caught:
            await _ask(_provider(provider_cls, base_url, {"ca_cert": make_ca().cert_pem}), stream=stream)
        assert tls.classify_tls_error(caught.value) == tls.TLS_CERTIFICATE_FAILED


@pytest.mark.asyncio
async def test_a_self_signed_gateway_certificate_can_be_trusted_as_is(tmp_path):
    gateway = make_server()
    with https_gateway(gateway, tmp_path) as base_url:
        provider = _provider(OpenAICompatibleProvider, base_url, {"ca_cert": gateway.cert_pem})
        assert await _ask(provider, stream=False) == "Paris"


# ---------------------------------------------------------------------------
# Clients built by the provider SDKs
# ---------------------------------------------------------------------------


def _ssl_context(httpx_client) -> ssl.SSLContext:
    return httpx_client._transport._pool._ssl_context


@pytest.mark.parametrize(
    ("factory", "settings_key"),
    [
        (lambda settings: _provider(AnthropicProvider, "https://gateway.internal", settings), "anthropic"),
        (
            lambda settings: _provider(AzureProvider, "https://gateway.internal/", {**settings, "api_version": "2025-04-01-preview"}),
            "azure",
        ),
    ],
)
def test_anthropic_and_azure_clients_follow_the_settings(factory, settings_key, internal_ca):
    relaxed = factory({"verify_ssl": False})._get_client()
    assert_no_verify_context(_ssl_context(relaxed._client))

    with_ca = factory({"ca_cert": internal_ca.cert_pem})._get_client()
    context = _ssl_context(with_ca._client)
    assert context.verify_mode == ssl.CERT_REQUIRED
    default_cas = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert context.cert_store_stats()["x509_ca"] == default_cas + 1

    default = factory({})._get_client()
    assert _ssl_context(default._client).verify_mode == ssl.CERT_REQUIRED


def _tls_provider_classes():
    """Every SDK provider class that says its client follows the settings."""
    import importlib
    import inspect
    import pkgutil

    import flocks.provider.sdk as sdk
    from flocks.provider.provider import BaseProvider

    found = {}
    for info in pkgutil.iter_modules(sdk.__path__):
        module = importlib.import_module(f"flocks.provider.sdk.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseProvider) and cls.__module__ == module.__name__ and cls.SUPPORTS_TLS_SETTINGS:
                found[cls.__qualname__] = cls
    return sorted(found.items())


TLS_PROVIDER_CLASSES = _tls_provider_classes()


@pytest.mark.parametrize(("name", "cls"), TLS_PROVIDER_CLASSES, ids=[name for name, _ in TLS_PROVIDER_CLASSES])
def test_every_provider_offering_the_settings_applies_them(name, cls, internal_ca):
    """A subclass that builds its own client must not drop the settings it claims to follow."""
    try:
        cls()
    except TypeError:
        pytest.skip("base class: needs constructor arguments")

    def client_for(settings):
        provider = cls()
        provider.configure(
            ProviderConfig(
                provider_id=provider.id,
                api_key="sk-test",  # secret-guard: allow (test value)
                base_url="https://gateway.internal/v1",
                custom_settings={**settings, "api_version": "2025-04-01-preview"},
            )
        )
        return provider._get_client()._client

    assert_no_verify_context(_ssl_context(client_for({"verify_ssl": False})))
    with_ca = _ssl_context(client_for({"ca_cert": internal_ca.cert_pem}))
    default_cas = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert with_ca.verify_mode == ssl.CERT_REQUIRED
    assert with_ca.cert_store_stats()["x509_ca"] == default_cas + 1
    assert _ssl_context(client_for({})).verify_mode == ssl.CERT_REQUIRED


def test_providers_that_offer_the_settings():
    for cls in (OpenAICompatibleProvider, OpenAIProvider, AnthropicProvider, AzureProvider):
        assert cls.SUPPORTS_TLS_SETTINGS is True
    from flocks.provider.sdk.deepseek import DeepSeekProvider
    from flocks.provider.sdk.google import GoogleProvider
    from flocks.provider.sdk.xai import XAIProvider

    assert DeepSeekProvider.SUPPORTS_TLS_SETTINGS is True  # OpenAIBaseProvider
    assert GoogleProvider.SUPPORTS_TLS_SETTINGS is False
    assert XAIProvider.SUPPORTS_TLS_SETTINGS is False


# ---------------------------------------------------------------------------
# Settings saved and read through the provider routes
# ---------------------------------------------------------------------------


@pytest.fixture
def saved_config(tmp_path, monkeypatch):
    """A flocks.json in a temporary directory, an empty registry, in-memory secrets."""
    config_file = tmp_path / "flocks.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ConfigWriter, "_get_config_path", lambda: config_file)
    monkeypatch.setattr(Provider, "_providers", {})
    monkeypatch.setattr(Provider, "_models", {})
    monkeypatch.setattr(Provider, "_initialized", True)
    store: dict = {}
    secrets = MagicMock()
    secrets.get.side_effect = store.get
    secrets.set.side_effect = store.__setitem__
    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: secrets)
    from flocks.server.routes import provider as provider_routes

    monkeypatch.setattr(provider_routes.Config, "clear_cache", MagicMock())

    def write(providers: dict) -> None:
        config_file.write_text(json.dumps({"provider": providers}), encoding="utf-8")

    def read(provider_id: str) -> dict:
        return json.loads(config_file.read_text(encoding="utf-8"))["provider"][provider_id]

    return MagicMock(write=write, read=read, secrets=store)


@pytest.mark.asyncio
async def test_credentials_save_and_read_the_tls_settings(saved_config, internal_ca):
    from flocks.server.routes import provider as provider_routes

    saved_config.write({
        "custom-gw": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Gateway",
            "options": {"apiKey": "{secret:custom-gw_llm_key}", "baseURL": "https://gw.internal/v1", "ssl_verify": True},
            "models": {},
        }
    })
    saved_config.secrets["custom-gw_llm_key"] = "sk-test"

    before = provider_routes._load_llm_provider_credentials("custom-gw")
    assert (before.verify_ssl, before.ca_cert, before.tls_configurable) == (True, None, True)

    await provider_routes.set_provider_credentials(
        "custom-gw", provider_routes.ProviderCredentialRequest(verify_ssl=False, ca_cert=internal_ca.cert_pem)
    )
    options = saved_config.read("custom-gw")["options"]
    assert options["verify_ssl"] is False
    assert "ssl_verify" not in options  # the older spelling would contradict the new value
    assert options["ca_cert"] == internal_ca.cert_pem.strip()
    assert options["baseURL"] == "https://gw.internal/v1"
    after = provider_routes._load_llm_provider_credentials("custom-gw")
    assert (after.verify_ssl, after.ca_cert) == (False, internal_ca.cert_pem.strip())

    # Back to the defaults: no TLS keys are left behind.
    await provider_routes.set_provider_credentials(
        "custom-gw", provider_routes.ProviderCredentialRequest(verify_ssl=True, ca_cert="")
    )
    options = saved_config.read("custom-gw")["options"]
    assert not {"verify_ssl", "ssl_verify", "ca_cert"} & set(options)

    # Leaving the fields out changes nothing.
    await provider_routes.set_provider_credentials(
        "custom-gw", provider_routes.ProviderCredentialRequest(verify_ssl=False)
    )
    await provider_routes.set_provider_credentials(
        "custom-gw", provider_routes.ProviderCredentialRequest(base_url="https://gw2.internal/v1")
    )
    assert saved_config.read("custom-gw")["options"]["verify_ssl"] is False


@pytest.mark.asyncio
async def test_a_bad_ca_cert_is_refused_before_anything_is_saved(saved_config):
    from fastapi import HTTPException

    from flocks.server.routes import provider as provider_routes

    saved_config.write({
        "custom-gw": {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "https://gw.internal/v1"}, "models": {}}
    })
    with pytest.raises(HTTPException) as caught:
        await provider_routes.set_provider_credentials(
            "custom-gw",
            provider_routes.ProviderCredentialRequest(api_key="sk-new", ca_cert="certs/ca.pem"),  # secret-guard: allow (test value)
        )
    assert caught.value.status_code == 400
    assert "绝对路径" in caught.value.detail
    assert saved_config.read("custom-gw")["options"] == {"baseURL": "https://gw.internal/v1"}
    assert "custom-gw_llm_key" not in saved_config.secrets


@pytest.mark.asyncio
async def test_providers_whose_client_ignores_the_settings_refuse_them(saved_config):
    from fastapi import HTTPException

    from flocks.server.routes import provider as provider_routes

    saved_config.write({"google": {"npm": "@ai-sdk/google", "options": {}, "models": {}}})
    assert provider_routes._load_llm_provider_credentials("google").tls_configurable is False
    with pytest.raises(HTTPException) as caught:
        await provider_routes.set_provider_credentials(
            "google", provider_routes.ProviderCredentialRequest(api_key="k", verify_ssl=False)
        )
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_a_new_provider_is_created_with_its_tls_settings(saved_config):
    from flocks.server.routes import provider as provider_routes

    await provider_routes.set_provider_credentials(
        "anthropic",
        provider_routes.ProviderCredentialRequest(
            api_key="sk-ant", base_url="https://claude-gw.internal", verify_ssl=False, skip_catalog_bootstrap=True  # secret-guard: allow (test value)
        ),
    )
    assert saved_config.read("anthropic")["options"]["verify_ssl"] is False


@pytest.mark.asyncio
async def test_catalog_says_which_providers_offer_the_settings(saved_config):
    from flocks.server.routes import provider as provider_routes

    catalog = {entry["id"]: entry for entry in (await provider_routes.get_provider_catalog())["providers"]}
    assert catalog["openai-compatible"]["tls_configurable"] is True
    assert catalog["anthropic"]["tls_configurable"] is True
    assert catalog["azure-openai"]["tls_configurable"] is True
    assert catalog["google"]["tls_configurable"] is False


@pytest.mark.asyncio
async def test_custom_provider_is_created_with_its_tls_settings(saved_config, internal_ca):
    from fastapi import HTTPException

    from flocks.server.routes import custom_provider

    created = await custom_provider.create_provider(
        custom_provider.CreateProviderReq(
            name="corp gw", base_url="https://gw.internal/v1", api_key="sk-test", ca_cert=internal_ca.cert_pem  # secret-guard: allow (test value)
        ),
        _admin=None,
    )
    options = saved_config.read(created.id)["options"]
    assert options["ca_cert"] == internal_ca.cert_pem.strip()
    assert "verify_ssl" not in options
    runtime = Provider._providers[created.id]
    assert runtime._config.custom_settings == {"ca_cert": internal_ca.cert_pem.strip()}

    created = await custom_provider.create_provider(
        custom_provider.CreateProviderReq(name="old gw", base_url="https://old.internal/v1", verify_ssl=False),
        _admin=None,
    )
    assert saved_config.read(created.id)["options"]["verify_ssl"] is False

    with pytest.raises(HTTPException) as caught:
        await custom_provider.create_provider(
            custom_provider.CreateProviderReq(name="bad gw", base_url="https://bad.internal/v1", ca_cert="ca.pem"),
            _admin=None,
        )
    assert caught.value.status_code == 422
    assert "provider" not in json.loads(ConfigWriter._get_config_path().read_text()) or "custom-bad-gw" not in json.loads(
        ConfigWriter._get_config_path().read_text()
    )["provider"]
