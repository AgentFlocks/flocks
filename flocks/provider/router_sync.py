"""Fetch a Router catalog and probe its models before publishing a snapshot."""

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from flocks.provider.router_catalog import (
    RouterCatalogError,
    credential_fingerprint,
    normalize_base_url,
    parse_portal_catalog_page,
)
from flocks.provider.sdk.openai_base import response_size_limit_hook

PORTAL_CATALOG_URL = "https://portal.agentflocks.com/api/console/common/models"
PORTAL_PAGE_SIZE = 100


def get_portal_session_token() -> str:
    """Read optional deployment authorization, never a user-submitted cookie.

    Flocks cannot derive a Portal session from an fr_ key. Without a separately
    authorized server-side session, callers must keep prices and test normally.
    """
    value = os.getenv("FLOCKS_PORTAL_SESSION_TOKEN")
    if not isinstance(value, str) or not value:
        raise RouterCatalogError("CATALOG_NOT_AUTHORIZED", "价格数据暂不可用，继续使用现有价格。")
    if (
        len(value) > 4096 or value.startswith("session_token=")
        or any(ord(char) < 33 or ord(char) > 126 or char in '\";,\\' for char in value)
    ):
        raise RouterCatalogError("CATALOG_NOT_AUTHORIZED", "价格数据暂不可用，继续使用现有价格。")
    return value


async def _fetch_portal_catalog(session_token: str) -> dict[str, dict[str, Any]]:
    # This client never receives the model API key or a configurable URL.
    # Its cookie jar (including any refreshed session) dies with this request.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=False,
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        cookies=httpx.Cookies({"session_token": session_token}),
        event_hooks={"response": [response_size_limit_hook(2 * 1024 * 1024)]},
    ) as portal:
        models: dict[str, dict[str, Any]] = {}
        page = 1
        total = None
        while total is None or len(models) < total:
            response = await portal.get(PORTAL_CATALOG_URL, params={"page": page, "pageSize": PORTAL_PAGE_SIZE})
            if response.status_code in (401, 403):
                raise RouterCatalogError("CATALOG_NOT_AUTHORIZED", "价格数据暂不可用，继续使用现有价格。")
            if response.status_code != 200:
                raise RouterCatalogError("CATALOG_UNAVAILABLE", "Portal 模型目录暂时不可用，已有配置已保留。")
            try:
                payload = response.json()
            except ValueError:
                raise RouterCatalogError("CATALOG_INVALID", "Portal 模型目录响应无效，已有配置已保留。") from None
            if isinstance(payload, dict) and payload.get("code") in (401, 403):
                raise RouterCatalogError("CATALOG_NOT_AUTHORIZED", "价格数据暂不可用，继续使用现有价格。")
            page_total, page_models = parse_portal_catalog_page(payload, page=page, page_size=PORTAL_PAGE_SIZE)
            if (total is not None and total != page_total) or models.keys() & page_models.keys():
                raise RouterCatalogError("CATALOG_CHANGED", "分页期间模型目录发生变化，请重新同步；已有配置已保留。")
            total = page_total
            models.update(page_models)
            page += 1
        return models


async def fetch_and_test_router_catalog(
    api_key: str,
    base_url: str,
    disabled_model_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read Portal with server-side authorization, then probe with the API key.

    The two credentials use separate clients and fixed destinations. Only
    public catalog metadata is included in the persisted result.
    """
    base_url = normalize_base_url(base_url)
    session_token = get_portal_session_token()
    async with asyncio.timeout(180):
        models = await _fetch_portal_catalog(session_token)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            event_hooks={"response": [response_size_limit_hook(2 * 1024 * 1024)]},
        ) as client:
            semaphore = asyncio.Semaphore(3)

            async def probe(model_id: str) -> dict[str, Any]:
                async with semaphore:
                    start = time.monotonic()
                    result: dict[str, Any] = {"model_id": model_id, "success": False}
                    try:
                        reply = await client.post(
                            f"{base_url}/chat/completions",
                            json={
                                "model": model_id,
                                "messages": [{
                                    "role": "user",
                                    "content": "What is the capital of France? Answer in one word only.",
                                }],
                                "max_tokens": 20,
                                "stream": False,
                            },
                        )
                        if reply.status_code == 200:
                            data = reply.json()
                            choices = data.get("choices") if isinstance(data, dict) else None
                            result["success"] = bool(
                                isinstance(choices, list) and choices
                                and isinstance(choices[0], dict)
                                and isinstance(choices[0].get("message"), dict)
                                and not data.get("error")
                            )
                        if not result["success"]:
                            # Never propagate response bodies, vendor URLs or credentials.
                            result["error"] = "模型测试未通过"
                    except (httpx.HTTPError, ValueError):
                        result["error"] = "模型测试请求失败或超时"
                    result["latency_ms"] = int((time.monotonic() - start) * 1000)
                    return result

            results = await asyncio.gather(*(
                probe(model_id) for model_id in models
                if model_id not in disabled_model_ids
            ))

    # Catalog authentication and validation establish price authority. A
    # transient model failure does not remove that model or invalidate its price.
    return {
        "schema_version": 1,
        "credential_fingerprint": credential_fingerprint(api_key, base_url),
        "synced_at": datetime.now(UTC).isoformat(),
        "models": models,
    }, results
