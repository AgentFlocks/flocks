"""An ordinary extension uses the same licensing extension points (G01).

Nothing here is a commercial build: an extension called "acme" licenses an
edition it names "team", lists a scene suite of that edition from its own hub
root and registers a licence checker through ``flocks.extensions``.  The open
product treats it the way it treats any licensed edition -- the edition's
name means nothing to it, and nothing about it is built in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from flocks.contracts.webui.models import WebUIWorkspaceListItem
from flocks.extensions import register_hub_root, register_license_checker, unregister_hub_root
from flocks.hub import suite_license
from flocks.hub.catalog import scene_suite_workspace_owners
from flocks.license import get_checker
from flocks.server.routes import webui as webui_routes

SUITE = "acme-suite"
WORKSPACE = "acme_ui"


def _manifest(plugin_type: str, plugin_id: str, **extra) -> dict:
    return {
        "schemaVersion": "hub.plugin.v1", "id": plugin_id, "type": plugin_type, "name": plugin_id,
        "description": "an ordinary extension's suite", "version": "1.0.0", "category": "workflow-automation",
        "trust": "community", "edition": "team",
        "source": {"kind": "bundled", "path": f"plugins/{plugin_type}s/{plugin_id}"}, **extra,
    }


@pytest.fixture()
def acme_root(tmp_path: Path):
    root = tmp_path / "acme-hub"
    suite = _manifest("component", SUITE, components=[{"type": "webui", "id": WORKSPACE}], entrypoints=["component.json"])
    webui = _manifest("webui", WORKSPACE, entrypoints=["workspace.json"])
    for manifest, files in (
        (suite, {"component.json": {"id": SUITE, "components": suite["components"]}}),
        (webui, {"workspace.json": {"id": WORKSPACE, "placement": "sceneWorkspace"}}),
    ):
        folder = root / manifest["source"]["path"]
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for name, content in files.items():
            (folder / name).write_text(json.dumps(content), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({"schemaVersion": "hub.index.v1", "plugins": [
        {"id": m["id"], "type": m["type"], "name": m["name"], "version": "1.0.0", "edition": "team",
         "manifestPath": f"{m['source']['path']}/manifest.json"}
        for m in (suite, webui)
    ]}), encoding="utf-8")
    register_hub_root("acme", root)
    suite_license.clear_page_suite_cache()
    try:
        yield root
    finally:
        unregister_hub_root("acme")
        suite_license.clear_page_suite_cache()


class AcmeChecker:
    """The extension's own checker: status "team" while its licence is in force."""

    team_active = True
    suites: set[str] = set()

    @classmethod
    async def is_active(cls, feature=None):
        return (await cls.feature_status(feature))["active"]

    @classmethod
    async def assert_active(cls, feature=None):
        if not await cls.is_active(feature):
            raise PermissionError(feature)

    @classmethod
    async def status(cls):
        return {"active": cls.team_active, "status": "team" if cls.team_active else "expired"}

    @classmethod
    async def feature_status(cls, feature=None):
        if feature and feature.startswith(suite_license.SUITE_FEATURE_PREFIX):
            active = cls.team_active and feature.removeprefix(suite_license.SUITE_FEATURE_PREFIX) in cls.suites
            return {"active": active, "status": "team" if active else "unlicensed", "details": {}}
        return {"active": cls.team_active, "status": "team", "details": {}}


@pytest.fixture()
def acme_checker():
    previous = get_checker()
    AcmeChecker.team_active, AcmeChecker.suites = True, set()
    register_license_checker(AcmeChecker)
    try:
        yield AcmeChecker
    finally:
        register_license_checker(previous)


async def _suites(client: AsyncClient) -> dict[str, dict]:
    resp = await client.get("/api/hub/scene-suites")
    assert resp.status_code == 200, resp.text
    return {item["id"]: item for item in resp.json()}


def _locked(edition_active: bool) -> bool:
    workspace = WebUIWorkspaceListItem(
        id=WORKSPACE, title=WORKSPACE, route=f"/contracts/webui/workspaces/{WORKSPACE}",
        icon="Layers", order=10, enabled=True, placement="sceneWorkspace",
    )
    (result,) = webui_routes._attach_scene_suites([workspace], edition_active=edition_active)
    return result.locked


@pytest.mark.asyncio
async def test_the_built_in_checker_does_not_grant_an_extensions_edition(client, acme_root):
    assert scene_suite_workspace_owners()[WORKSPACE] == SUITE
    suites = await _suites(client)
    assert suites[SUITE]["edition"] == "team" and suites[SUITE]["editionLocked"] is True
    assert suites["soc-workspace"]["editionLocked"] is None, "the open edition is never locked"
    for plugin_type, plugin_id in (("component", SUITE), ("webui", WORKSPACE)):
        decision = await suite_license.install_decision(plugin_type, plugin_id)
        assert (decision.allowed, decision.reason) == (False, "edition_required")
    assert _locked(edition_active=False)


@pytest.mark.asyncio
async def test_the_extensions_checker_grants_its_edition_and_licenses_its_suite(client, acme_root, acme_checker):
    suites = await _suites(client)
    assert (suites[SUITE]["editionLocked"], suites[SUITE]["licensed"]) == (False, False)
    decision = await suite_license.install_decision("component", SUITE)
    assert (decision.allowed, decision.reason) == (False, "suite_unlicensed")
    assert not _locked(edition_active=True)

    acme_checker.suites = {SUITE}
    suites = await _suites(client)
    assert (suites[SUITE]["editionLocked"], suites[SUITE]["licensed"]) == (False, True)
    for plugin_type, plugin_id in (("component", SUITE), ("webui", WORKSPACE)):
        assert (await suite_license.install_decision(plugin_type, plugin_id)).allowed
    assert suite_license.licensed_suite_for_workspace(WORKSPACE) == SUITE

    acme_checker.team_active = False
    suites = await _suites(client)
    assert suites[SUITE]["editionLocked"] is True, "its licence lapsing locks the suite again"
