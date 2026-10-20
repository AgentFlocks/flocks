"""Installed Pro scene workspaces are locked while no Pro license is active."""

import pytest

from flocks.contracts.webui.models import WebUIWorkspaceListItem
from flocks.server.routes import webui as webui_routes


def _workspace(workspace_id: str, *, native: bool = False) -> WebUIWorkspaceListItem:
    return WebUIWorkspaceListItem(
        id=workspace_id,
        title=workspace_id,
        route=f"/contracts/webui/workspaces/{workspace_id}",
        icon="Layers",
        order=10,
        enabled=True,
        placement="sceneWorkspace",
        native=native,
    )


@pytest.fixture()
def suites(monkeypatch):
    monkeypatch.setattr(
        webui_routes,
        "scene_suite_workspace_owners",
        lambda: {"free_ui": "free-suite", "pro_ui": "pro-suite"},
    )
    monkeypatch.setattr(
        webui_routes,
        "_suite_edition",
        lambda suite_id: "pro" if suite_id == "pro-suite" else "oss",
    )


def test_pro_workspace_is_locked_without_license(suites):
    result = {item.id: item for item in webui_routes._attach_scene_suites(
        [_workspace("free_ui"), _workspace("pro_ui")], edition_active=False,
    )}

    assert result["free_ui"].locked is False
    assert result["pro_ui"].locked is True
    assert result["pro_ui"].suiteId == "pro-suite"
    # Locking never rewrites the stored on/off switch.
    assert result["pro_ui"].enabled is True


def test_pro_workspace_opens_with_license(suites):
    result = {item.id: item for item in webui_routes._attach_scene_suites(
        [_workspace("pro_ui")], edition_active=True,
    )}

    assert result["pro_ui"].locked is False


@pytest.mark.asyncio
async def test_enabled_only_listing_leaves_out_locked_workspaces(monkeypatch, suites):
    class _Store:
        def list_workspaces(self, enabled_only: bool = False):
            return [_workspace("free_ui"), _workspace("pro_ui")]

    async def oss_status():
        return {"activated": True, "active": True, "status": "oss"}

    monkeypatch.setattr(webui_routes, "_store", _Store())
    monkeypatch.setattr(webui_routes, "license_status", oss_status)

    enabled = await webui_routes.list_webui_workspaces(enabled_only=True)
    everything = await webui_routes.list_webui_workspaces(enabled_only=False)

    assert [item.id for item in enabled] == ["free_ui"]
    assert {item.id: item.locked for item in everything} == {"free_ui": False, "pro_ui": True}
