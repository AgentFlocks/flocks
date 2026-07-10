"""
Helpers for onboarding pre-flight status detection.

This module centralizes the configuration checks that power the
"当前配置状态" section in the onboarding skill, so the logic can be
reused and tested independently of the markdown skill file.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter
from flocks.mcp import MCP
from flocks.mcp.types import McpStatus
from flocks.server.routes.mcp import get_mcp_credentials
from flocks.server.routes.provider import get_provider_credentials, get_service_credentials


THREATBOOK_PROVIDER_IDS = {"threatbook-cn-llm", "threatbook-io-llm"}
SECURITY_SERVICES = ("virustotal", "fofa", "urlscan", "shodan")
KNOWN_CHANNELS = ("feishu", "wecom", "dingtalk", "telegram", "teams")
PROVIDER_SECRET_CANDIDATES = {
    "openai": ("openai_llm_key", "openai_api_key"),
    "anthropic": ("anthropic_llm_key", "anthropic_api_key"),
    "threatbook-cn-llm": ("threatbook-cn-llm_llm_key", "threatbook-cn-llm_api_key"),
    "threatbook-io-llm": ("threatbook-io-llm_llm_key", "threatbook-io-llm_api_key"),
}
SERVICE_SECRET_CANDIDATES = {
    "threatbook-cn": ("threatbook_cn_api_key",),
    "threatbook-io": ("threatbook_io_api_key",),
    "threatbook_api": ("threatbook_api_key",),
    "virustotal": ("virustotal_api_key",),
    "fofa": ("fofa_key", "fofa_api_key"),
    "urlscan": ("urlscan_api_key",),
    "shodan": ("shodan_api_key",),
}


def _is_real_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not value.strip().startswith("<")


def _has_meaningful_service_credentials(response: Any) -> bool:
    candidate_values = []
    fields = getattr(response, "fields", None) or {}
    if isinstance(fields, dict):
        candidate_values.extend(
            value for key, value in fields.items() if key not in {"base_url"}
        )

    candidate_values.extend(
        [
            getattr(response, "api_key", None),
            getattr(response, "secret", None),
            getattr(response, "username", None),
        ]
    )
    return any(_is_real_value(value) for value in candidate_values)


def _load_secret_values() -> Dict[str, Any]:
    try:
        secret_file = Config.get_secret_file()
        if not secret_file.exists():
            return {}
        return json.loads(secret_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _provider_has_credentials(provider_id: str) -> bool:
    try:
        response = await get_provider_credentials(provider_id)
        if _is_real_value(getattr(response, "api_key", None)):
            return True
    except Exception:
        pass

    secrets = _load_secret_values()
    return any(
        _is_real_value(secrets.get(secret_id))
        for secret_id in PROVIDER_SECRET_CANDIDATES.get(
            provider_id,
            (f"{provider_id}_llm_key", f"{provider_id}_api_key"),
        )
    )


async def _service_has_credentials(service_id: str) -> bool:
    try:
        response = await get_service_credentials(service_id)
        if _has_meaningful_service_credentials(response):
            return True
    except Exception:
        pass

    secrets = _load_secret_values()
    return any(
        _is_real_value(secrets.get(secret_id))
        for secret_id in SERVICE_SECRET_CANDIDATES.get(
            service_id,
            (f"{service_id}_api_key",),
        )
    )


async def _detect_threatbook_llm_status() -> bool:
    try:
        default_llm = await Config.resolve_default_llm()
    except Exception:
        default_llm = None

    if default_llm and default_llm.get("provider_id") in THREATBOOK_PROVIDER_IDS:
        return True

    for provider_id in THREATBOOK_PROVIDER_IDS:
        if await _provider_has_credentials(provider_id):
            return True
    return False


async def _detect_threatbook_api_status() -> bool:
    for service_id in ("threatbook-cn", "threatbook-io", "threatbook_api"):
        if await _service_has_credentials(service_id):
            return True
    return False


async def _detect_mcp_status(name: str) -> Dict[str, Any]:
    raw_config = ConfigWriter.get_mcp_server(name)
    has_config = raw_config is not None
    enabled = bool(raw_config.get("enabled", True)) if isinstance(raw_config, dict) else True

    has_credential = False
    try:
        credential_info = await get_mcp_credentials(name)
        has_credential = bool(getattr(credential_info, "has_credential", False))
    except Exception:
        has_credential = False

    runtime_status = None
    try:
        runtime = await MCP.status()
        info = runtime.get(name)
        if info is not None:
            status_value = getattr(info, "status", None)
            runtime_status = status_value.value if isinstance(status_value, McpStatus) else str(status_value)
    except Exception:
        runtime_status = None

    if runtime_status == McpStatus.CONNECTED.value:
        status = "connected"
    elif runtime_status in {McpStatus.FAILED.value, McpStatus.NEEDS_AUTH.value}:
        status = "error"
    elif runtime_status == McpStatus.DISABLED.value or (has_config and not enabled):
        status = "disabled"
    elif has_config or has_credential or runtime_status in {
        McpStatus.DISCONNECTED.value,
        McpStatus.CONNECTING.value,
    }:
        status = "configured"
    else:
        status = "not_configured"

    return {
        "status": status,
        "configured": status != "not_configured",
        "connected": status == "connected",
        "has_credential": has_credential,
    }


async def build_onboarding_preflight_status() -> Dict[str, Any]:
    llm_status = {
        "openai": await _provider_has_credentials("openai"),
        "anthropic": await _provider_has_credentials("anthropic"),
        "threatbook": await _detect_threatbook_llm_status(),
    }

    security_tool_status = {
        service_id: await _service_has_credentials(service_id)
        for service_id in SECURITY_SERVICES
    }

    tb_mcp = await _detect_mcp_status("threatbook_mcp")

    channels_raw: Dict[str, Any] = {}
    try:
        raw = ConfigWriter._read_raw()
        if isinstance(raw, dict):
            channels_raw = raw.get("channels", {}) or {}
    except Exception:
        channels_raw = {}

    channel_status = {
        channel_id: bool(channels_raw.get(channel_id, {}).get("enabled"))
        for channel_id in KNOWN_CHANNELS
    }

    return {
        "llm_status": llm_status,
        "tb_api_configured": await _detect_threatbook_api_status(),
        "tb_mcp_configured": tb_mcp["configured"],
        "tb_mcp_connected": tb_mcp["connected"],
        "tb_mcp_status": tb_mcp["status"],
        "security_tool_status": security_tool_status,
        "channel_status": channel_status,
    }


def print_onboarding_preflight_status() -> None:
    print(json.dumps(asyncio.run(build_onboarding_preflight_status()), indent=2, ensure_ascii=False))
