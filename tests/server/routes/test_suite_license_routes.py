"""Scene suite licences at run time.

Each Pro scene suite is licensed on its own. Under Flocks Pro a suite whose
licence is not in force is not offered by the navigation, and its pages --
bundle, assets, API, sources -- are refused. The open edition enforces
nothing here: it never installs a Pro suite, and one present in a development
checkout keeps working.

Case list (编号 | 场景):
  SR1 Pro, suite unlicensed: bundle, asset, page detail, API listing and API
      call are 403; licensed: all served
  SR2 Pro: an unlicensed suite's workspace is listed locked (licensed=False)
      and left out of the navigation, its pages are not listed; a licensed
      one is marked
  SR3 Pro: a workspace that belongs to no Pro suite is unaffected
  SR4 open edition: a Pro suite's page is served, nothing is marked
  SR5 scene suites listing: under Pro each Pro suite carries its own licence
      state; on the open edition none does
  SR6 a licence check the checker cannot answer refuses the page (fail closed)
  SR7 a suite page is the suite's by its id: a standalone copy under that id
      is still gated, and importing over it needs the suite's licence
  SR8 when ownership cannot be decided the page is refused under a suite
      licensing checker (and served under the built-in one); nothing
      undecided is cached
  SR9 the ownership cache keeps no made-up page ids and stays bounded
  SR10 the page listing hides a standalone copy of an unlicensed suite page
  SR11 installing: an unreadable manifest is undecided (refused under a suite
       licensing checker), a missing plugin is left to the installer
  SR12 an index built before a cache clear is not written back after it
  SR13 a suite's installed workspace names its pages even without the
       hub package
  SR14 ownership read before an extension hub root was registered is not
       kept: the root's suite owns its page ids at once, with no cache clear,
       and stops owning them when the root goes away
  SR15 a page's data contract operations are gated like the page: refused
       while its suite is unlicensed, left to the contract runtime otherwise
       (and always for a contract path that is not a page id)
  SR16 what the ownership signature cannot see -- a page added to a suite's
       installed workspace, then copied out as a standalone page -- counts
       once the index's time is up, without a cache clear
  SR17 a page's answer taken from an index about to expire expires with it:
       the page cache does not give it a fresh lifetime of its own
  SR18 so does one taken while another lookup reads the index again: the
       answer keeps the read time of the index it came from
"""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from types import SimpleNamespace

from flocks.contracts.webui.builder import WebUIPageBuilder
from flocks.contracts.webui.store import WebUIPagesStore
from flocks.hub import suite_license as suite_license_mod
from flocks.server.routes import webui as webui_routes
from tests.license_fakes import registered_pro_checker

PRO_SUITE = "audit-suite"
OSS_SUITE = "ops-suite"


