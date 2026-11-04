"""An ordinary extension's hub access policy (flocks.hub.access).

Nothing here is a commercial build: an extension called "acme" lists a scene
suite of an edition it names "team" from its own hub root and registers a
policy through ``flocks.extensions``.  The open product asks the policy and
does what it says -- the edition's name, and why the policy decides as it
does, mean nothing to it.

Case list (编号 | 场景):
  AP1 without a policy the suite is listed as it is and nothing is refused
  AP2 the policy's refusal stops an install (403, its own words) before
      anything is installed
  AP3 the suite listing carries what the policy says: locked, its reason and
      its annotations
  AP4 a page the policy refuses is refused on every route that serves it,
      with the policy's status and words; a page it does not refuse is served
  AP5 the page and workspace listings are narrowed by the policy: a locked
      workspace keeps its reason and leaves the navigation
  AP6 with several policies the first refusal wins and listings are narrowed
      by all of them
  AP7 a policy told of content changes that fails does not stop the others
  AP8 a refused page is neither served nor changed on the routes that write or
      hand out its files (assets, save, build, API reload, export, import over
      it) nor on their pre-contract aliases: the policy's status, and the
      page's files as they were
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from flocks.contracts.webui.builder import WebUIPageBuilder
from flocks.contracts.webui.store import WebUIPagesStore
from flocks.extensions import (
    register_hub_access_policy,
    register_hub_root,
    unregister_hub_access_policy,
    unregister_hub_root,
)
from flocks.hub import access as hub_access
from flocks.hub.catalog import clear_catalog_caches
from flocks.server.routes import webui as webui_routes

SUITE = "acme-suite"
WORKSPACE = "acme_ui"
PAGE = "acme-page"


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
    try:
        yield root
    finally:
        unregister_hub_root("acme")
        clear_catalog_caches()


@pytest.fixture
def pages(tmp_path, monkeypatch):
    """The acme scene's workspace with one page, and a standalone page of nobody's."""
    root = tmp_path / "webui_pages"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    store = WebUIPagesStore()
    webui_routes.reset_route_dependencies(store=store, builder=WebUIPageBuilder(store))
    for workspace_id, page_id in ((WORKSPACE, PAGE), (None, "own-page")):
        page_dir = (store.root / workspace_id / page_id) if workspace_id else (store.root / page_id)
        for sub in ("src", "dist", "api"):
            (page_dir / sub).mkdir(parents=True, exist_ok=True)
        (page_dir / "manifest.json").write_text(json.dumps({
            "id": page_id, "title": page_id, "route": f"/contracts/webui/{page_id}", "icon": "LayoutDashboard",
            "order": 10, "enabled": True, "placement": "home.after", "entry": "src/index.tsx", "updatedAt": 0,
        }), encoding="utf-8")
        (page_dir / "dist" / "page.js").write_text("export default function Page(){return null;}", encoding="utf-8")
        (page_dir / "api" / "routes.yaml").write_text(
            "routes:\n  - method: GET\n    path: /stats\n    handler: handlers.stats\n", encoding="utf-8",
        )
        (page_dir / "api" / "handlers.py").write_text("def stats(ctx, request):\n    return {'ok': True}\n", encoding="utf-8")
    (store.root / WORKSPACE / "workspace.json").write_text(json.dumps({
        "id": WORKSPACE, "title": WORKSPACE, "icon": "Layers", "order": 10, "enabled": True,
        "placement": "sceneWorkspace", "defaultPageId": PAGE,
        "sections": [{"id": "main", "label": "main", "pageIds": [PAGE], "defaultPageId": PAGE}],
    }), encoding="utf-8")
    monkeypatch.setattr(webui_routes, "scene_suite_workspace_owners", lambda: {WORKSPACE: SUITE})
    yield store
    webui_routes.reset_route_dependencies()


class AcmePolicy:
    """The extension's policy: its team edition is not granted on this install."""

    def __init__(self) -> None:
        self.granted = False
        self.changed = 0
        self.asked_pages: list[str] = []
        self.seen_store = None

    async def install_refusal(self, plugin_type, plugin_id):
        if not self.granted and (plugin_type, plugin_id) in {("component", SUITE), ("webui", WORKSPACE)}:
            return "acme: the team edition is not granted here"
        return None

    async def decorate_scene_suites(self, suites):
        return [
            suite.model_copy(update={
                "locked": not self.granted, "lockReason": None if self.granted else "team",
                "annotations": {**suite.annotations, "acme": {"edition": "team"}},
            }) if suite.edition == "team" else suite
            for suite in suites
        ]

    async def page_refusal(self, page_id, store):
        self.asked_pages.append(page_id)
        self.seen_store = store
        if page_id == PAGE and not self.granted:
            return (402, "acme: this page needs the team edition")
        return None

    async def hidden_pages(self, pages, store):
        return set() if self.granted else {PAGE}

    async def locked_workspaces(self, workspaces):
        return {} if self.granted else {w.id: "team" for w in workspaces if w.suiteId == SUITE}

    def contents_changed(self):
        self.changed += 1


