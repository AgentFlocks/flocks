"""Hub packages delivered sealed by an extension.

Case list (编号 | 场景):
  SH1 a sealed source's plugins are listed from its manifests and supersede
      the bundled announcement; without a source the announcement stays
  SH2 installing a sealed suite unseals its package once, installs the
      prebuilt pages without source, records the sealed origin and leaves
      no staging behind
  SH3 a plugin announced as sealed that no source provides is refused before
      anything is installed, and its payload is never taken from the bundle
  SH4 a refusal to unseal (no licence) fails the install with its reason,
      installs nothing and leaves no staging behind
  SH5 a package whose contents do not match its catalog entry is refused
  SH6 a prebuilt bundle for another runtime, or one that does not match its
      build record, is refused
  SH7 while a source cannot answer, the catalog falls back to the
      announcement and page ownership is undecided (never "no owner")
  SH8 an update preview of an installed sealed suite stages the package too
  SH9 staging left behind by a dead process is purged once it is stale
  SH10 through the hub routes: unlicensed install is 403 before anything is
       unsealed; licensed install succeeds; the plugin file listing shows
       nothing of an uninstalled sealed package
  SH11 a newer release listed while unsealing does not get the unsealed
       payload recorded under its version
  SH12 an optional part whose package cannot be unsealed is skipped; a
       required one stops the install
  SH13 a manifest that reads once and then fails does not turn a pro plugin
       into one that needs no suite licence
  SH14 the file preview of a sealed plugin never shows another listing of
       the same id (an extension root's plain sources), also while the
       sources cannot answer
  SH15 while the sources cannot answer, a suite the open hub announces as
       sealed, or one of its parts, is not installed from an extension root
       listing it in plain
  SH16 the file preview of a sealed tool shows nothing of a tool the open
       hub bundles under the same id
  SH17 the open hub's own manifest of a plugin decides whether it announces
       that plugin as sealed; only a plugin it has no manifest for is
       announced so by being a part of a suite announced as sealed
  SH18 the sources are asked before the manifest is read: a listing read
       while they could not answer is not previewed once they answer
  SH19 while the sources cannot answer, an optional part the open hub
       announces as sealed is skipped, not installed from an extension root
       listing it in plain
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.contracts.webui.store import WebUIPagesStore
from flocks.hub import catalog, installer, local, sealed
from flocks.hub.files import plugin_root
from flocks.hub.models import HubPluginManifest
from flocks.hub.sealed import SealedPackage, SealedPayloadUnavailable, SealedSourceUnavailable
from flocks.hub.suite_license import SuiteOwnershipUnknown, clear_page_suite_cache, shipped_page_owners
from flocks.server.auth import require_admin, require_user
from flocks.server.routes.hub import router
from tests.sealed_fakes import FakeSealedSource

SUITE = "code-audit-workspace"
WEBUI = "code_audit_ui"


@pytest.fixture
def hub_env(isolated_hub_env, monkeypatch):
    async def refresh(*args, **kwargs):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", refresh)
    catalog.clear_catalog_caches()
    yield isolated_hub_env
    catalog.clear_catalog_caches()


def _staging_dir(env) -> Path:
    return env["data_dir"] / "hub" / "staging"


def _entries():
    return {(entry.type, entry.id): entry for entry in catalog.list_catalog()}


# SH1
def test_sealed_plugins_supersede_the_announcement(hub_env, sealed_suites):
    entries = _entries()
    suite, webui = entries[("component", SUITE)], entries[("webui", WEBUI)]
    assert suite.source == "sealed" and suite.description == f"{SUITE} (sealed)"
    assert webui.source == "sealed" and webui.edition == "pro"
    assert webui.manifestPath == f"plugins/webuis/{WEBUI}/manifest.json"
    assert suite.state == webui.state == "available"


def test_without_a_source_the_announcement_stays(hub_env):
    entries = _entries()
    assert entries[("component", SUITE)].source == "bundled"
    assert entries[("component", SUITE)].edition == "pro"
    assert ("webui", WEBUI) not in entries


# SH2
@pytest.mark.asyncio
async def test_install_unseals_once_and_keeps_only_built_pages(hub_env, sealed_suites):
    record = await installer.install_plugin("component", SUITE)

    assert [suite for suite, _ in sealed_suites.unsealed] == [SUITE]
    assert not any(_staging_dir(hub_env).iterdir())
    assert not sealed_suites.unsealed[0][1].exists()

    webui_record = local.get_record("webui", WEBUI)
    assert record.source == f"sealed:plugins/components/{SUITE}"
    assert webui_record.source == f"sealed:plugins/webuis/{WEBUI}"
    assert webui_record.installedBy == f"component:{SUITE}"
    installed = Path(webui_record.installPath)
    assert not list(installed.rglob("src"))
    assert sorted(path.parent.parent.name for path in installed.rglob("dist/page.js")) == [
        "code_audit_findings", "code_audit_overview",
    ]
    store = WebUIPagesStore(root=installed, project_root=None, legacy_root=None)
    assert {page.id: page.buildStatus for page in store.list_pages()} == {
        "code-audit-overview": "ready", "code-audit-findings": "ready",
    }
    entries = _entries()
    assert entries[("component", SUITE)].state == entries[("webui", WEBUI)].state == "installed"


# SH3
@pytest.mark.asyncio
async def test_announced_suite_without_a_package_is_refused(hub_env):
    with pytest.raises(sealed.SealedPayloadUnavailable, match="no package"):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("component", SUITE) is None
    with pytest.raises(FileNotFoundError, match="sealed"):
        plugin_root("component", SUITE, prefer_bundled=True)


@pytest.mark.asyncio
async def test_sealed_payload_exists_only_while_staged(hub_env, sealed_suites):
    with pytest.raises(FileNotFoundError, match="sealed"):
        plugin_root("webui", WEBUI, prefer_bundled=True)
    async with sealed.staged_payloads("component", SUITE):
        staged = plugin_root("webui", WEBUI, prefer_bundled=True)
        assert (staged / "workspace.json").is_file()
    assert not staged.exists()


# SH4
@pytest.mark.asyncio
async def test_refused_unseal_installs_nothing(hub_env, sealed_suites):
    sealed_suites.unseal_error = "suite code-audit-workspace is not licensed (expired)"
    with pytest.raises(Exception, match="not licensed"):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("component", SUITE) is None
    assert local.get_record("webui", WEBUI) is None
    assert not any(_staging_dir(hub_env).iterdir())


# SH5
@pytest.mark.asyncio
async def test_contents_must_match_the_catalog(hub_env, sealed_suites):
    def bump_version(suite_id, destination, manifests):
        path = destination / "plugins" / "webuis" / WEBUI / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = "9.9.9"
        path.write_text(json.dumps(data), encoding="utf-8")

    sealed_suites.tamper = bump_version
    with pytest.raises(sealed.SealedPayloadUnavailable, match="does not match its catalog entry"):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("webui", WEBUI) is None

    def drop_webui(suite_id, destination, manifests):
        import shutil

        shutil.rmtree(destination / "plugins" / "webuis" / WEBUI)

    sealed_suites.tamper = drop_webui
    with pytest.raises(sealed.SealedPayloadUnavailable, match="holds no webui"):
        await installer.install_plugin("component", SUITE)


# SH6
@pytest.mark.asyncio
@pytest.mark.parametrize(("options", "reason"), [
    ({"runtime_version": 0}, "was built for"),
    ({"record_bundle": "something else"}, "does not match its build record"),
])
async def test_unusable_prebuilt_bundle_is_refused(hub_env, sealed_suites, options, reason):
    sealed_suites.tree_options = options
    with pytest.raises(RuntimeError, match=reason):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("webui", WEBUI) is None


# SH7
def test_unanswering_source_leaves_ownership_undecided(hub_env, sealed_suites):
    store = WebUIPagesStore()
    assert shipped_page_owners(store)["code-audit-overview"] == SUITE
    assert shipped_page_owners(store)["ai-redteam-surface"] == "ai-redteam-workspace"

    sealed_suites.unavailable = True
    entries = _entries()  # the catalog notices through the source's signature
    assert entries[("component", SUITE)].source == "bundled"
    assert ("webui", WEBUI) not in entries
    with pytest.raises(SuiteOwnershipUnknown):
        shipped_page_owners(store)

    sealed_suites.unavailable = False
    clear_page_suite_cache()
    assert shipped_page_owners(store)["code-audit-findings"] == SUITE


@pytest.mark.asyncio
async def test_unanswering_source_refuses_installs(hub_env, sealed_suites):
    sealed_suites.unavailable = True
    with pytest.raises(sealed.SealedPayloadUnavailable, match="cannot be listed"):
        await installer.install_plugin("component", SUITE)


# SH8
@pytest.mark.asyncio
async def test_update_preview_stages_the_package(hub_env, sealed_suites):
    await installer.install_plugin("component", SUITE)
    sealed_suites.versions[SUITE] = "1.1.0"
    plan = await installer.preview_plan("component", SUITE)
    versions = {(item["type"], item["id"]): (item["installedVersion"], item["version"]) for item in plan["items"]}
    assert versions[("component", SUITE)] == ("1.0.0", "1.1.0")
    assert versions[("webui", WEBUI)] == ("1.0.0", "1.1.0")
    assert len(sealed_suites.unsealed) == 2
    assert not any(_staging_dir(hub_env).iterdir())

    await installer.update_plugin("component", SUITE)
    assert local.get_record("webui", WEBUI).version == "1.1.0"


# SH9
@pytest.mark.asyncio
async def test_stale_staging_is_purged(hub_env, sealed_suites):
    staging = _staging_dir(hub_env)
    staging.mkdir(parents=True, exist_ok=True)
    stale, fresh = staging / "sealed-dead", staging / "sealed-running"
    for path in (stale, fresh):
        (path / "0").mkdir(parents=True)
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))

    async with sealed.staged_payloads("component", SUITE):
        pass
    assert not stale.exists()
    assert fresh.exists()


# SH10
@pytest.fixture
async def hub_client(hub_env):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[require_user] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_hub_routes_install_sealed_suites_by_licence(hub_client, pro_checker, sealed_suites):
    refused = await hub_client.post(f"/hub/plugins/component/{SUITE}/install", json={})
    assert refused.status_code == 403
    assert sealed_suites.unsealed == []

    files = await hub_client.get(f"/hub/plugins/webui/{WEBUI}/files")
    assert files.status_code == 404

    pro_checker.licensed = {SUITE}
    installed = await hub_client.post(f"/hub/plugins/component/{SUITE}/install", json={})
    assert installed.status_code == 200, installed.text
    assert installed.json()["source"] == f"sealed:plugins/components/{SUITE}"

    scenes = {item["id"]: item for item in (await hub_client.get("/hub/scene-suites")).json()}
    assert scenes[SUITE]["state"] == "installed" and scenes[SUITE]["licensed"] is True
    assert scenes["ai-redteam-workspace"]["licensed"] is False


# SH11
@pytest.mark.asyncio
async def test_install_records_the_version_it_unsealed(hub_env, sealed_suites):
    """The source lists a newer version while the package is being unsealed:
    the install still records -- and checks against -- what it unsealed."""
    real_unseal = sealed_suites.unseal

    async def unseal_then_newer_release(package, destination):
        await real_unseal(package, destination)
        sealed_suites.versions[SUITE] = "1.2.0"

    sealed_suites.unseal = unseal_then_newer_release
    await installer.install_plugin("component", SUITE)
    assert local.get_record("component", SUITE).version == "1.0.0"
    assert local.get_record("webui", WEBUI).version == "1.0.0"
    assert _entries()[("component", SUITE)].state == "updateAvailable"


class _AddonSource(FakeSealedSource):
    """The audit suite with an optional part that comes in a package of its own."""

    name = "test-sealed-addon"

    def __init__(self, *, optional: bool) -> None:
        super().__init__()
        self.optional = optional

    def _packages(self):
        packages = super()._packages()
        package, manifests = packages[SUITE]
        component = dict(manifests["component"])
        component["components"] = [*component["components"], {"type": "webui", "id": "addon_ui", "optional": self.optional}]
        addon = {**manifests["webui"], "id": "addon_ui", "source": {"kind": "sealed", "path": "plugins/webuis/addon_ui"}}
        packages[SUITE] = (
            SealedPackage(
                id=SUITE, version=package.version,
                plugins=(HubPluginManifest.model_validate(component), package.plugins[1]), pages=package.pages,
            ),
            {**manifests, "component": component},
        )
        packages["addon-pack"] = (
            SealedPackage(id="addon-pack", version="1.0.0", plugins=(HubPluginManifest.model_validate(addon),)),
            {"webui": addon},
        )
        return packages

    async def unseal(self, package, destination):
        if package.id == "addon-pack":
            raise SealedPayloadUnavailable("the add-on is not licensed")
        await super().unseal(package, destination)


@pytest.fixture
def addon_source(request):
    source = _AddonSource(optional=request.param)
    sealed.register_sealed_source(source)
    catalog.clear_catalog_caches()
    clear_page_suite_cache()
    yield source
    sealed.unregister_sealed_source(source)
    catalog.clear_catalog_caches()
    clear_page_suite_cache()


# SH12
@pytest.mark.asyncio
@pytest.mark.parametrize("addon_source", [True], indirect=True)
async def test_unavailable_optional_part_is_skipped(hub_env, addon_source):
    await installer.install_plugin("component", SUITE)
    assert local.get_record("webui", WEBUI) is not None
    assert local.get_record("webui", "addon_ui") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("addon_source", [False], indirect=True)
async def test_unavailable_required_part_stops_the_install(hub_env, addon_source):
    with pytest.raises(sealed.SealedPayloadUnavailable, match="not licensed"):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("component", SUITE) is None


# SH13
@pytest.mark.asyncio
async def test_a_second_read_failing_does_not_waive_the_suite_licence(hub_env, sealed_suites, pro_checker, monkeypatch):
    from flocks.hub import suite_license

    real = suite_license.load_manifest
    reads = {"webui": 0}

    def flaky(plugin_type, plugin_id):
        if (plugin_type, plugin_id) == ("webui", WEBUI):
            reads["webui"] += 1
            if reads["webui"] > 1:
                raise OSError("source went away")
        return real(plugin_type, plugin_id)

    monkeypatch.setattr(suite_license, "load_manifest", flaky)
    decision = await suite_license.install_decision("webui", WEBUI)
    assert not decision.allowed and decision.reason == "suite_unlicensed"


def _plain_extension_suite(root: Path) -> None:
    """The same suite as plain sources in an extension hub root (page development)."""
    for plugin_type, plugin_id, extra in (
        ("component", SUITE, {"components": [{"type": "webui", "id": WEBUI}], "entrypoints": ["component.json"]}),
        ("webui", WEBUI, {"entrypoints": ["workspace.json"]}),
    ):
        folder = root / "plugins" / f"{plugin_type}s" / plugin_id
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({
            "schemaVersion": "hub.plugin.v1", "id": plugin_id, "type": plugin_type, "name": plugin_id,
            "description": "plain sources", "version": "9.9.9", "trust": "official", "edition": "pro",
            "source": {"kind": "bundled", "path": f"plugins/{plugin_type}s/{plugin_id}"}, **extra,
        }), encoding="utf-8")
    (root / "plugins" / "components" / SUITE / "component.json").write_text(
        json.dumps({"schemaVersion": "hub.component.v1", "id": SUITE, "components": [{"type": "webui", "id": WEBUI}]}),
        encoding="utf-8",
    )
    (root / "plugins" / "webuis" / WEBUI / "workspace.json").write_text('{"id": "%s", "secret": "plain"}' % WEBUI, encoding="utf-8")
    (root / "index.json").write_text(json.dumps({"schemaVersion": "hub.index.v1", "plugins": [
        {"id": SUITE, "type": "component", "name": SUITE, "version": "9.9.9", "trust": "official", "edition": "pro",
         "manifestPath": f"plugins/components/{SUITE}/manifest.json"},
        {"id": WEBUI, "type": "webui", "name": WEBUI, "version": "9.9.9", "trust": "official", "edition": "pro",
         "manifestPath": f"plugins/webuis/{WEBUI}/manifest.json"},
    ]}), encoding="utf-8")


# SH14
@pytest.mark.asyncio
async def test_preview_of_a_sealed_plugin_shows_no_plain_listing(hub_client, sealed_suites, tmp_path):
    from flocks.extensions import register_hub_root, unregister_hub_root

    _plain_extension_suite(tmp_path / "plain")
    register_hub_root("sh14-plain", tmp_path / "plain")
    try:
        for unavailable in (False, True):
            sealed_suites.unavailable = unavailable
            assert (await hub_client.get(f"/hub/plugins/webui/{WEBUI}/files")).status_code == 404, unavailable
            content = await hub_client.get(f"/hub/plugins/webui/{WEBUI}/files/content", params={"path": "workspace.json"})
            assert content.status_code == 404 and "plain" not in content.text, unavailable
    finally:
        sealed_suites.unavailable = False
        unregister_hub_root("sh14-plain")


# SH15
@pytest.mark.asyncio
async def test_unanswering_source_keeps_plain_sources_from_installing(hub_env, sealed_suites, tmp_path):
    from flocks.extensions import register_hub_root, unregister_hub_root

    _plain_extension_suite(tmp_path / "plain")
    register_hub_root("sh15-plain", tmp_path / "plain")
    sealed_suites.unavailable = True
    try:
        with pytest.raises(sealed.SealedPayloadUnavailable, match="cannot be listed"):
            await installer.install_plugin("component", SUITE)
        # The suite's part on its own: the open hub announces it through the suite.
        with pytest.raises(sealed.SealedPayloadUnavailable, match="cannot be listed"):
            await installer.install_plugin("webui", WEBUI)
        assert local.get_record("component", SUITE) is None
        assert local.get_record("webui", WEBUI) is None
    finally:
        unregister_hub_root("sh15-plain")


class _ToolSource(FakeSealedSource):
    """A pro tool, sealed, under the id of a tool the open hub bundles."""

    name = "test-sealed-tool"

    def __init__(self, tool_id: str) -> None:
        super().__init__()
        self.tool_id = tool_id

    def _packages(self):
        manifest = HubPluginManifest.model_validate({
            "schemaVersion": "hub.plugin.v1", "id": self.tool_id, "type": "tool", "name": self.tool_id,
            "description": "sealed tool", "version": "1.0.0", "category": "integration", "trust": "official",
            "edition": "pro", "source": {"kind": "sealed", "path": f"plugins/tools/{self.tool_id}"},
        })
        return {"tool-pack": (SealedPackage(id="tool-pack", version="1.0.0", plugins=(manifest,)), {})}


# SH16
@pytest.mark.asyncio
async def test_preview_of_a_sealed_tool_shows_no_bundled_tool(hub_client, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled_tool"
    bundled.mkdir()
    (bundled / "_provider.yaml").write_text("name: shared_tool\nsecret: plain\n", encoding="utf-8")
    monkeypatch.setattr(catalog, "_bundled_tool_roots", lambda: {("tool", "shared_tool"): bundled})
    source = _ToolSource("shared_tool")
    sealed.register_sealed_source(source)
    catalog.clear_catalog_caches()
    try:
        assert (await hub_client.get("/hub/plugins/tool/shared_tool/files")).status_code == 404
        content = await hub_client.get("/hub/plugins/tool/shared_tool/files/content", params={"path": "_provider.yaml"})
        assert content.status_code == 404 and "plain" not in content.text
    finally:
        sealed.unregister_sealed_source(source)
        sealed.clear_sealed_cache()
        catalog.clear_catalog_caches()


# SH17
def test_own_manifest_decides_whether_a_part_is_announced_sealed(hub_env, sealed_suites, tmp_path, monkeypatch):
    from flocks.hub import paths

    hub = tmp_path / "bundled"
    for plugin_type, plugin_id, manifest in (
        ("component", "sealed-suite", {"source": {"kind": "sealed"}, "components": [
            {"type": "webui", "id": "suite_ui"}, {"type": "skill", "id": "shared-skill"},
        ]}),
        ("skill", "shared-skill", {"source": {"kind": "bundled", "path": "plugins/skills/shared-skill"}}),
    ):
        folder = hub / "plugins" / f"{plugin_type}s" / plugin_id
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"id": plugin_id, "type": plugin_type, **manifest}), encoding="utf-8")
    (hub / "index.json").write_text(json.dumps({"plugins": [
        {"id": "sealed-suite", "type": "component"}, {"id": "shared-skill", "type": "skill"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(paths, "get_bundled_hub_root", lambda: hub)

    # A part with no manifest of its own in the open hub: sealed with its suite.
    assert sealed.announced_sealed("webui", "suite_ui") is True
    # A part the open hub ships in plain itself: its own manifest says so.
    assert sealed.announced_sealed("skill", "shared-skill") is False
    sealed_suites.unavailable = True
    with pytest.raises(SealedPayloadUnavailable, match="cannot be listed"):
        sealed._plan("webui", "suite_ui")
    assert sealed._plan("skill", "shared-skill") == []


class _AnswersFromTheSecondAsk(FakeSealedSource):
    """Cannot answer the first time it is asked, then answers (the core
    finishing its first listing)."""

    def __init__(self) -> None:
        super().__init__()
        self.unavailable = True

    def packages(self):
        if self.unavailable:
            self.unavailable = False
            raise SealedSourceUnavailable("being listed")
        return super().packages()


# SH18
def test_a_listing_read_while_the_sources_could_not_answer_is_not_previewed(hub_env, tmp_path):
    from flocks.extensions import register_hub_root, unregister_hub_root
    from flocks.hub.files import read_file_content

    source = _AnswersFromTheSecondAsk()
    sealed.register_sealed_source(source)
    _plain_extension_suite(tmp_path / "plain")
    register_hub_root("sh18-plain", tmp_path / "plain")
    catalog.clear_catalog_caches()
    clear_page_suite_cache()
    try:
        with pytest.raises(FileNotFoundError):
            read_file_content("webui", WEBUI, "workspace.json")
        assert not source.unavailable  # asked, and answering by now
    finally:
        unregister_hub_root("sh18-plain")
        sealed.unregister_sealed_source(source)
        sealed.clear_sealed_cache()
        catalog.clear_catalog_caches()
        clear_page_suite_cache()


def _plain_suite_with_optional_part(root: Path, suite_id: str) -> None:
    """A suite of the extension root's own, in plain, that lists the sealed
    suite's page plugin as an optional part."""
    components = [{"type": "webui", "id": WEBUI, "optional": True}]
    folder = root / "plugins" / "components" / suite_id
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "schemaVersion": "hub.plugin.v1", "id": suite_id, "type": "component", "name": suite_id,
        "description": "plain suite", "version": "1.0.0", "trust": "official", "edition": "oss",
        "source": {"kind": "bundled", "path": f"plugins/components/{suite_id}"},
        "entrypoints": ["component.json"], "components": components,
    }), encoding="utf-8")
    (folder / "component.json").write_text(
        json.dumps({"schemaVersion": "hub.component.v1", "id": suite_id, "components": components}), encoding="utf-8",
    )
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    index["plugins"].append({
        "id": suite_id, "type": "component", "name": suite_id, "version": "1.0.0", "trust": "official",
        "edition": "oss", "manifestPath": f"plugins/components/{suite_id}/manifest.json",
    })
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")


# SH19
@pytest.mark.asyncio
async def test_unanswering_source_skips_an_optional_sealed_part(hub_env, sealed_suites, pro_checker, tmp_path):
    from flocks.extensions import register_hub_root, unregister_hub_root

    _plain_extension_suite(tmp_path / "plain")
    _plain_suite_with_optional_part(tmp_path / "plain", "dev-suite")
    register_hub_root("sh19-plain", tmp_path / "plain")
    pro_checker.licensed = {SUITE}
    sealed_suites.unavailable = True
    events = []

    async def progress(event):
        events.append(event)

    try:
        await installer.install_plugin("component", "dev-suite", progress=progress)
        assert local.get_record("component", "dev-suite") is not None
        assert local.get_record("webui", WEBUI) is None
        # Skipped as sealed, before anything was read from the plain listing.
        skipped = [event.item for event in events if event.item is not None and event.item.status == "skipped"]
        assert [(item.id, "delivered sealed" in (item.message or "")) for item in skipped] == [(WEBUI, True)]
    finally:
        unregister_hub_root("sh19-plain")
