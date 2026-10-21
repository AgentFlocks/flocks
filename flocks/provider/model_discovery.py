"""Read-only discovery of provider model inventories.

Discovery never configures the shared SDK instance and never persists credentials.
A reference catalog is only a fallback for an unsupported listing operation.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter
from flocks.provider.model_catalog import get_raw_catalog
from flocks.provider.provider import Provider

MAX_MODELS = 1000
MAX_PAGES = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DISCOVERY_TIMEOUT_SECONDS = 20.0

# SDK defaults are also used for provider types which predate catalog.json.
_OPENAI_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "cerebras": "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "perplexity": "https://api.perplexity.ai",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "tencent": "https://api.hunyuan.cloud.tencent.com/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "threatbook-cn-llm": "https://llm.threatbook.cn/v1",
    "threatbook-io-llm": "https://llm.threatbook.io/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "minimax": "https://api.minimax.chat/v1",
    "stepfun": "https://api.stepfun.com/v1",
    "local": "http://localhost:11434/v1",
    "gateway": "http://localhost:4000",
    "vercel": "https://api.vercel.ai/v1",
    "opencode": "https://api.opencode.ai/v1",
    "zenmux": "https://api.zenmux.ai/v1",
}
_ENV_KEYS = {
    "openai": ["OPENAI_API_KEY", "LLM_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "alibaba": ["DASHSCOPE_API_KEY", "ALIBABA_API_KEY"],
    "tencent": ["HUNYUAN_API_KEY", "TENCENT_API_KEY"],
    "volcengine": ["VOLCENGINE_API_KEY", "ARK_API_KEY"],
    "azure": ["AZURE_OPENAI_API_KEY"],
    "azure-openai": ["AZURE_OPENAI_API_KEY"],
}
_COMPATIBLE_NPMS = {
    "@ai-sdk/openai-compatible",
    "@ai-sdk/cherry",
    "@ai-sdk/openai",
    "@ai-sdk/mistral",
    "@ai-sdk/groq",
    "@ai-sdk/togetherai",
    "@ai-sdk/xai",
    "@ai-sdk/deepinfra",
    "@ai-sdk/cerebras",
    "@openrouter/ai-sdk-provider",
}
_UNSUPPORTED_PROVIDERS = {
    "google-vertex",
    "google-vertex-anthropic",
    "gitlab",
    "sap-ai-core",
    "github-copilot",
    "github-copilot-enterprise",
    "cloudflare-ai-gateway",
}


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True, by_alias=False)
    return {}


def _usable(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if re.search(r"\{(?:secret|env):", value):
        return None
    return value.strip()


def _protocol(provider_id: str, raw: dict, catalog: dict) -> str | None:
    npm = raw.get("npm") or catalog.get("npm")
    if provider_id in {"azure", "azure-openai", "azure-cognitive-services"} or npm == "@ai-sdk/azure":
        return "azure"
    if provider_id in _UNSUPPORTED_PROVIDERS:
        return None
    if provider_id == "google" or npm == "@ai-sdk/google":
        return "google"
    if provider_id == "anthropic" or npm == "@ai-sdk/anthropic":
        return "anthropic"
    if provider_id == "cohere" or npm == "@ai-sdk/cohere":
        return "cohere"
    if provider_id == "ollama":
        return "ollama"
    if provider_id in _OPENAI_DEFAULTS or provider_id in {"openai-compatible", "cherry"} or npm in _COMPATIBLE_NPMS:
        return "openai"
    return None


def _no_auth_allowed(provider_id: str, raw: dict, protocol: str | None) -> bool:
    return (
        protocol == "ollama"
        or provider_id in {"openai-compatible", "cherry", "local", "gateway"}
        or (
            raw.get("npm") in {"@ai-sdk/openai-compatible", "@ai-sdk/cherry"}
            and (provider_id.startswith("custom-") or provider_id not in get_raw_catalog())
        )
    )


def _resolve_option(value: Any, secrets: Any) -> str | None:
    """Resolve one option independently of unrelated merged config failures."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\{(secret|env):([^{}]+)\}", value.strip())
    if match:
        value = secrets.get(match[2]) if match[1] == "secret" else os.getenv(match[2])
    return _usable(value)


