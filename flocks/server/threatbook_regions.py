"""Canonical ThreatBook regional endpoints shared by guided setup flows."""

from __future__ import annotations

from typing import Any, Dict, Literal
from urllib.parse import quote


ThreatBookRegion = Literal["cn", "global"]

THREATBOOK_REGION_PRESETS: Dict[ThreatBookRegion, Dict[str, Any]] = {
    "cn": {
        "activation_url": "https://x.threatbook.com/flocks/activate",
        "threatbook_llm_provider_id": "threatbook-cn-llm",
        "threatbook_default_model_id": "deepseek-v4-flash-0731",
        "threatbook_api_service_id": "threatbook-cn",
        "threatbook_mcp_name": "threatbook_mcp",
        "threatbook_mcp_endpoint": "https://mcp.threatbook.cn/mcp",
        "threatbook_mcp_secret_id": "threatbook_mcp_key",
        "requires_mcp": True,
    },
    "global": {
        "activation_url": "https://i.threatbook.io/flocks/activate",
        "threatbook_llm_provider_id": "threatbook-io-llm",
        "threatbook_default_model_id": "deepseek-v4-flash-0731",
        "threatbook_api_service_id": "threatbook-io",
        "threatbook_mcp_name": "threatbook_mcp",
        "threatbook_mcp_endpoint": "https://mcp.threatbook.io/mcp",
        "threatbook_mcp_secret_id": "threatbook_mcp_key",
        "requires_mcp": True,
    },
}


def build_threatbook_mcp_url(
    region: ThreatBookRegion,
    api_key: str,
    *,
    encode_key: bool = True,
) -> str:
    """Build a regional MCP URL from a key or secret placeholder."""
    endpoint = THREATBOOK_REGION_PRESETS[region]["threatbook_mcp_endpoint"]
    value = quote(api_key, safe="") if encode_key else api_key
    return f"{endpoint}?apikey={value}"


def infer_threatbook_mcp_region(url: str | None) -> ThreatBookRegion | None:
    """Infer the configured region from a persisted MCP URL."""
    normalized_url = (url or "").strip().lower()
    if normalized_url.startswith(THREATBOOK_REGION_PRESETS["global"]["threatbook_mcp_endpoint"]):
        return "global"
    if normalized_url.startswith(THREATBOOK_REGION_PRESETS["cn"]["threatbook_mcp_endpoint"]):
        return "cn"
    return None
