from __future__ import annotations

from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("mcp_status", ["disconnected", "timeout", "mystery-state"])
async def test_status_degrades_for_non_operational_mcp_states(
    monkeypatch: pytest.MonkeyPatch,
    mcp_status: str,
):
    from flocks.server.routes import monitoring as monitoring_routes

    monkeypatch.setattr(
        monitoring_routes.MCP,
        "status_snapshot",
        lambda: ({"demo-mcp": SimpleNamespace(status=mcp_status)}, True),
    )

    response = await monitoring_routes.get_status()

    assert response.status == "degraded"
    assert response.mcpServers == {"demo-mcp": mcp_status}


async def test_status_degrades_when_mcp_status_check_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import monitoring as monitoring_routes

    def _status_snapshot():
        raise RuntimeError("MCP status failed")

    monkeypatch.setattr(monitoring_routes.MCP, "status_snapshot", _status_snapshot)

    response = await monitoring_routes.get_status()

    assert response.status == "degraded"
    assert response.mcpServers == {}


async def test_status_snapshot_does_not_trigger_mcp_initialization(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import monitoring as monitoring_routes

    async def _unexpected_status():
        raise AssertionError("monitoring must not initialize MCP while reading status")

    monkeypatch.setattr(monitoring_routes.MCP, "status", _unexpected_status)
    monkeypatch.setattr(
        monitoring_routes.MCP,
        "status_snapshot",
        lambda: ({}, False),
    )

    response = await monitoring_routes.get_status()

    assert response.status == "degraded"
    assert response.mcpServers == {}


@pytest.mark.parametrize("mcp_status", ["connected", "disabled"])
async def test_status_accepts_operational_or_intentionally_disabled_mcp_states(
    monkeypatch: pytest.MonkeyPatch,
    mcp_status: str,
):
    from flocks.server.routes import monitoring as monitoring_routes

    monkeypatch.setattr(
        monitoring_routes.MCP,
        "status_snapshot",
        lambda: ({"demo-mcp": SimpleNamespace(status=mcp_status)}, True),
    )

    response = await monitoring_routes.get_status()

    assert response.status == "healthy"


async def test_metrics_do_not_label_tool_parse_failures_as_system_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import monitoring as monitoring_routes

    monitor = SimpleNamespace(
        get_metrics=lambda: {
            "global": {"total_calls": 12, "failed_parses": 3},
        }
    )
    monkeypatch.setattr(monitoring_routes, "get_monitor", lambda: monitor)
    monkeypatch.setattr(
        monitoring_routes,
        "_sample_tool_rates",
        lambda total, failed: (12.0, 3.0),
    )

    response = await monitoring_routes.get_metrics()
    payload = response.model_dump()

    assert response.toolCallRate == 12.0
    assert response.toolParseFailureRate == 3.0
    assert response.errorRate is None
    assert payload["errorRate"] is None
    assert payload["toolParseFailureRate"] == 3.0
