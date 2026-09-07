"""Validate Router model catalogs and select credential-scoped snapshots.

This module performs no network or credential-store access. Only explicitly
supported public metadata crosses the catalog boundary.
"""

import hashlib
import json
import math
import unicodedata
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from flocks.provider.types import PriceConfig

MAX_CATALOG_MODELS = 1000


class RouterCatalogError(ValueError):
    """A stable error that never includes an upstream response or credential."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _invalid_catalog() -> RouterCatalogError:
    return RouterCatalogError("invalid_catalog", "Router model catalog is invalid.")


def _invalid_pricing() -> RouterCatalogError:
    return RouterCatalogError("invalid_pricing", "Router model pricing is invalid.")


def _public_text(value: Any, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_length
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _price(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid_pricing()
    try:
        parsed = float(value)
    except (OverflowError, ValueError):
        raise _invalid_pricing() from None
    if not math.isfinite(parsed) or parsed < 0:
        raise _invalid_pricing()
    return parsed


def _parse_pricing(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _invalid_pricing()
    input_price = _price(raw.get("input"))
    output_price = _price(raw.get("output"))
    unit = raw.get("unit")
    if (
        isinstance(unit, bool)
        or not isinstance(unit, (int, float))
        or unit != 1_000_000
        or raw.get("currency") != "CNY"
    ):
        raise _invalid_pricing()
    version = raw.get("price_version")
    if not _public_text(version, max_length=128) or version != version.strip():
        raise _invalid_pricing()

    tiers = None
    tiers_known = raw.get("price_tiers_known", True)
    if not isinstance(tiers_known, bool) or (not tiers_known and raw.get("price_tiers") is not None):
        raise _invalid_pricing()
    if raw.get("price_tiers") is not None:
        raw_tiers = raw["price_tiers"]
        if not isinstance(raw_tiers, list) or not 1 <= len(raw_tiers) <= 100:
            raise _invalid_pricing()
        tiers = []
        previous_bound = -1
        for index, raw_tier in enumerate(raw_tiers):
            if not isinstance(raw_tier, dict) or "max_input_tokens" not in raw_tier:
                raise _invalid_pricing()
            bound = raw_tier["max_input_tokens"]
            if index == len(raw_tiers) - 1:
                if bound is not None:
                    raise _invalid_pricing()
            elif (
                isinstance(bound, bool)
                or not isinstance(bound, int)
                or bound <= previous_bound
            ):
                raise _invalid_pricing()
            else:
                previous_bound = bound
            tiers.append({
                "max_input_tokens": bound,
                "input": _price(raw_tier.get("input")),
                "output": _price(raw_tier.get("output")),
            })
        if tiers[0]["input"] != input_price or tiers[0]["output"] != output_price:
            raise _invalid_pricing()

    return PriceConfig(
        input=input_price,
        output=output_price,
        unit=1_000_000,
        currency="CNY",
        price_version=version,
        price_tiers=tiers,
        price_tiers_known=tiers_known,
        cache_read_uses_input=True,
        reasoning_uses_output=True,
        cost_rounding_places=6,
    ).model_dump(mode="json")


def parse_router_catalog(payload: Any) -> dict[str, dict[str, Any]]:
    """Validate normalized public model metadata (also used for saved snapshots)."""
    if not isinstance(payload, dict) or payload.get("object") != "list":
        raise _invalid_catalog()
    models = payload.get("data")
    if not isinstance(models, list) or not 1 <= len(models) <= MAX_CATALOG_MODELS:
        raise _invalid_catalog()

    result: dict[str, dict[str, Any]] = {}
    for model in models:
        if not isinstance(model, dict) or model.get("object") != "model":
            raise _invalid_catalog()
        model_id = model.get("id")
        if (
            not _public_text(model_id, max_length=256)
            or model_id != model_id.strip()
            or model_id in result
        ):
            raise _invalid_catalog()
        name = model.get("name", model_id)
        if not _public_text(name, max_length=256):
            raise _invalid_catalog()
        result[model_id] = {
            "name": name.strip(),
            "pricing": _parse_pricing(model.get("pricing")),
        }
    return result


def parse_portal_catalog_page(
    payload: Any, *, page: int, page_size: int,
) -> tuple[int, dict[str, dict[str, Any]]]:
    """Map the existing Portal console response, not an invented /v1 schema.

    The console's modelName is the public chat model ID; its numeric id is a
    database row ID. Missing priceTiers means unknown, not an empty tier list.
    """
    if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
        raise _invalid_catalog()
    data = payload.get("data")
    if not isinstance(data, dict):
        raise _invalid_catalog()
    total = data.get("total")
    if (
        type(total) is not int or not 1 <= total <= MAX_CATALOG_MODELS
        or type(data.get("page")) is not int or data["page"] != page
        or type(data.get("pageSize")) is not int or data["pageSize"] != page_size
    ):
        raise _invalid_catalog()
    rows = data.get("list")
    expected = min(page_size, total - (page - 1) * page_size)
    if not isinstance(rows, list) or expected <= 0 or len(rows) != expected:
        raise _invalid_catalog()
    models = []
    for row in rows:
        if not isinstance(row, dict):
            raise _invalid_catalog()
        tiers = row.get("priceTiers")
        if tiers is not None:
            if not isinstance(tiers, list) or any(
                not isinstance(tier, dict) or "maxInputTokens" not in tier for tier in tiers
            ):
                raise _invalid_pricing()
            tiers = [{
                "max_input_tokens": tier.get("maxInputTokens"),
                "input": tier.get("inputPrice"),
                "output": tier.get("outputPrice"),
            } for tier in tiers] or None
        models.append({
            "id": row.get("modelName"),
            "object": "model",
            "pricing": {
                "input": row.get("inputPrice"), "output": row.get("outputPrice"),
                "currency": "CNY", "unit": 1_000_000,
                "price_version": row.get("priceVersion"),
                "price_tiers": tiers, "price_tiers_known": "priceTiers" in row,
            },
        })
    return total, parse_router_catalog({"object": "list", "data": models})


def normalize_base_url(base_url: str) -> str:
    """Canonicalize an HTTP(S) API base without credential or query material."""
    error = RouterCatalogError("invalid_base_url", "Router API base URL is invalid.")
    if (
        not isinstance(base_url, str)
        or not base_url
        or base_url != base_url.strip()
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in base_url)
        or "\\" in base_url
    ):
        raise error
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise error from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in base_url
        or "#" in base_url
    ):
        raise error
    scheme = parsed.scheme.lower()
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, parsed.path.rstrip("/"), "", ""))


def credential_fingerprint(api_key: str, base_url: str) -> str:
    """Bind a snapshot to one exact credential and canonical API base."""
    if not isinstance(api_key, str) or not api_key:
        raise RouterCatalogError("invalid_credential", "Router credential is invalid.")
    material = json.dumps(
        [api_key, normalize_base_url(base_url)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@lru_cache(maxsize=32)
def _validated_snapshot(serialized: str) -> dict[str, Any]:
    snapshot = json.loads(serialized)
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("schema_version")) is not int
        or snapshot["schema_version"] != 1
        or not isinstance(snapshot.get("credential_fingerprint"), str)
        or not isinstance(snapshot.get("models"), dict)
    ):
        raise _invalid_catalog()
    raw_models = snapshot["models"]
    if not 1 <= len(raw_models) <= MAX_CATALOG_MODELS:
        raise _invalid_catalog()
    models = []
    for model_id, model in raw_models.items():
        if not isinstance(model, dict):
            raise _invalid_catalog()
        models.append({
            "id": model_id,
            "object": "model",
            "name": model.get("name", model_id),
            "pricing": model.get("pricing"),
        })
    result = {
        "schema_version": 1,
        "credential_fingerprint": snapshot["credential_fingerprint"],
        "models": parse_router_catalog({"object": "list", "data": models}),
    }
    synced_at = snapshot.get("synced_at")
    if synced_at is not None:
        if not _public_text(synced_at, max_length=64):
            raise _invalid_catalog()
        result["synced_at"] = synced_at
    return result


def active_router_catalog(provider: Any) -> dict[str, Any] | None:
    """Return a validated snapshot only for the provider's current Router key."""
    config = getattr(provider, "_config", None)
    settings = getattr(config, "custom_settings", None)
    if not isinstance(settings, dict):
        return None
    snapshot = settings.get("router_catalog")
    if not isinstance(snapshot, dict):
        return None
    effective_key = getattr(provider, "_effective_api_key", None)
    api_key = (
        effective_key()
        if callable(effective_key)
        else getattr(config, "api_key", None) or getattr(provider, "_api_key", None)
    )
    if not isinstance(api_key, str) or not api_key.startswith("fr_"):
        return None
    base_url = (
        getattr(config, "base_url", None)
        or getattr(provider, "_base_url", None)
        or getattr(provider, "DEFAULT_BASE_URL", None)
    )
    try:
        fingerprint = credential_fingerprint(api_key, base_url)
        if snapshot.get("credential_fingerprint") != fingerprint:
            return None
        serialized = json.dumps(
            snapshot, ensure_ascii=True, sort_keys=True, allow_nan=False,
        )
        return _validated_snapshot(serialized)
    except (RouterCatalogError, TypeError, ValueError, OverflowError):
        return None