@pytest.fixture
def acme_policy():
    policy = AcmePolicy()
    register_hub_access_policy(policy)
    try:
        yield policy
    finally:
        unregister_hub_access_policy(policy)


async def _suites(client: AsyncClient) -> dict[str, dict]:
    resp = await client.get("/api/hub/scene-suites")
    assert resp.status_code == 200, resp.text
    return {item["id"]: item for item in resp.json()}


PAGE_REQUESTS = [
    ("GET", f"/api/contracts/webui/pages/{PAGE}/bundle.js"),
    ("GET", f"/api/contracts/webui/pages/{PAGE}"),
    ("GET", f"/api/contracts/webui/pages/{PAGE}/api"),
    ("GET", f"/api/contracts/webui/pages/{PAGE}/api/stats"),
]


# AP1
@pytest.mark.asyncio
async def test_without_a_policy_nothing_is_decided(client, acme_root, pages):
    suites = await _suites(client)
    assert suites[SUITE]["edition"] == "team"
    assert (suites[SUITE]["locked"], suites[SUITE]["lockReason"], suites[SUITE]["annotations"]) == (None, None, {})
    assert await hub_access.install_refusal("component", SUITE) is None
    for method, url in PAGE_REQUESTS:
        assert (await client.request(method, url)).status_code == 200, url
    nav = await client.get("/api/contracts/webui/workspaces", params={"enabledOnly": True})
    assert [(item["id"], item["locked"], item["lockReason"]) for item in nav.json()] == [(WORKSPACE, False, None)]


# AP2
@pytest.mark.asyncio
async def test_the_policy_refuses_an_install_in_its_own_words(client, acme_root, acme_policy):
    resp = await client.post(f"/api/hub/plugins/component/{SUITE}/install", json={})
    assert resp.status_code == 403
    assert "team edition is not granted" in resp.text
    from flocks.hub import local

    assert local.get_record("component", SUITE) is None


# AP3
@pytest.mark.asyncio
async def test_the_suite_listing_says_what_the_policy_says(client, acme_root, acme_policy):
    suites = await _suites(client)
    assert (suites[SUITE]["locked"], suites[SUITE]["lockReason"]) == (True, "team")
    assert suites[SUITE]["annotations"] == {"acme": {"edition": "team"}}
    assert suites["soc-workspace"]["locked"] is None

    acme_policy.granted = True
    suites = await _suites(client)
    assert (suites[SUITE]["locked"], suites[SUITE]["lockReason"]) == (False, None)


# AP4
@pytest.mark.asyncio
async def test_a_refused_page_is_refused_everywhere_it_is_served(client, acme_policy, pages):
    for method, url in PAGE_REQUESTS:
        resp = await client.request(method, url)
        assert resp.status_code == 402, (url, resp.status_code, resp.text)
        assert "needs the team edition" in resp.text
    assert acme_policy.seen_store is webui_routes._store
    assert (await client.get("/api/contracts/webui/pages/own-page/bundle.js")).status_code == 200

    acme_policy.granted = True
    for method, url in PAGE_REQUESTS:
        assert (await client.request(method, url)).status_code == 200, url


@pytest.mark.asyncio
async def test_a_page_data_contract_follows_the_page(client, acme_policy, pages):
    resp = await client.post(f"/api/contracts/webui/pages/{PAGE}/access/c/operations/op", json={})
    assert resp.status_code == 402, resp.text
    acme_policy.asked_pages.clear()
    # A contract path that names no page is left to the contract runtime.
    await client.post("/api/contracts/webui/pages/test/records/access/c/operations/op", json={})
    assert acme_policy.asked_pages == []


# AP5
@pytest.mark.asyncio
async def test_the_listings_are_narrowed_by_the_policy(client, acme_policy, pages):
    listed = await client.get("/api/contracts/webui/pages")
    assert [item["id"] for item in listed.json()] == ["own-page"]

    full = await client.get("/api/contracts/webui/workspaces")
    assert [(item["id"], item["locked"], item["lockReason"]) for item in full.json()] == [(WORKSPACE, True, "team")]
    nav = await client.get("/api/contracts/webui/workspaces", params={"enabledOnly": True})
    assert nav.json() == []

    acme_policy.granted = True
    listed = await client.get("/api/contracts/webui/pages")
    assert sorted(item["id"] for item in listed.json()) == [PAGE, "own-page"]
    nav = await client.get("/api/contracts/webui/workspaces", params={"enabledOnly": True})
    assert [(item["id"], item["locked"]) for item in nav.json()] == [(WORKSPACE, False)]


