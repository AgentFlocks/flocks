"""The plaza and scene manager share suite completeness and safe repair behavior."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.hub import catalog, installer, local
from flocks.server.auth import require_admin, require_user
from flocks.server.routes.hub import router


@pytest.fixture
async def suite_client(isolated_hub_env, monkeypatch):
    async def refresh(*args):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", refresh)
    monkeypatch.setattr(installer, "_build_webui_pages", lambda *args: None)
    catalog.clear_catalog_caches()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[require_user] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    catalog.clear_catalog_caches()


async def suite_states(client):
    plaza = await client.get("/hub/catalog", params={"type": "component"})
    scenes = await client.get("/hub/scene-suites")
    assert plaza.status_code == scenes.status_code == 200
    return [
        next(item["state"] for item in response.json() if item["id"] == "soc-workspace")
        for response in (plaza, scenes)
    ]


@pytest.mark.parametrize(("kind", "identifier", "optional"), [
    ("workflow", "stream_alert_triage", False),
    ("tool", "soc_workspace_query", False),
    ("webui", "soc_ui", False),
    ("component", "soc-workspace", False),
    ("workflow", "stream_alert_triage", True),
])
async def test_missing_payload_has_same_state_in_both_entry_points(
    suite_client, isolated_hub_env, monkeypatch, kind, identifier, optional,
):
    await installer.install_plugin("component", "soc-workspace")
    assert await suite_states(suite_client) == ["installed", "installed"]
    path = Path(local.get_record(kind, identifier).installPath)
    path.rename(isolated_hub_env["home"] / f"{path.name}-removed")
    if optional:
        load_manifest = catalog.load_manifest

        def with_optional(plugin_type, plugin_id):
            manifest = load_manifest(plugin_type, plugin_id)
            if (plugin_type, plugin_id) == ("component", "soc-workspace"):
                return manifest.model_copy(update={"components": [
                    ref.model_copy(update={"optional": True}) if ref.id == identifier else ref
                    for ref in manifest.components
                ]})
            return manifest

        monkeypatch.setattr(catalog, "load_manifest", with_optional)
    # No explicit cache reset: deleting payloads must invalidate the snapshot.
    expected = "installed" if optional else "partial"
    assert await suite_states(suite_client) == [expected, expected]
    filtered = await suite_client.get("/hub/catalog", params={
        "type": "component", "state": expected, "limit": 25,
    })
    assert any(item["id"] == "soc-workspace" for item in filtered.json()["items"])
    assert filtered.json()["facets"]["state"][expected] >= 1


async def test_plaza_repair_confirms_backs_up_and_updates_outdated_children(suite_client):
    await installer.install_plugin("component", "soc-workspace")
    missing = Path(local.get_record("workflow", "stream_alert_triage").installPath)
    missing.rename(missing.parent / ".triage-removed")
    old_tool = local.get_record("tool", "soc_workspace_query")
    local.save_installed_record(old_tool.model_copy(update={"version": "0.0.1"}))
    custom = Path(old_tool.installPath) / "custom.txt"
    custom.write_text("user customization", encoding="utf-8")
    assert await suite_states(suite_client) == ["partial", "partial"]

    url = "/hub/plugins/component/soc-workspace"
    blocked = await suite_client.post(f"{url}/install")
    assert blocked.status_code == 409
    assert custom.read_text() == "user customization"
    preview = (await suite_client.post(f"{url}/update/preview")).json()
    assert preview["requiresConfirmation"]
    assert {item["id"] for item in preview["items"]} >= {
        "soc-workspace", "stream_alert_triage", "soc_workspace_query",
    }
    repaired = await suite_client.post(f"{url}/update", json={
        "confirmationToken": preview["token"], "confirmChanges": True,
    })
    assert repaired.status_code == 200, repaired.text
    assert await suite_states(suite_client) == ["installed", "installed"]
    assert local.has_install_payload("workflow", missing)
    assert local.get_record("tool", "soc_workspace_query").version == catalog.load_manifest("tool", "soc_workspace_query").version
    assert not custom.exists()
    backup = Path(repaired.json()["backupPath"])
    plan = json.loads((backup / "backup.json").read_text())["plan"]
    index = next(index for index, item in enumerate(plan["items"]) if item["id"] == "soc_workspace_query")
    assert (backup / str(index) / "package" / "custom.txt").read_text() == "user customization"


def test_partial_suites_are_prioritized_with_updates():
    from flocks.hub.models import HubCatalogEntry
    from flocks.server.routes.hub import _prioritize_installed_catalog_entries

    entries = [HubCatalogEntry(
        id=state, name=state, type="component", state=state, manifestPath="manifest.json",
    ) for state in ("available", "installed", "partial", "updateAvailable")]
    assert [entry.state for entry in _prioritize_installed_catalog_entries(entries)] == [
        "partial", "updateAvailable", "installed", "available",
    ]