async def _resolve_connection(
    provider_id: str, raw: dict, catalog: dict, api_key: str | None, base_url: str | None
) -> tuple[str | None, str | None]:
    resolved_options: dict = {}
    try:
        config = await Config.get()
        pconfig = (getattr(config, "provider", None) or {}).get(provider_id)
        resolved_options = _dict(
            getattr(pconfig, "options", None) if not isinstance(pconfig, dict) else pconfig.get("options")
        )
    except Exception:
        # Conventional secrets and SDK/environment defaults remain usable when
        # an unrelated configuration entry cannot be resolved.
        pass
    provider = Provider._providers.get(provider_id)
    sdk_config = getattr(provider, "_config", None)
    from flocks.security import get_secret_manager

    secrets = get_secret_manager()
    raw_options = raw.get("options") or {}
    raw_key = raw_options.get("api_key") or raw_options.get("apiKey")
    raw_url = raw_options.get("base_url") or raw_options.get("baseURL")
    stored_key = _usable(resolved_options.get("api_key") or resolved_options.get("apiKey")) or _resolve_option(
        raw_key, secrets
    )
    stored_url = _usable(resolved_options.get("base_url") or resolved_options.get("baseURL")) or _resolve_option(
        raw_url, secrets
    )
    if base_url is None and raw_url and not stored_url:
        raise DiscoveryError("missing_base_url", "The configured provider URL reference could not be resolved.")
    stored_key = (
        stored_key or _usable(secrets.get(f"{provider_id}_llm_key")) or _usable(secrets.get(f"{provider_id}_api_key"))
    )
    stored_url = stored_url or _usable(secrets.get(f"{provider_id}_base_url"))
    env_prefix = provider_id.upper().replace("-", "_")
    env_keys = _ENV_KEYS.get(provider_id, [f"{env_prefix}_API_KEY"])
    sdk_key = (
        _usable(getattr(sdk_config, "api_key", None))
        or _usable(getattr(provider, "_api_key", None))
        or _usable(getattr(provider, "api_key", None))
    )
    sdk_url = (
        _usable(getattr(sdk_config, "base_url", None))
        or _usable(getattr(provider, "_base_url", None))
        or _usable(getattr(provider, "base_url", None))
        or _usable(getattr(provider, "_endpoint", None))
    )
    env_url_names = [getattr(provider, "ENV_BASE_URL", None), f"{env_prefix}_BASE_URL"]
    if provider_id == "openai":
        env_url_names.extend(["OPENAI_API_BASE", "LLM_API_BASE"])
    elif provider_id in {"azure", "azure-openai"}:
        env_url_names.append("AZURE_OPENAI_ENDPOINT")
    elif provider_id == "local":
        env_url_names.append("LOCAL_LLM_BASE_URL")
    env_url = next(
        (_usable(os.getenv(name)) for name in env_url_names if isinstance(name, str) and _usable(os.getenv(name))), None
    )
    effective_key = (
        api_key.strip()
        if api_key is not None
        else stored_key
        or sdk_key
        or next((_usable(os.getenv(name)) for name in env_keys if _usable(os.getenv(name))), None)
    )
    effective_url = (
        base_url.strip()
        if base_url is not None
        else stored_url
        or env_url
        or sdk_url
        or _usable(getattr(provider, "DEFAULT_BASE_URL", None))
        or _usable(catalog.get("default_base_url"))
        or _OPENAI_DEFAULTS.get(provider_id)
        or {
            "anthropic": "https://api.anthropic.com",
            "google": "https://generativelanguage.googleapis.com/v1beta",
            "cohere": "https://api.cohere.com/v1",
            "ollama": "http://localhost:11434/v1",
        }.get(_protocol(provider_id, raw, catalog))
    )
    if provider_id in {"openai-compatible", "cherry"} and base_url is None and not (stored_url or env_url):
        effective_url = None
    return effective_key, effective_url