def _write_workspace(root, workspace_id: str, page_id: str, page_dir_name: str) -> None:
    workspace_dir = root / workspace_id
    page_dir = workspace_dir / page_dir_name
    for sub in ("src", "dist", "assets", "api"):
        (page_dir / sub).mkdir(parents=True, exist_ok=True)
    (workspace_dir / "workspace.json").write_text(
        json.dumps({
            "id": workspace_id, "title": workspace_id, "icon": "ShieldCheck", "order": 10, "enabled": True,
            "placement": "sceneWorkspace", "defaultPageId": page_id,
            "sections": [{"id": "main", "label": "main", "pageIds": [page_id], "defaultPageId": page_id}],
        }),
        encoding="utf-8",
    )
    (page_dir / "manifest.json").write_text(
        json.dumps({
            "id": page_id, "title": page_id, "route": f"/contracts/webui/{page_id}", "icon": "LayoutDashboard",
            "order": 10, "enabled": True, "placement": "home.after", "entry": "src/index.tsx", "updatedAt": 0,
        }),
        encoding="utf-8",
    )
    (page_dir / "dist" / "page.js").write_text("export default function Page(){return null;}", encoding="utf-8")
    (page_dir / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (page_dir / "api" / "routes.yaml").write_text(
        "routes:\n  - method: GET\n    path: /stats\n    handler: handlers.get_stats\n", encoding="utf-8"
    )
    (page_dir / "api" / "handlers.py").write_text("def get_stats(ctx, request):\n    return {'ok': True}\n", encoding="utf-8")


@pytest.fixture
def suite_pages(tmp_path, monkeypatch):
    """A Pro suite workspace (audit_ws/audit-page) and an open one (ops_ws/ops-page)."""
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    webui_routes.reset_route_dependencies(store=store, builder=WebUIPageBuilder(store))
    _write_workspace(store.root, "audit_ws", "audit-page", "audit_page")
    _write_workspace(store.root, "ops_ws", "ops-page", "ops_page")

    owners = {"audit_ws": PRO_SUITE, "ops_ws": OSS_SUITE}
    editions = {PRO_SUITE: "pro", OSS_SUITE: "oss"}
    monkeypatch.setattr(webui_routes, "scene_suite_workspace_owners", lambda: dict(owners))
    monkeypatch.setattr(suite_license_mod, "scene_suite_workspace_owners", lambda: dict(owners))
    monkeypatch.setattr(suite_license_mod, "_plugin_edition", lambda plugin_type, plugin_id: editions.get(plugin_id))

    def suite_edition(suite_id):
        if suite_id not in editions:
            raise suite_license_mod.SuiteOwnershipUnknown(suite_id)
        return editions[suite_id]

    monkeypatch.setattr(suite_license_mod, "_suite_edition", suite_edition)
    suite_license_mod.clear_page_suite_cache()
    yield store
    suite_license_mod.clear_page_suite_cache()


PAGE_REQUESTS = [
    ("GET", "/api/contracts/webui/pages/audit-page/bundle.js"),
    ("GET", "/api/contracts/webui/pages/audit-page/assets/logo.svg"),
    ("GET", "/api/contracts/webui/pages/audit-page"),
    ("GET", "/api/contracts/webui/pages/audit-page/api"),
    ("GET", "/api/contracts/webui/pages/audit-page/api/stats"),
    ("GET", "/api/user-defined-pages/audit-page/bundle.js"),
]


# SR1
@pytest.mark.asyncio
async def test_unlicensed_pro_suite_pages_are_refused(client: AsyncClient, suite_pages):
    with registered_pro_checker() as checker:
        for method, url in PAGE_REQUESTS:
            resp = await client.request(method, url)
            assert resp.status_code == 403, (url, resp.status_code, resp.text)
            assert "场景套件授权" in resp.json()["message"]

        checker.licensed = {PRO_SUITE}
        for method, url in PAGE_REQUESTS:
            resp = await client.request(method, url)
            assert resp.status_code == 200, (url, resp.status_code, resp.text)


# SR2
@pytest.mark.asyncio
async def test_navigation_hides_unlicensed_suite(client: AsyncClient, suite_pages):
    with registered_pro_checker() as checker:
        nav = await client.get("/api/contracts/webui/workspaces", params={"enabledOnly": True})
        assert [item["id"] for item in nav.json()] == ["ops_ws"]

        # The scene manager still lists it, locked, like a Pro scene without Pro.
        full = await client.get("/api/contracts/webui/workspaces")
        marks = {item["id"]: (item["locked"], item["licensed"]) for item in full.json()}
        assert marks == {"audit_ws": (True, False), "ops_ws": (False, None)}

        for params in ({"enabledOnly": True}, {}):
            pages = await client.get("/api/contracts/webui/pages", params=params)
            assert [item["id"] for item in pages.json()] == ["ops-page"]

        checker.licensed = {PRO_SUITE}
        nav = await client.get("/api/contracts/webui/workspaces", params={"enabledOnly": True})
        marks = {item["id"]: item["licensed"] for item in nav.json()}
        assert marks == {"audit_ws": True, "ops_ws": None}
        pages = await client.get("/api/contracts/webui/pages", params={"enabledOnly": True})
        assert sorted(item["id"] for item in pages.json()) == ["audit-page", "ops-page"]


# SR3
@pytest.mark.asyncio
async def test_open_suite_pages_are_not_gated_under_pro(client: AsyncClient, suite_pages):
    with registered_pro_checker() as checker:
        resp = await client.get("/api/contracts/webui/pages/ops-page/bundle.js")
        assert resp.status_code == 200
        assert suite_license_mod.suite_feature(OSS_SUITE) not in checker.asked


# SR4
@pytest.mark.asyncio
async def test_open_edition_serves_pro_suite_pages(client: AsyncClient, suite_pages):
    for method, url in PAGE_REQUESTS:
        resp = await client.request(method, url)
        assert resp.status_code == 200, (url, resp.status_code, resp.text)
    full = await client.get("/api/contracts/webui/workspaces", params={"enabledOnly": True})
    assert {item["id"]: item["licensed"] for item in full.json()} == {"audit_ws": None, "ops_ws": None}


# SR5
@pytest.mark.asyncio
async def test_scene_suites_carry_their_own_licence(client: AsyncClient):
    resp = await client.get("/api/hub/scene-suites")
    assert resp.status_code == 200
    assert all(item["licensed"] is None for item in resp.json())

    with registered_pro_checker() as checker:
        checker.licensed = {"code-audit-workspace"}
        resp = await client.get("/api/hub/scene-suites")
    suites = {item["id"]: item for item in resp.json()}
    assert suites["code-audit-workspace"]["licensed"] is True
    assert suites["code-audit-workspace"]["licenseState"] == "commercial"
    assert suites["code-audit-workspace"]["licenseExpiresAt"] == 1893456000000
    assert suites["ai-redteam-workspace"]["licensed"] is False
    assert suites["ai-redteam-workspace"]["licenseState"] == "unlicensed"
    # An open-edition suite has no licence of its own.
    assert suites["soc-workspace"]["licensed"] is None


# SR6
@pytest.mark.asyncio
async def test_unanswerable_licence_check_refuses(client: AsyncClient, suite_pages):
    with registered_pro_checker() as checker:
        checker.licensed = {PRO_SUITE}

        async def broken(feature=None):
            raise RuntimeError("core unreachable")

        with patch.object(checker, "feature_status", broken):
            resp = await client.get("/api/contracts/webui/pages/audit-page/bundle.js")
        assert resp.status_code == 403


def _write_standalone_page(root, page_id: str) -> None:
    page_dir = root / page_id
    (page_dir / "dist").mkdir(parents=True, exist_ok=True)
    (page_dir / "manifest.json").write_text(
        json.dumps({
            "id": page_id, "title": page_id, "route": f"/contracts/webui/{page_id}", "icon": "LayoutDashboard",
            "order": 10, "enabled": True, "placement": "home.after", "entry": "src/index.tsx", "updatedAt": 0,
        }),
        encoding="utf-8",
    )
    (page_dir / "dist" / "page.js").write_text("export default function Page(){return null;}", encoding="utf-8")


# SR7
@pytest.mark.asyncio
async def test_suite_pages_are_the_suites_by_id(client: AsyncClient, suite_pages, monkeypatch):
    _write_standalone_page(suite_pages.root, "loose-page")
    monkeypatch.setattr(suite_license_mod, "shipped_page_owners", lambda store: {"loose-page": PRO_SUITE})
    suite_license_mod.clear_page_suite_cache()
    with registered_pro_checker() as checker:
        # A standalone page under an id the suite ships: still the suite's.
        assert (await client.get("/api/contracts/webui/pages/loose-page/bundle.js")).status_code == 403

        # Importing over the suite's page needs the suite's licence; nothing is replaced.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("audit-page/manifest.json", json.dumps({
                "id": "audit-page", "title": "replaced", "route": "/contracts/webui/audit-page", "icon": "LayoutDashboard",
                "order": 10, "enabled": True, "placement": "home.after", "entry": "src/index.tsx", "updatedAt": 0,
            }))
            zf.writestr("audit-page/src/index.tsx", "export default function Page(){return null;}")
        before = (suite_pages.root / "audit_ws" / "audit_page" / "manifest.json").read_text(encoding="utf-8")
        resp = await client.post(
            "/api/contracts/webui/pages/import",
            params={"overwrite": True},
            files={"file": ("audit-page.zip", buffer.getvalue(), "application/zip")},
        )
        assert resp.status_code == 403, resp.text
        assert not (suite_pages.root / "audit-page").exists()
        assert (suite_pages.root / "audit_ws" / "audit_page" / "manifest.json").read_text(encoding="utf-8") == before

        checker.licensed = {PRO_SUITE}
        assert (await client.get("/api/contracts/webui/pages/loose-page/bundle.js")).status_code == 200


