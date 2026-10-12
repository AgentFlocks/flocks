"""Scene suites are the hub's component packages, gated by edition."""

import pytest
from fastapi import HTTPException

from flocks.hub.catalog import list_catalog, load_manifest
from flocks.server.routes.hub import _assert_edition_allowed, _suite_workspace_id


def _component_entries():
    return {entry.id: entry for entry in list_catalog() if entry.type == "component"}


def test_bundled_scene_suites_declare_their_edition():
    entries = _component_entries()

    assert set(entries) == {"soc-workspace", "code-audit-workspace", "ai-redteam-workspace"}
    assert entries["soc-workspace"].edition == "oss"
    assert entries["code-audit-workspace"].edition == "pro"
    assert entries["ai-redteam-workspace"].edition == "pro"


@pytest.mark.parametrize(
    ("suite_id", "workspace_id"),
    [
        ("soc-workspace", "soc_ui"),
        ("code-audit-workspace", "code_audit_ui"),
        ("ai-redteam-workspace", "ai_redteam_ui"),
    ],
)
def test_suite_resolves_to_its_workspace(suite_id: str, workspace_id: str):
    assert _suite_workspace_id(suite_id) == workspace_id


def test_pro_workspaces_are_scene_workspaces(sealed_suites):
    for workspace_id in ("code_audit_ui", "ai_redteam_ui"):
        manifest = load_manifest("webui", workspace_id)
        assert manifest.edition == "pro"
        assert manifest.source.kind == "sealed"


def test_open_source_hub_only_announces_pro_suites():
    # The pro suites' pages are delivered sealed by an extension; the hub
    # bundled here lists the suites and carries none of their contents.
    for suite_id in ("code-audit-workspace", "ai-redteam-workspace"):
        assert load_manifest("component", suite_id).source.kind == "sealed"
    for workspace_id in ("code_audit_ui", "ai_redteam_ui"):
        with pytest.raises(FileNotFoundError):
            load_manifest("webui", workspace_id)


@pytest.mark.asyncio
async def test_oss_may_install_an_oss_suite():
    # No exception: the OSS license checker reports edition "oss" and this suite
    # does not require Pro.
    await _assert_edition_allowed("component", "soc-workspace")


@pytest.mark.asyncio
@pytest.mark.parametrize("suite_id", ["code-audit-workspace", "ai-redteam-workspace"])
async def test_oss_may_not_install_a_pro_suite(suite_id: str):
    with pytest.raises(HTTPException) as excinfo:
        await _assert_edition_allowed("component", suite_id)

    assert excinfo.value.status_code == 403
    assert "Flocks Pro" in excinfo.value.detail


@pytest.mark.asyncio
async def test_pro_license_alone_does_not_unlock_a_pro_suite(pro_checker):
    # Each Pro suite is licensed on its own: the platform licence is not enough.
    with pytest.raises(HTTPException) as excinfo:
        await _assert_edition_allowed("component", "code-audit-workspace")

    assert excinfo.value.status_code == 403
    assert "单独授权" in excinfo.value.detail


@pytest.mark.asyncio
async def test_suite_license_unlocks_its_own_suite_only(pro_checker, sealed_suites):
    pro_checker.licensed = {"code-audit-workspace"}

    await _assert_edition_allowed("component", "code-audit-workspace")
    # The suite's pages ship with it and are covered by the same licence.
    await _assert_edition_allowed("webui", "code_audit_ui")
    with pytest.raises(HTTPException):
        await _assert_edition_allowed("component", "ai-redteam-workspace")
    with pytest.raises(HTTPException):
        await _assert_edition_allowed("webui", "ai_redteam_ui")


@pytest.mark.asyncio
async def test_suite_license_needs_an_active_platform_license(pro_checker):
    pro_checker.licensed = {"code-audit-workspace"}
    pro_checker.platform_active = False

    with pytest.raises(HTTPException) as excinfo:
        await _assert_edition_allowed("component", "code-audit-workspace")
    assert "Flocks Pro" in excinfo.value.detail