def _validate_base_url(value: str | None) -> str:
    if not value:
        raise DiscoveryError("missing_base_url", "Configure a provider base URL before fetching models.")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        if any(ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError
        parsed.port
        if host.lower().rstrip(".") in {
            "metadata.google.internal",
            "metadata",
            "instance-data",
            "metadata.azure.internal",
        }:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if getattr(address, "ipv4_mapped", None):
                address = address.ipv4_mapped
            if (
                address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or str(address) == "100.100.100.200"
            ):
                raise ValueError
        return str(httpx.URL(urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")))).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        raise DiscoveryError(
            "invalid_base_url",
            "The provider base URL must be a valid HTTP(S) endpoint without credentials, query parameters, or fragments.",
        ) from None


def _endpoint(base_url: str, protocol: str) -> str:
    if protocol == "anthropic":
        return base_url + ("/models" if base_url.endswith("/v1") else "/v1/models")
    if protocol == "google":
        return base_url + ("/models" if re.search(r"/v1(?:beta|alpha)?$", base_url) else "/v1beta/models")
    if protocol == "cohere":
        return re.sub(r"/v\d+$", "", base_url) + "/v1/models"
    if protocol == "ollama":
        return re.sub(r"/(?:v1|api)$", "", base_url) + "/api/tags"
    return base_url + "/models"


async def _validate_resolved_addresses(url: str) -> None:
    """Block metadata destinations while preserving local and private gateways."""
    host = urlsplit(url).hostname
    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM),
            timeout=3.0,
        )
    except socket.gaierror:
        # The HTTP transport owns connection error reporting; a test or custom
        # transport may also resolve names independently of the system resolver.
        return
    for address in addresses:
        target = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if getattr(target, "ipv4_mapped", None):
            target = target.ipv4_mapped
        if target.is_link_local or target.is_multicast or target.is_unspecified or str(target) == "100.100.100.200":
            raise DiscoveryError("invalid_base_url", "The provider endpoint resolves to a prohibited network address.")


async def _request_json(client: httpx.AsyncClient, url: str, headers: dict, params: dict | None = None) -> dict:
    await _validate_resolved_addresses(url)
    async with client.stream("GET", url, headers=headers, params=params) as response:
        code = response.status_code
        if code >= 300:
            error_code, message = {
                401: ("authentication_failed", "The provider rejected the credentials."),
                403: ("permission_denied", "The credentials do not have permission to list models."),
                429: ("rate_limited", "The provider is rate limiting requests. Try again later."),
                404: ("discovery_unsupported", "The provider does not support this model listing endpoint."),
                405: ("discovery_unsupported", "The provider does not support this model listing endpoint."),
                501: ("discovery_unsupported", "The provider does not support model listing."),
            }.get(
                code,
                ("redirect_not_allowed", "The model listing endpoint returned a redirect.")
                if code < 400
                else ("upstream_error", "The provider could not complete model discovery."),
            )
            raise DiscoveryError(error_code, message, status_code=code)
        # Ask for an uncompressed inventory and reject encoded streams before
        # decoding, so the body limit also protects against compression bombs.
        if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
            raise DiscoveryError("invalid_response", "The provider returned an unsupported encoded model list.")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise DiscoveryError("response_too_large", "The provider model list exceeds the response size limit.")
        try:
            result = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise DiscoveryError("invalid_response", "The provider returned an invalid model list.") from None
        if not isinstance(result, (dict, list)):
            raise DiscoveryError("invalid_response", "The provider returned an invalid model list.")
        return result


def _positive_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100_000_000 else None