# SR8
@pytest.mark.asyncio
async def test_undecided_ownership_is_not_an_allow(client: AsyncClient, suite_pages, monkeypatch):
    def unreadable(suite_id):
        raise suite_license_mod.SuiteOwnershipUnknown(f"{suite_id}: manifest unreadable")

    monkeypatch.setattr(suite_license_mod, "_suite_edition", unreadable)
    suite_license_mod.clear_page_suite_cache()
    url = "/api/contracts/webui/pages/audit-page/bundle.js"
    # The built-in checker enforces nothing, decided or not.
    assert (await client.get(url)).status_code == 200
    with registered_pro_checker() as checker:
        checker.licensed = {PRO_SUITE}
        resp = await client.get(url)
        assert resp.status_code == 503, resp.text
        assert "无法确认" in resp.json()["message"]
        # Once the manifest reads again, the answer is decided at once:
        # the undecided result was not cached.
        monkeypatch.setattr(suite_license_mod, "_suite_edition", lambda suite_id: {PRO_SUITE: "pro"}.get(suite_id, "oss"))
        assert (await client.get(url)).status_code == 200


# SR9
def test_ownership_cache_keeps_no_made_up_ids(suite_pages, monkeypatch):
    monkeypatch.setattr(suite_license_mod, "shipped_page_owners", lambda store: {})
    suite_license_mod.clear_page_suite_cache()
    for n in range(50):
        assert suite_license_mod.pro_suite_for_page(f"no-such-page-{n}", suite_pages) is None
    assert suite_license_mod._page_suite_cache == {}
    assert suite_license_mod.pro_suite_for_page("audit-page", suite_pages) == PRO_SUITE
    assert set(suite_license_mod._page_suite_cache) == {"audit-page"}

    monkeypatch.setattr(suite_license_mod, "_PAGE_SUITE_CACHE_MAX", 4)
    monkeypatch.setattr(suite_pages, "page_exists", lambda page_id: True)
    for n in range(10):
        suite_license_mod.pro_suite_for_page(f"page-{n}", suite_pages)
    assert len(suite_license_mod._page_suite_cache) <= 4


