"""Scene suites are the hub's component packages."""

import json

import pytest

from flocks.hub.catalog import list_catalog
from flocks.hub.paths import get_bundled_hub_root
from flocks.server.routes.hub import _assert_install_allowed, _suite_workspace_id


def _component_entries():
    return {entry.id: entry for entry in list_catalog() if entry.type == "component"}


def test_the_bundled_hub_lists_open_edition_plugins_only():
    index = json.loads((get_bundled_hub_root() / "index.json").read_text(encoding="utf-8"))

    assert {entry.get("edition", "oss") for entry in index["plugins"]} == {"oss"}
    assert {entry["id"] for entry in index["plugins"] if entry["type"] == "component"} == {"soc-workspace"}


def test_the_catalog_lists_the_bundled_suite():
    entries = _component_entries()

    assert set(entries) == {"soc-workspace"}
    assert entries["soc-workspace"].edition == "oss"


def test_suite_resolves_to_its_workspace():
    assert _suite_workspace_id("soc-workspace") == "soc_ui"


@pytest.mark.asyncio
async def test_without_a_hub_access_policy_a_suite_installs():
    # No exception: nothing registered refuses installs.
    await _assert_install_allowed("component", "soc-workspace")