# AP6
@pytest.mark.asyncio
async def test_several_policies_narrow_together(acme_policy, pages):
    class Second:
        async def install_refusal(self, plugin_type, plugin_id):
            return "second: no"

        async def page_refusal(self, page_id, store):
            return (403, "second: no")

        async def hidden_pages(self, pages, store):
            return {"own-page"}

    second = Second()
    register_hub_access_policy(second)
    try:
        assert await hub_access.install_refusal("component", SUITE) == "acme: the team edition is not granted here"
        assert await hub_access.install_refusal("skill", "anything") == "second: no"
        assert await hub_access.page_refusal(PAGE, pages) == (402, "acme: this page needs the team edition")
        assert await hub_access.hidden_pages([], pages) == {PAGE, "own-page"}
        # A method a policy does not implement is simply not asked.
        assert await hub_access.locked_workspaces([]) == {}
    finally:
        unregister_hub_access_policy(second)


# AP7
def test_content_changes_reach_every_policy(acme_policy):
    class Failing:
        def contents_changed(self):
            raise RuntimeError("cache gone")

    failing = Failing()
    register_hub_access_policy(failing)
    try:
        hub_access.contents_changed()
    finally:
        unregister_hub_access_policy(failing)
    assert acme_policy.changed == 1


def _page_files(store: WebUIPagesStore) -> dict[str, bytes]:
    """The refused page's files, and anything under its id at the root, by path."""
    found: dict[str, bytes] = {}
    for base in (store.root / WORKSPACE / PAGE, store.root / PAGE):
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    found[path.relative_to(store.root).as_posix()] = path.read_bytes()
    return found


def _archive_of(page_id: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{page_id}/manifest.json", json.dumps({
            "id": page_id, "title": "replaced", "route": f"/contracts/webui/{page_id}", "icon": "LayoutDashboard",
            "order": 10, "enabled": True, "placement": "home.after", "entry": "src/index.tsx", "updatedAt": 0,
        }))
        archive.writestr(f"{page_id}/src/index.tsx", "export default 2;\n")
        archive.writestr(f"{page_id}/api/handlers.py", "def stats(ctx, request):\n    return {'replaced': True}\n")
    return buffer.getvalue()


# AP8
@pytest.mark.asyncio
async def test_a_refused_page_is_neither_served_nor_changed(client, acme_policy, pages):
    page_dir = pages.root / WORKSPACE / PAGE
    (page_dir / "src" / "index.tsx").write_text("export default 1;\n", encoding="utf-8")
    (page_dir / "assets").mkdir()
    (page_dir / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    before = _page_files(pages)

    edit = {"sourcePath": "src/index.tsx", "sourceContent": "changed"}
    requests = []
    for prefix in (f"/api/contracts/webui/pages/{PAGE}", f"/api/user-defined-pages/{PAGE}"):
        requests += [
            ("GET", f"{prefix}", {}),
            ("GET", f"{prefix}/bundle.js", {}),
            ("GET", f"{prefix}/assets/logo.svg", {}),
            ("GET", f"{prefix}/api/stats", {}),
            ("PUT", f"{prefix}", {"json": edit}),
            ("PUT", f"{prefix}", {"json": {"sourcePath": "api/handlers.py", "sourceContent": "changed"}}),
            ("POST", f"{prefix}/build", {}),
            ("POST", f"{prefix}/api/reload", {}),
            ("GET", f"{prefix}/export", {}),
        ]
    for method, url, kwargs in requests:
        resp = await client.request(method, url, **kwargs)
        assert resp.status_code == 402, (method, url, resp.status_code, resp.text)
        assert "needs the team edition" in resp.text

    for url in ("/api/contracts/webui/pages/import", "/api/user-defined-pages/import"):
        resp = await client.post(
            url, params={"overwrite": "true"},
            files={"file": (f"{PAGE}.zip", _archive_of(PAGE), "application/zip")},
        )
        assert resp.status_code == 402, (url, resp.status_code, resp.text)

    assert _page_files(pages) == before
    assert acme_policy.changed == 0

    acme_policy.granted = True
    resp = await client.get(f"/api/user-defined-pages/{PAGE}/export")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    assert (await client.get(f"/api/user-defined-pages/{PAGE}/assets/logo.svg")).text == "<svg/>"