# SR10
@pytest.mark.asyncio
async def test_listing_hides_standalone_copies(client: AsyncClient, suite_pages, monkeypatch):
    _write_standalone_page(suite_pages.root, "loose-page")
    owners = lambda store: {"loose-page": PRO_SUITE}  # noqa: E731
    monkeypatch.setattr(suite_license_mod, "shipped_page_owners", owners)
    monkeypatch.setattr(webui_routes, "shipped_page_owners", owners)
    suite_license_mod.clear_page_suite_cache()
    with registered_pro_checker() as checker:
        listed = [item["id"] for item in (await client.get("/api/contracts/webui/pages")).json()]
        assert "loose-page" not in listed and "audit-page" not in listed
        checker.licensed = {PRO_SUITE}
        listed = [item["id"] for item in (await client.get("/api/contracts/webui/pages")).json()]
        assert "loose-page" in listed and "audit-page" in listed


# SR11
@pytest.mark.asyncio
async def test_install_with_unreadable_manifest_is_undecided(monkeypatch):
    def broken(plugin_type, plugin_id):
        if plugin_id == "missing":
            raise FileNotFoundError(plugin_id)
        raise ValueError("manifest is not valid JSON")

    monkeypatch.setattr(suite_license_mod, "load_manifest", broken)
    # The built-in checker: nothing to decide, the installer reports problems.
    assert (await suite_license_mod.install_decision("component", "code-audit-workspace")).allowed
    with registered_pro_checker():
        decision = await suite_license_mod.install_decision("component", "code-audit-workspace")
        assert not decision.allowed and decision.reason == "ownership_unknown"
        assert (await suite_license_mod.install_decision("component", "missing")).allowed


# SR12
def test_index_from_before_a_clear_is_not_kept(suite_pages, monkeypatch):
    suite_license_mod.clear_page_suite_cache()

    def catalog_cleared_midway(plugin_type=None):
        suite_license_mod.clear_page_suite_cache()  # a hub refresh lands while the index is built
        return []

    monkeypatch.setattr(suite_license_mod, "list_catalog", catalog_cleared_midway)
    assert suite_license_mod.shipped_page_owners(suite_pages) == {}
    assert suite_license_mod._shipped_pages is None


def _suite_ships_its_installed_workspace(monkeypatch) -> None:
    """The Pro suite in the catalog, shipping workspace audit_ws; its pages
    are the ones installed there (no hub package to read them from)."""
    monkeypatch.setattr(
        suite_license_mod, "list_catalog",
        lambda plugin_type=None: [SimpleNamespace(id=PRO_SUITE, edition="pro")],
    )
    monkeypatch.setattr(
        suite_license_mod, "load_manifest",
        lambda plugin_type, plugin_id: SimpleNamespace(components=[SimpleNamespace(type="webui", id="audit_ws")]),
    )
    monkeypatch.setattr(suite_license_mod, "_bundled_page_ids", lambda webui_id: set())


