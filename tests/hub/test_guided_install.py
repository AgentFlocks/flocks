"""Hub entries installed through a Rex conversation (the REA plugin)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.hub.catalog import list_catalog, load_manifest
from flocks.rea import runtime
from flocks.server.auth import require_admin
from flocks.server.routes.hub import router


def _rea_entry():
    return next(entry for entry in list_catalog(plugin_type="skill") if entry.id == "rea")


def test_rea_is_listed_as_a_guided_install(isolated_hub_env):
    manifest = load_manifest("skill", "rea")
    assert manifest.guidedInstall is not None and manifest.guidedInstall.kind == "rea"
    assert manifest.nameCn == "REA 逆向分析"
    assert manifest.descriptionCn == "基于 Reverse Engineer Anything 的逆向分析工具，帮助还原关键逻辑、算法或接口流程。"
    entry = _rea_entry()
    assert entry.guidedInstall.kind == "rea"
    assert entry.state == "available"
    assert entry.installedVersion is None


def test_rea_state_follows_live_readiness_not_the_cached_snapshot(isolated_hub_env, monkeypatch):
    assert _rea_entry().state == "available"  # builds and caches the snapshot
    monkeypatch.setattr(runtime, "is_ready", lambda: True)
    monkeypatch.setattr(runtime, "installed_version", lambda: runtime.REA_VERSION)
    entry = _rea_entry()
    assert (entry.state, entry.installedVersion) == ("installed", runtime.REA_VERSION)
    assert [item.id for item in list_catalog(plugin_type="skill", state=["installed"])] == ["rea"]
    # A failed re-verification turns it back into an install offer.
    monkeypatch.setattr(runtime, "is_ready", lambda: False)
    assert _rea_entry().state == "available"


def test_a_broken_readiness_check_reads_as_available(isolated_hub_env, monkeypatch):
    def broken():
        raise OSError("config unreadable")

    monkeypatch.setattr(runtime, "is_ready", broken)
    assert _rea_entry().state == "available"


@pytest.fixture
async def client(isolated_hub_env):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/hub/plugins/skill/rea/install"),
        ("post", "/hub/plugins/skill/rea/install/stream"),
        ("post", "/hub/plugins/skill/rea/update/preview"),
        ("post", "/hub/plugins/skill/rea/update"),
        ("delete", "/hub/plugins/skill/rea"),
    ],
)
async def test_file_copy_operations_are_refused_for_guided_entries(client, isolated_hub_env, method, path):
    kwargs = {"json": {"scope": "global"}} if method == "post" else {}
    response = await getattr(client, method)(path, **kwargs)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "hub_guided_install"
    assert not (isolated_hub_env["home"] / ".flocks" / "plugins" / "skills" / "rea").exists()


@pytest.mark.asyncio
async def test_catalog_endpoint_carries_the_guided_install(client):
    response = await client.get("/hub/catalog", params={"type": "skill", "q": "REA"})
    assert response.status_code == 200
    entry = next(item for item in response.json() if item["id"] == "rea")
    assert entry["guidedInstall"] == {"kind": "rea", "title": "REA 逆向分析"}
    assert entry["state"] == "available"
