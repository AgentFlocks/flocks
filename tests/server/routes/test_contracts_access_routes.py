from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.server.routes import contracts as contracts_routes
from flocks.contracts.access.runtime import OperationRuntime
from flocks.contracts.webui.store import WebUIPagesStore
from tests.contracts.access.test_runtime import (
    CONTRACT_ID,
    PAGE_ID,
    _alert_record,
    _plugin,
    _write_alert_assets,
)


def test_contract_route_runtime_reloads_when_plugin_signature_changes(monkeypatch: pytest.MonkeyPatch):
    created: list[object] = []
    signature = (("plugin.py", 1, 100),)

    class RuntimeStub:
        def __init__(self) -> None:
            created.append(self)

    monkeypatch.setattr(contracts_routes, "OperationRuntime", RuntimeStub)
    monkeypatch.setattr(contracts_routes, "_contract_plugin_signature", lambda: signature)
    contracts_routes.reset_route_dependencies()

    first = contracts_routes._get_runtime()
    second = contracts_routes._get_runtime()
    assert first is second
    assert len(created) == 1

    signature = (("plugin.py", 2, 100),)
    third = contracts_routes._get_runtime()
    assert third is not first
    assert len(created) == 2


@pytest.fixture
def contract_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "contracts-webui"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore(root=root)
    _write_alert_assets(store, [_alert_record(id="alert-route-1")])
    contracts_routes.reset_route_dependencies(runtime=OperationRuntime(plugins=(_plugin(store),)))
    return store


@pytest.mark.asyncio
async def test_contract_operation_route(client: AsyncClient, contract_pages: WebUIPagesStore):
    resp = await client.post(
        f"/api/contracts/webui/pages/{PAGE_ID}/access/{CONTRACT_ID}/operations/list",
        json={"params": {"limit": 10}},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["totalRaw"] == 1
    assert body["items"][0]["id"] == "alert-route-1"


@pytest.mark.asyncio
async def test_contract_operation_route_applies_default_member_policy(contract_pages: WebUIPagesStore):
    response = await contracts_routes.execute_webui_contract_operation(
        PAGE_ID,
        CONTRACT_ID,
        "list",
        {"params": {"limit": 10}},
        AuthUser(
            id="u1",
            username="analyst",
            role="member",
            tenant_ids=("tenant-a",),
            asset_groups=("core",),
        ),
    )

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["items"][0]["id"] == "alert-route-1"
    assert body["meta"]["filterStagesApplied"][0]["source"] == "policy.tenantIds"


@pytest.mark.asyncio
async def test_contract_operation_route_rejects_forbidden_fields(
    client: AsyncClient,
    contract_pages: WebUIPagesStore,
):
    resp = await client.post(
        f"/api/contracts/webui/pages/{PAGE_ID}/access/{CONTRACT_ID}/operations/list",
        json={"params": {"driver": "sqlite"}},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "forbidden_request_field"
