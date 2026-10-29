"""Scene workspaces are listed with the suite that ships them; leftovers no suite knows are not."""

import pytest

from flocks.contracts.webui.models import WebUIWorkspaceListItem
from flocks.server.routes import webui as webui_routes


def _workspace(workspace_id: str, *, native: bool = False, placement: str = "sceneWorkspace") -> WebUIWorkspaceListItem:
    return WebUIWorkspaceListItem(
        id=workspace_id,
        title=workspace_id,
        route=f"/contracts/webui/workspaces/{workspace_id}",
        icon="Layers",
        order=10,
        enabled=True,
        placement=placement,
        native=native,
    )


@pytest.fixture()
def suites(monkeypatch):
    monkeypatch.setattr(webui_routes, "scene_suite_workspace_owners", lambda: {"ops_ui": "ops-suite", "team_ui": "team-suite"})


def test_scene_workspaces_carry_their_suite(suites):
    result = {item.id: item for item in webui_routes._attach_scene_suites([
        _workspace("ops_ui"), _workspace("team_ui"), _workspace("leftover_ui"),
        _workspace("dev_ui", native=True), _workspace("bench_ui", placement="aiWorkbench"),
    ])}

    assert (result["ops_ui"].suiteId, result["team_ui"].suiteId) == ("ops-suite", "team-suite")
    # A user-root scene no suite knows about is a leftover: not listed.
    assert "leftover_ui" not in result
    # Development scenes and workbench pages are not scenes a suite ships.
    assert {"dev_ui", "bench_ui"} <= set(result)
    assert not any(item.locked for item in result.values())


@pytest.mark.asyncio
async def test_without_a_policy_no_workspace_is_locked(monkeypatch, suites):
    class _Store:
        def list_workspaces(self, enabled_only: bool = False):
            return [_workspace("ops_ui"), _workspace("team_ui")]

    monkeypatch.setattr(webui_routes, "_store", _Store())

    enabled = await webui_routes.list_webui_workspaces(enabled_only=True)
    assert [(item.id, item.locked, item.lockReason) for item in enabled] == [
        ("ops_ui", False, None), ("team_ui", False, None),
    ]
