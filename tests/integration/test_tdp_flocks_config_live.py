"""
使用本机 ~/.flocks/config/flocks.json 中的 ``api_services.tdp_api`` 对 TDP 发起真实 HTTP 调用（不使用 mock）。

运行（需可访问配置的 ``base_url``，且凭证有效）::

    FLOCKS_LIVE_TDP_TEST=1 uv run pytest tests/integration/test_tdp_flocks_config_live.py -v

说明：
  - 默认不运行；仅当 ``FLOCKS_LIVE_TDP_TEST=1`` 时执行，避免 CI 或无凭证环境误连。
  - 凭证解析逻辑与运行时一致：``tdp.handler`` 通过 ``ConfigWriter.get_api_service_raw("tdp_api")``
    与 SecretManager 解析 Key/Secret（支持 flocks.json 内联或 ``{secret:...}`` 引用）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext
from flocks.tool.tool_loader import yaml_to_tool


_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_TDP_PLUGIN_DIR = _WORKSPACE_ROOT / ".flocks" / "plugins" / "tools" / "api" / "tdp_v3_3_10"


def _live_tdp_enabled() -> bool:
    return os.environ.get("FLOCKS_LIVE_TDP_TEST") == "1"


def _tdp_service_config() -> dict | None:
    return ConfigWriter.get_api_service_raw("tdp_api")


def _skip_reason_if_not_ready() -> str | None:
    if not _live_tdp_enabled():
        return "Set FLOCKS_LIVE_TDP_TEST=1 to run live TDP tests against ~/.flocks/config/flocks.json."
    raw = _tdp_service_config()
    if raw is None:
        return "api_services.tdp_api is missing in flocks.json."
    if raw.get("enabled") is False:
        return "api_services.tdp_api.enabled is false."
    if not (raw.get("base_url") or raw.get("baseUrl")):
        return "api_services.tdp_api has no base_url."
    return None


def _load_tdp_tool(yaml_name: str):
    path = _TDP_PLUGIN_DIR / yaml_name
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return yaml_to_tool(raw, path)


def _default_time_range_seconds(days: int = 7) -> tuple[int, int]:
    end_ts = int(time.time())
    start_ts = end_ts - days * 86400
    return start_ts, end_ts


@pytest.fixture
def skip_unless_tdp_live_configured():
    reason = _skip_reason_if_not_ready()
    if reason:
        pytest.skip(reason)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tdp_interface_risk_list_live_no_mock(skip_unless_tdp_live_configured):
    """与 Flocks 运行时一致：YAML → yaml_to_tool → handler → 真实 POST（签名校验）。"""
    tool = _load_tdp_tool("tdp_interface_risk_list.yaml")
    assert tool.info.name == "tdp_interface_risk_list"

    start_ts, end_ts = _default_time_range_seconds(days=7)
    ctx = ToolContext(session_id="live_tdp_test", message_id="live_tdp_test")

    result = await tool.handler(
        ctx,
        time_from=start_ts,
        time_to=end_ts,
        cur_page=1,
        page_size=5,
        sort_by="last_occ_time",
        sort_order="desc",
    )

    assert result.metadata.get("source") == "TDP"
    assert result.metadata.get("api") == "interface_risk_list"
    assert result.metadata.get("path") == "/api/v1/interface/risk/getApiList"

    if not result.success:
        err_payload = result.output if isinstance(result.output, dict) else {}
        code = err_payload.get("response_code", err_payload.get("status"))
        if code == 404:
            pytest.skip(
                "TDP 返回 404：该实例可能未开放 /api/v1/interface/risk/getApiList。"
                "凭证与签名已通过 test_tdp_dashboard_status_live_smoke 验证。"
            )
        pytest.fail(result.error or str(err_payload))



@pytest.mark.integration
@pytest.mark.asyncio
async def test_tdp_dashboard_status_live_smoke(skip_unless_tdp_live_configured):
    """最小连通性：看板 status，验证凭证与签名可用。"""
    tool = _load_tdp_tool("tdp_dashboard_status.yaml")
    ctx = ToolContext(session_id="live_tdp_test", message_id="live_tdp_test_smoke")

    result = await tool.handler(ctx, action="status")

    assert result.metadata.get("api") in ("dashboard_status",)
    assert result.metadata.get("path") == "/api/v1/dashboard/status"
    assert result.success is True, getattr(result, "error", None)
