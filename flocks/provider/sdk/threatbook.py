"""ThreatBook LLM provider implementations.

The China service is backed by Flocks Router. Router's model catalog is the
authority for enabled models, names, prices, versions, and input-token price
tiers. The bundled catalog remains an offline fallback and supplies
capability/limit metadata that Router does not expose.
"""

import asyncio
import os
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

from flocks.provider.model_catalog import get_provider_model_definitions
from flocks.provider.provider import ModelCapabilities, ModelInfo
from flocks.provider.sdk.openai_base import (
    OpenAIBaseProvider,
    _coerce_bool,
    resolve_verify_ssl,
)


class ThreatBookCnLLMProvider(OpenAIBaseProvider):
    """ThreatBook-China LLM provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://llm.threatbook.cn/v1"
    DEFAULT_MODEL_CATALOG_URL = (
        "https://flocks-router-test.threatbook-inc.cn/api/console/common/models"
    )
    ENV_API_KEY = ["THREATBOOK_CN_LLM_API_KEY"]
    ENV_BASE_URL = "THREATBOOK_CN_LLM_BASE_URL"
    CATALOG_ID = "threatbook-cn-llm"
    MODEL_CATALOG_CACHE_TTL_SECONDS = 60.0
    MODEL_CATALOG_TIMEOUT_SECONDS = 5.0

    def __init__(self):
        super().__init__(provider_id="threatbook-cn-llm", name="ThreatBook-cn-llm")
        self._router_models: Optional[list[ModelInfo]] = None
        self._router_models_url: Optional[str] = None
        self._router_models_last_attempt = 0.0
        self._router_models_last_attempt_url: Optional[str] = None
        self._router_models_lock = asyncio.Lock()

    def get_models(self) -> list[ModelInfo]:
        """Return Router models after the first successful discovery.

        ``None`` and ``[]`` intentionally mean different things: ``None`` has
        never been refreshed successfully and falls back to flocks.json;
        ``[]`` is a valid authoritative response when Router has no active
        models.
        """
        if self._router_models is not None:
            return list(self._router_models)
        if not getattr(self, "_config_models", []):
            return []
        # Existing installations can contain obsolete price fields in
        # flocks.json. Build the offline fallback from the bundled Router
        # snapshot so those stale values cannot override current defaults.
        fallback_rows = []
        for model in get_provider_model_definitions(self.CATALOG_ID):
            row: dict[str, Any] = {"model_name": model.name}
            if model.pricing:
                row["input_price"] = model.pricing.input
                row["output_price"] = model.pricing.output
                row["price_tiers"] = (
                    [tier.model_dump() for tier in model.pricing.price_tiers] if model.pricing.price_tiers else None
                )
                row["price_version"] = model.pricing.price_version
            fallback_rows.append(row)
        return self._build_router_models(fallback_rows)

    @property
    def model_catalog_is_authoritative(self) -> bool:
        """Router (or its bundled snapshot) owns this provider's model list."""
        return True

    def _get_model_definition_source_models(self) -> list[ModelInfo]:
        return self.get_models()

    def _model_catalog_urls(self) -> list[str]:
        """Return catalog sources in consistency-preferred order.

        Console's common-model endpoint is the fee-details page's exact data
        source. It currently requires a Passport ``session_token`` cookie, so
        the API-key-protected ``/v1/models`` endpoint remains the runtime
        fallback for ordinary Flocks installations. Both response shapes are
        parsed identically below.
        """
        custom_settings = getattr(self._config, "custom_settings", None) or {}
        configured_url = (
            custom_settings.get("model_catalog_url") if isinstance(custom_settings, dict) else None
        ) or os.getenv("THREATBOOK_CN_LLM_MODEL_CATALOG_URL")

        catalog_url = configured_url or self.DEFAULT_MODEL_CATALOG_URL
        urls = [catalog_url]
        if "/api/console/" in urlsplit(catalog_url).path:
            parsed = urlsplit(catalog_url)
            origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            urls.append(f"{origin}/v1/models")
        return list(dict.fromkeys(urls))

    @staticmethod
    def _positive_float_env(
        name: str,
        default: float,
        *,
        allow_zero: bool = False,
    ) -> float:
        try:
            value = float(os.getenv(name, default))
        except (TypeError, ValueError):
            return default
        return value if value > 0 or (allow_zero and value == 0) else default

    @staticmethod
    def _price_value(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @classmethod
    def _router_price_tiers(cls, raw: dict[str, Any]) -> list[dict[str, Any]]:
        tiers = raw.get("price_tiers", raw.get("priceTiers"))
        if not isinstance(tiers, list):
            return []

        parsed_tiers: list[dict[str, Any]] = []
        for tier in tiers:
            if not isinstance(tier, dict):
                continue
            input_price = cls._price_value(tier.get("input_price", tier.get("inputPrice")))
            output_price = cls._price_value(tier.get("output_price", tier.get("outputPrice")))
            if input_price is None or output_price is None:
                continue

            raw_max = tier.get("max_input_tokens", tier.get("maxInputTokens"))
            if raw_max is None:
                max_input_tokens = None
            elif isinstance(raw_max, bool):
                continue
            else:
                try:
                    max_input_tokens = int(raw_max)
                except (TypeError, ValueError):
                    continue
                if max_input_tokens < 0:
                    continue
            parsed_tiers.append(
                {
                    "max_input_tokens": max_input_tokens,
                    "input_price": input_price,
                    "output_price": output_price,
                }
            )

        return sorted(
            parsed_tiers,
            key=lambda tier: (
                tier["max_input_tokens"] is None,
                tier["max_input_tokens"] or 0,
            ),
        )

    @classmethod
    def _router_default_prices(
        cls,
        raw: dict[str, Any],
    ) -> tuple[Optional[float], Optional[float]]:
        """Resolve the price displayed by Router's fee-details page.

        For tiered models, Router documents ``input_price`` / ``output_price``
        as the first tier. Prefer the explicit first tier defensively so Flocks
        still matches the fee page if those fields temporarily drift.
        """
        input_price = cls._price_value(raw.get("input_price", raw.get("inputPrice")))
        output_price = cls._price_value(raw.get("output_price", raw.get("outputPrice")))
        tiers = cls._router_price_tiers(raw)
        if tiers:
            input_price = tiers[0]["input_price"]
            output_price = tiers[0]["output_price"]
        return input_price, output_price

    def _build_router_models(self, rows: list[Any]) -> list[ModelInfo]:
        catalog_by_lower = {model.id.lower(): model for model in get_provider_model_definitions(self.CATALOG_ID)}
        configured_by_lower = {model.id.lower(): model for model in getattr(self, "_config_models", [])}
        result: list[ModelInfo] = []
        seen: set[str] = set()

        for raw in rows:
            if not isinstance(raw, dict):
                continue
            router_name = raw.get("model_name", raw.get("modelName"))
            if not isinstance(router_name, str) or not router_name.strip():
                continue
            router_name = router_name.strip()
            normalized = router_name.lower()
            if normalized in seen:
                continue
            seen.add(normalized)

            catalog = catalog_by_lower.get(normalized)
            configured = configured_by_lower.get(normalized)
            model_id = catalog.id if catalog else (configured.id if configured else router_name)

            if catalog:
                capabilities = ModelCapabilities(
                    supports_streaming=catalog.capabilities.supports_streaming,
                    supports_tools=catalog.capabilities.supports_tools,
                    supports_vision=catalog.capabilities.supports_vision,
                    supports_reasoning=catalog.capabilities.supports_reasoning,
                    interleaved=catalog.capabilities.interleaved,
                    thinking_level_map=catalog.capabilities.thinking_level_map,
                    max_tokens=catalog.limits.max_output_tokens,
                    context_window=catalog.limits.context_window,
                )
                display_name = router_name
            else:
                capabilities = ModelCapabilities()
                display_name = router_name

            explicit_keys = set(getattr(configured, "_explicit_keys", set()) if configured else set())
            if configured:
                configured_capabilities = configured.capabilities
                capability_fields = {
                    "supports_streaming": "supports_streaming",
                    "supports_tools": "supports_tools",
                    "supports_vision": "supports_vision",
                    "supports_reasoning": "supports_reasoning",
                    "interleaved": "interleaved",
                    "thinking_level_map": "thinking_level_map",
                    "max_output_tokens": "max_tokens",
                    "max_tokens": "max_tokens",
                    "context_window": "context_window",
                }
                for config_key, attribute in capability_fields.items():
                    if config_key in explicit_keys:
                        setattr(
                            capabilities,
                            attribute,
                            getattr(configured_capabilities, attribute),
                        )
                # Router owns model identity and display casing. Persisted
                # names from older flocks.json snapshots must not overwrite
                # the current Console model name.

            input_price, output_price = self._router_default_prices(raw)
            price_tiers = self._router_price_tiers(raw)
            raw_price_version = raw.get(
                "price_version",
                raw.get("priceVersion"),
            )
            price_version = str(raw_price_version) if raw_price_version is not None else None
            pricing = None
            if input_price is not None and output_price is not None:
                pricing = {
                    "input": input_price,
                    "output": output_price,
                    "currency": "CNY",
                }
                if price_tiers:
                    pricing["price_tiers"] = price_tiers
                if price_version:
                    pricing["price_version"] = price_version
            elif catalog and catalog.pricing:
                pricing = {
                    "input": catalog.pricing.input,
                    "output": catalog.pricing.output,
                    "currency": catalog.pricing.currency,
                }
                if catalog.pricing.price_tiers:
                    pricing["price_tiers"] = [tier.model_dump() for tier in catalog.pricing.price_tiers]
                if catalog.pricing.price_version:
                    pricing["price_version"] = catalog.pricing.price_version

            model = ModelInfo(
                id=model_id,
                name=display_name,
                provider_id=self.id,
                capabilities=capabilities,
                pricing=pricing,
                custom_settings=(dict(configured.custom_settings) if configured else {}),
            )
            model._explicit_keys = explicit_keys
            result.append(model)

        return result

    async def _fetch_catalog_rows(
        self,
        client: httpx.AsyncClient,
        models_url: str,
        headers: dict[str, str],
        cookies: Optional[dict[str, str]],
    ) -> list[Any]:
        """Fetch either Console's paginated shape or ``/v1/models``."""
        page = 1
        page_size = 100
        rows: list[Any] = []

        while True:
            params = {"page": page, "pageSize": page_size} if "/api/console/" in models_url else None
            response = await client.get(
                models_url,
                headers=headers,
                cookies=cookies,
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Router model response is not an object")
            if "code" in payload and payload.get("code") != 0:
                raise ValueError(f"Router model response failed with code {payload.get('code')}")

            data = payload.get("data")
            if isinstance(data, list):
                return data
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise ValueError("Router model response has no model list")

            rows.extend(data["list"])
            total = data.get("total", len(rows))
            try:
                total = max(0, int(total))
            except (TypeError, ValueError):
                total = len(rows)
            if len(rows) >= total or not data["list"]:
                return rows
            page += 1

    async def refresh_models(self, force: bool = False) -> bool:
        """Refresh active models and complete pricing from Flocks Router.

        The refresh is rate-limited and failure-safe. A failed request never
        clears the last successful Router snapshot or the bundled fallback.
        """
        models_urls = self._model_catalog_urls()
        attempt_key = "|".join(models_urls)
        now = time.monotonic()
        ttl = self._positive_float_env(
            "THREATBOOK_CN_LLM_MODEL_CACHE_TTL_SECONDS",
            self.MODEL_CATALOG_CACHE_TTL_SECONDS,
            allow_zero=True,
        )
        if (
            not force
            and attempt_key == self._router_models_last_attempt_url
            and now - self._router_models_last_attempt < ttl
        ):
            return self._router_models is not None

        async with self._router_models_lock:
            now = time.monotonic()
            if (
                not force
                and attempt_key == self._router_models_last_attempt_url
                and now - self._router_models_last_attempt < ttl
            ):
                return self._router_models is not None

            if self._router_models_url and self._router_models_url not in models_urls:
                # Never carry an authoritative snapshot across environments.
                # If the new Router is unavailable, fall back to the bundled
                # catalog/config rather than showing models from the old URL.
                self._router_models = None
                self._router_models_url = None

            self._router_models_last_attempt = now
            self._router_models_last_attempt_url = attempt_key
            custom_settings = getattr(self._config, "custom_settings", None) or {}
            verify_ssl = resolve_verify_ssl(custom_settings, default=True)
            trust_env = _coerce_bool(os.getenv("FLOCKS_HTTP_TRUST_ENV"), True)
            if isinstance(custom_settings, dict) and "trust_env" in custom_settings:
                trust_env = _coerce_bool(custom_settings.get("trust_env"), trust_env)
            timeout_seconds = self._positive_float_env(
                "THREATBOOK_CN_LLM_MODEL_TIMEOUT_SECONDS",
                self.MODEL_CATALOG_TIMEOUT_SECONDS,
            )
            headers: dict[str, str] = {"Accept": "application/json"}
            api_key = self._config.api_key if self._config else self._api_key
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            session_token = os.getenv("THREATBOOK_CN_LLM_CONSOLE_SESSION_TOKEN")
            cookies = {"session_token": session_token} if session_token else None

            models = None
            successful_url = None
            errors: list[tuple[str, Exception]] = []
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                verify=verify_ssl,
                trust_env=trust_env,
            ) as client:
                for models_url in models_urls:
                    try:
                        rows = await self._fetch_catalog_rows(
                            client,
                            models_url,
                            headers,
                            cookies,
                        )
                        candidate_models = self._build_router_models(rows)
                        if rows and not candidate_models:
                            raise ValueError("Router model response has no valid model entries")
                    except Exception as exc:
                        errors.append((models_url, exc))
                        continue
                    models = candidate_models
                    successful_url = models_url
                    break

            if models is None or successful_url is None:
                failed_url, exc = errors[-1]
                self.log.warning(
                    "router.models.refresh_failed",
                    {
                        "url": failed_url,
                        "attempted_urls": [url for url, _ in errors],
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "using_fallback": self._router_models is None,
                    },
                )
                return False

            self._router_models = models
            self._router_models_url = successful_url
            self.log.info(
                "router.models.refreshed",
                {
                    "url": successful_url,
                    "count": len(models),
                },
            )
            return True


class ThreatBookIoLLMProvider(OpenAIBaseProvider):
    """ThreatBook international LLM provider (OpenAI-compatible)."""

    DEFAULT_BASE_URL = "https://llm.threatbook.io/v1"
    ENV_API_KEY = ["THREATBOOK_IO_LLM_API_KEY"]
    ENV_BASE_URL = "THREATBOOK_IO_LLM_BASE_URL"
    CATALOG_ID = "threatbook-io-llm"

    def __init__(self):
        super().__init__(provider_id="threatbook-io-llm", name="ThreatBook-io-llm")