def _add_page_to_suite_workspace(store, page_id: str) -> None:
    """What an admin adding a page to the installed suite workspace leaves:
    nothing the ownership signature reads changes."""
    page_dir = store.root / "audit_ws" / page_id.replace("-", "_")
    (page_dir / "dist").mkdir(parents=True)
    (page_dir / "manifest.json").write_text(json.dumps({
        "id": page_id, "title": page_id, "route": f"/contracts/webui/{page_id}", "icon": "LayoutDashboard",
        "order": 20, "enabled": True, "placement": "home.after", "entry": "src/index.tsx", "updatedAt": 0,
    }), encoding="utf-8")


# SR13
def test_installed_workspace_names_suite_pages(suite_pages, monkeypatch):
    suite_license_mod.clear_page_suite_cache()
    _suite_ships_its_installed_workspace(monkeypatch)
    assert suite_license_mod.shipped_page_owners(suite_pages) == {"audit-page": PRO_SUITE}
    suite_license_mod.clear_page_suite_cache()


def _write_extension_suite(root, suite_id: str, webui_id: str, page_id: str) -> None:
    """An extension hub root shipping one pro suite with one page."""
    def manifest(plugin_id, plugin_type, **extra):
        return {
            "schemaVersion": "hub.plugin.v1", "id": plugin_id, "type": plugin_type, "name": plugin_id,
            "description": plugin_id, "version": "2.0.0", "author": "Tests", "license": "MIT",
            "category": "workflow-automation", "trust": "official", "edition": "pro",
            "source": {"kind": "bundled", "path": f"plugins/{plugin_type}s/{plugin_id}"},
            "compatibility": {"flocks": ">=0.8.0", "os": ["darwin", "linux", "windows"]}, **extra,
        }

    suite = manifest(suite_id, "component", entrypoints=["component.json"], components=[{"type": "webui", "id": webui_id}])
    webui = manifest(webui_id, "webui", entrypoints=["workspace.json"])
    for item in (suite, webui):
        folder = root / item["source"]["path"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "manifest.json").write_text(json.dumps(item), encoding="utf-8")
    page_dir = root / webui["source"]["path"] / page_id
    page_dir.mkdir(parents=True)
    (page_dir / "manifest.json").write_text(json.dumps({"id": page_id, "title": page_id}), encoding="utf-8")
    (root / "index.json").write_text(json.dumps({
        "schemaVersion": "hub.index.v1",
        "plugins": [
            {"id": item["id"], "type": item["type"], "name": item["name"], "version": "2.0.0", "trust": "official",
             "edition": "pro", "manifestPath": f"{item['source']['path']}/manifest.json"}
            for item in (suite, webui)
        ],
    }), encoding="utf-8")


# SR14
@pytest.mark.asyncio
async def test_ownership_follows_extension_roots_without_a_cache_clear(client: AsyncClient, suite_pages, tmp_path):
    from flocks.extensions import register_hub_root, unregister_hub_root

    _write_standalone_page(suite_pages.root, "code-security-workspace")
    extension = tmp_path / "extension-hub"
    _write_extension_suite(extension, "ext-audit-suite", "ext_audit_ui", "code-security-workspace")
    url = "/api/contracts/webui/pages/code-security-workspace/bundle.js"
    with registered_pro_checker() as checker:
        # Owned by no suite yet: served, and that answer is cached.
        assert (await client.get(url)).status_code == 200
        register_hub_root("sr14-extension", extension)
        try:
            # The root's suite ships this page id now; nobody cleared a cache.
            assert (await client.get(url)).status_code == 403
            checker.licensed = {"ext-audit-suite"}
            assert (await client.get(url)).status_code == 200
            checker.licensed = set()
        finally:
            unregister_hub_root("sr14-extension")
        assert (await client.get(url)).status_code == 200


# SR15
@pytest.mark.asyncio
async def test_contract_operations_follow_the_page_suite_licence(client: AsyncClient, suite_pages):
    from flocks.server.routes import contracts as contract_routes

    class Runtime:
        def __init__(self):
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs["page_id"])
            return SimpleNamespace(status_code=200, body={"ok": True})

    runtime = Runtime()
    contract_routes.reset_route_dependencies(runtime=runtime)
    try:
        with registered_pro_checker() as checker:
            url = "/api/contracts/webui/pages/{page}/access/records/operations/list"
            assert (await client.post(url.format(page="audit-page"), json={})).status_code == 403
            assert runtime.calls == []
            # A page no Pro suite owns is the runtime's to answer.
            assert (await client.post(url.format(page="ops-page"), json={})).status_code == 200
            # A contract path that is no page id (contracts may use any path)
            # names no page to license: the runtime answers, never a 503.
            assert (await client.post(url.format(page="test/records"), json={})).status_code == 200
            checker.licensed = {PRO_SUITE}
            assert (await client.post(url.format(page="audit-page"), json={})).status_code == 200
            assert runtime.calls == ["ops-page", "test/records", "audit-page"]
    finally:
        contract_routes.reset_route_dependencies()