def _normalize_model(item: Any, protocol: str, already_added: set[str]) -> dict | None:
    if not isinstance(item, dict):
        return None
    raw_id = item.get("name") if protocol in {"google", "cohere"} else item.get("id")
    if protocol == "ollama":
        raw_id = item.get("model") or item.get("name")
    if not isinstance(raw_id, str) or not raw_id.strip() or len(raw_id) > 512 or any(ord(char) < 32 for char in raw_id):
        return None
    model_id = raw_id.removeprefix("models/") if protocol == "google" else raw_id
    if not model_id.strip():
        return None
    name = item.get("displayName") or item.get("display_name") or item.get("name") or model_id
    name = name.strip()[:512] if isinstance(name, str) else model_id
    if not name or any(ord(char) < 32 for char in name):
        name = model_id
    result: dict = {
        "id": model_id,
        "name": name,
        "already_added": model_id in already_added,
        "selectable": True,
    }
    caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    limits = {
        "context_window": item.get("inputTokenLimit")
        if protocol == "google"
        else item.get("context_window") or item.get("context_length") or item.get("max_context_length"),
        "max_output_tokens": item.get("outputTokenLimit")
        if protocol == "google"
        else item.get("max_output_tokens") or item.get("max_output"),
    }
    for key, value in limits.items():
        if _positive_integer(value):
            result[key] = value
    for key, aliases in {
        "supports_vision": ["supports_vision", "vision"],
        "supports_tools": ["supports_tools", "function_calling", "tool_calling"],
        "supports_reasoning": ["supports_reasoning", "reasoning"],
    }.items():
        for alias in aliases:
            value = item.get(alias, caps.get(alias))
            if isinstance(value, bool):
                result[key] = value
                break
    if isinstance(item.get("input_modalities"), list):
        result["supports_vision"] = "image" in item["input_modalities"]
    methods = item.get("supportedGenerationMethods") or item.get("supported_actions")
    endpoints = item.get("endpoints")
    non_chat = (
        protocol == "google"
        and isinstance(methods, list)
        and "generateContent" not in methods
        or protocol == "cohere"
        and isinstance(endpoints, list)
        and "chat" not in endpoints
        or caps.get("completion_chat") is False
        or item.get("model_type") in {"embedding", "embeddings", "rerank", "image", "audio", "tts", "transcription"}
        or item.get("type") in {"embedding", "embeddings", "rerank", "image", "audio", "tts", "transcription"}
    )
    if non_chat:
        result.update(selectable=False, unavailable_reason="not_chat_model")
    return result


def _catalog_result(provider_id: str, raw: dict, protocol: str | None, already_added: set[str]) -> dict:
    catalog_id = "azure-openai" if protocol == "azure" else provider_id
    catalog = get_raw_catalog().get(catalog_id, {})
    candidates = []
    for model_id, model in catalog.get("models", {}).items():
        item = {
            "id": model_id,
            "name": model.get("name", model_id),
            **model.get("capabilities", {}),
            "model_type": model.get("model_type", "llm"),
            **model.get("limits", {}),
        }
        normalized = _normalize_model(item, "openai", already_added)
        if normalized:
            candidates.append(normalized)
    # Legacy SDK provider definitions are a reference, not evidence of account
    # permissions. Never manufacture a catalog for an unknown custom provider.
    if not candidates and provider_id in _UNSUPPORTED_PROVIDERS:
        provider = Provider._providers.get(provider_id)
        if provider:
            for model in provider.get_models():
                candidates.append(
                    {"id": model.id, "name": model.name, "already_added": model.id in already_added, "selectable": True}
                )
    if protocol == "azure":
        for model in candidates:
            model.update(selectable=False, unavailable_reason="azure_deployment_required")
    return {
        "source": "catalog" if candidates else "none",
        "status": "fallback" if candidates else "error",
        "code": "catalog_fallback" if candidates else "discovery_unsupported",
        "message": "Azure requires your deployment name. Reference model names cannot be imported as deployments."
        if protocol == "azure"
        else "This provider does not expose a supported model list. Reference catalog models are shown; account access has not been verified."
        if candidates
        else "This provider does not expose a supported model list. Add model IDs manually.",
        "truncated": len(candidates) > MAX_MODELS,
        "models": candidates[:MAX_MODELS],
    }