# SR16
def test_suite_page_index_expires_like_the_page_cache(suite_pages, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(suite_license_mod.time, "monotonic", lambda: clock["now"])
    _suite_ships_its_installed_workspace(monkeypatch)
    suite_license_mod.clear_page_suite_cache()
    assert suite_license_mod.shipped_page_owners(suite_pages) == {"audit-page": PRO_SUITE}

    # An admin adds a page to the suite's installed workspace, and saving it
    # leaves a standalone copy under the same id: nothing the signature reads
    # changes, and nobody clears a cache.
    _add_page_to_suite_workspace(suite_pages, "custom-page")
    _write_standalone_page(suite_pages.root, "custom-page")

    clock["now"] += 1
    assert "custom-page" not in suite_license_mod.shipped_page_owners(suite_pages)

    clock["now"] += suite_license_mod._PAGE_SUITE_TTL_S
    assert suite_license_mod.shipped_page_owners(suite_pages)["custom-page"] == PRO_SUITE
    assert suite_license_mod.pro_suite_for_page("custom-page", suite_pages) == PRO_SUITE
    suite_license_mod.clear_page_suite_cache()


# SR17
def test_page_answers_expire_with_the_index_they_came_from(suite_pages, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(suite_license_mod.time, "monotonic", lambda: clock["now"])
    _suite_ships_its_installed_workspace(monkeypatch)
    suite_license_mod.clear_page_suite_cache()
    _write_standalone_page(suite_pages.root, "custom-page")
    assert suite_license_mod.shipped_page_owners(suite_pages) == {"audit-page": PRO_SUITE}

    # Looked up with one second of the index's time left: owned by no suite.
    clock["now"] += suite_license_mod._PAGE_SUITE_TTL_S - 1
    assert suite_license_mod.pro_suite_for_page("custom-page", suite_pages) is None

    _add_page_to_suite_workspace(suite_pages, "custom-page")

    # Two seconds on the index has expired, and so has the answer read from it.
    clock["now"] += 2
    assert suite_license_mod.pro_suite_for_page("custom-page", suite_pages) == PRO_SUITE
    suite_license_mod.clear_page_suite_cache()


# SR18
def test_page_answers_keep_their_index_read_time_across_a_rebuild(suite_pages, monkeypatch):
    ttl = suite_license_mod._PAGE_SUITE_TTL_S
    clock = {"now": 1000.0}
    monkeypatch.setattr(suite_license_mod.time, "monotonic", lambda: clock["now"])
    _suite_ships_its_installed_workspace(monkeypatch)
    suite_license_mod.clear_page_suite_cache()
    _write_standalone_page(suite_pages.root, "custom-page")
    assert suite_license_mod.shipped_page_owners(suite_pages) == {"audit-page": PRO_SUITE}  # read at 1000

    real_workspace_id_for_page = suite_pages.workspace_id_for_page
    rebuilt = []

    def another_lookup_meanwhile(page_id):
        # This lookup decides from the index read at 1000. Meanwhile another
        # one, a moment later, finds that index expired and reads it again,
        # after the page was added to the suite's workspace.
        if not rebuilt:
            _add_page_to_suite_workspace(suite_pages, "custom-page")
            saved, clock["now"] = clock["now"], 1000.0 + ttl + 0.1
            rebuilt.append(suite_license_mod.shipped_page_owners(suite_pages))
            clock["now"] = saved
        return real_workspace_id_for_page(page_id)

    monkeypatch.setattr(suite_pages, "workspace_id_for_page", another_lookup_meanwhile)
    clock["now"] = 1000.0 + ttl - 0.1
    assert suite_license_mod.pro_suite_for_page("custom-page", suite_pages) is None
    assert rebuilt and rebuilt[0]["custom-page"] == PRO_SUITE

    # That answer came from the index read at 1000, and is gone with it --
    # not kept for a full lifetime counted from the lookup.
    clock["now"] = 1000.0 + ttl + 2
    assert suite_license_mod.pro_suite_for_page("custom-page", suite_pages) == PRO_SUITE
    suite_license_mod.clear_page_suite_cache()