async def discover_models(
    provider_id: str, *, api_key: str | None = None, base_url: str | None = None
) -> dict[str, Any]:
    """Fetch models without saving configuration or mutating a provider client."""
    raw = ConfigWriter.get_provider_raw(provider_id) or {}
    catalog = get_raw_catalog().get(provider_id, {})
    protocol = _protocol(provider_id, raw, catalog)
    already_added = set((raw.get("models") or {}).keys())
    fetched_at = datetime.now(UTC).isoformat()
    try:
        if protocol is None or protocol == "azure":
            result = _catalog_result(provider_id, raw, protocol, already_added)
        else:
            async with asyncio.timeout(DISCOVERY_TIMEOUT_SECONDS):
                key, url = await _resolve_connection(provider_id, raw, catalog, api_key, base_url)
                url = _endpoint(_validate_base_url(url), protocol)
                if not key and not _no_auth_allowed(provider_id, raw, protocol):
                    raise DiscoveryError("missing_credentials", "Configure credentials before fetching models.")
                headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
                if key and key not in {"not-needed", "ollama"}:
                    headers[
                        "x-goog-api-key"
                        if protocol == "google"
                        else "x-api-key"
                        if protocol == "anthropic"
                        else "Authorization"
                    ] = key if protocol in {"google", "anthropic"} else f"Bearer {key}"
                if protocol == "anthropic":
                    headers["anthropic-version"] = "2023-06-01"
                params = (
                    {"limit": 1000}
                    if protocol == "anthropic"
                    else {"pageSize": 1000}
                    if protocol == "google"
                    else {"page_size": 1000, "endpoint": "chat"}
                    if protocol == "cohere"
                    else {}
                )
                models: dict[str, dict] = {}
                seen_cursors: set[str] = set()
                truncated = False
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False
                ) as client:
                    for page in range(MAX_PAGES):
                        payload = await _request_json(client, url, headers, params)
                        items = (
                            payload
                            if isinstance(payload, list)
                            else payload.get("models")
                            if protocol in {"google", "cohere", "ollama"}
                            else payload.get("data")
                        )
                        if not isinstance(items, list):
                            raise DiscoveryError("invalid_response", "The provider returned an invalid model list.")
                        for item in items:
                            model = _normalize_model(item, protocol, already_added)
                            if model and model["id"] not in models:
                                if len(models) >= MAX_MODELS:
                                    truncated = True
                                    break
                                models[model["id"]] = model
                        cursor = None
                        cursor_param = None
                        if isinstance(payload, dict):
                            if protocol == "google":
                                cursor, cursor_param = payload.get("nextPageToken"), "pageToken"
                            elif protocol == "cohere":
                                cursor, cursor_param = payload.get("next_page_token"), "page_token"
                            elif protocol == "anthropic" and payload.get("has_more"):
                                cursor, cursor_param = payload.get("last_id"), "after_id"
                            elif payload.get("has_more"):
                                cursor, cursor_param = (
                                    payload.get("last_id")
                                    or (items[-1].get("id") if items and isinstance(items[-1], dict) else None),
                                    "after",
                                )
                            # Absolute pagination links from arbitrary providers
                            # are not followed, so credentials stay on this origin.
                            elif payload.get("next") or payload.get("next_url") or payload.get("nextPage"):
                                truncated = True
                        if not cursor:
                            if isinstance(payload, dict) and payload.get("has_more"):
                                truncated = True
                            break
                        if (
                            not isinstance(cursor, str)
                            or cursor in seen_cursors
                            or page == MAX_PAGES - 1
                            or len(models) >= MAX_MODELS
                        ):
                            truncated = True
                            break
                        seen_cursors.add(cursor)
                        params[cursor_param] = cursor
                result = {
                    "source": "api",
                    "status": "success",
                    "code": "models_discovered",
                    "message": "Models fetched from the provider. Select models to add to your inventory.",
                    "truncated": truncated,
                    "models": list(models.values()),
                }
    except DiscoveryError as error:
        if error.status_code in {404, 405, 501}:
            result = _catalog_result(provider_id, raw, protocol, already_added)
        else:
            result = {
                "source": "none",
                "status": "error",
                "code": error.code,
                "message": error.message,
                "truncated": False,
                "models": [],
            }
    except (TimeoutError, httpx.TimeoutException):
        result = {
            "source": "none",
            "status": "error",
            "code": "connection_timeout",
            "message": "Model discovery timed out. Try again later.",
            "truncated": False,
            "models": [],
        }
    except httpx.DecodingError:
        result = {
            "source": "none",
            "status": "error",
            "code": "invalid_response",
            "message": "The provider returned an invalid encoded model list.",
            "truncated": False,
            "models": [],
        }
    except httpx.TransportError:
        result = {
            "source": "none",
            "status": "error",
            "code": "connection_error",
            "message": "Could not connect to the provider model listing endpoint.",
            "truncated": False,
            "models": [],
        }
    result["fetched_at"] = fetched_at
    return result
