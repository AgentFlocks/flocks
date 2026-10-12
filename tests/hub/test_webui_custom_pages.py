"""Updating a WebUI package keeps the pages operators added to it."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.hub import custom_pages, installer, local
from flocks.server.auth import require_admin
from flocks.server.routes.hub import router

URL = "/hub/plugins/webui/soc_ui/update"


@pytest.fixture
async def client(isolated_hub_env, monkeypatch):
    async def refresh(*args):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", refresh)
    monkeypatch.setattr(installer, "_build_webui_pages", lambda *args: None)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def add_page(root: Path, directory: str, page_id: str, title: str = "威胁看板") -> Path:
    page = root / directory
    (page / "src").mkdir(parents=True)
    (page / "api").mkdir()
    (page / "dist").mkdir()
    (page / "manifest.json").write_text(
        json.dumps({"id": page_id, "title": title, "route": f"/contracts/webui/{page_id}"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (page / "src/index.tsx").write_text("export default function Page() { return null; }\n")
    (page / "api/routes.yaml").write_text("routes: []\n")
    (page / "dist/page.js").write_text("built")
    return page


def workspace(root: Path) -> dict:
    return json.loads((root / "workspace.json").read_text(encoding="utf-8"))


def save_workspace(root: Path, data: dict) -> None:
    (root / "workspace.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def section(data: dict, section_id: str) -> dict:
    return next(item for item in data["sections"] if item["id"] == section_id)


# The bundled soc_ui ships three official operations pages: overview, alerts, cases.
async def installed_soc_ui() -> Path:
    record = await installer.install_plugin("webui", "soc_ui")
    return Path(record.installPath)


def mark_outdated() -> None:
    record = local.get_record("webui", "soc_ui")
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))


async def confirmed_update(client, url: str = URL):
    preview = (await client.post(url + "/preview")).json()
    response = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 200, response.text
    return preview, response


async def test_update_keeps_custom_page_and_its_section_entry(client):
    root = await installed_soc_ui()
    page = add_page(root, "my_threats", "my-threats")
    data = workspace(root)
    section(data, "operations")["pageIds"].append("my-threats")
    save_workspace(root, data)
    mark_outdated()

    await confirmed_update(client)

    assert (page / "src/index.tsx").read_text().startswith("export default")
    assert (page / "api/routes.yaml").is_file()
    assert (page / "dist/page.js").read_text() == "built"
    assert section(workspace(root), "operations")["pageIds"] == ["soc-overview", "soc-alerts", "soc-cases", "my-threats"]


async def test_preview_lists_kept_pages_and_backup_still_has_them(client):
    root = await installed_soc_ui()
    add_page(root, "my_threats", "my-threats")
    mark_outdated()

    preview, response = await confirmed_update(client)

    item = preview["items"][0]
    assert item["keepPages"] == [{"id": "my-threats", "title": "威胁看板", "path": "my_threats"}]
    assert item["replacePages"] == []
    backup = Path(response.json()["backupPath"])
    assert (backup / "0/package/my_threats/src/index.tsx").is_file()


async def test_pages_survive_repeated_updates_and_record_holds_only_release_files(client):
    root = await installed_soc_ui()
    add_page(root, "my_threats", "my-threats")
    for _ in range(2):
        mark_outdated()
        preview, _ = await confirmed_update(client)
        assert [page["id"] for page in preview["items"][0]["keepPages"]] == ["my-threats"]
        assert (root / "my_threats/src/index.tsx").is_file()
    baseline = local.get_record("webui", "soc_ui").fileHashes
    assert "package/soc_dashboard/manifest.json" in baseline
    assert not any(key.startswith("package/my_threats/") for key in baseline)


async def test_custom_section_keeps_its_place_and_official_layout_is_restored(client):
    root = await installed_soc_ui()
    add_page(root, "my_threats", "my-threats")
    add_page(root, "my_assets", "my-assets", title="资产")
    data = workspace(root)
    # The admin also moved an official page into the new section and made a
    # custom page the default; only the custom parts carry over.
    section(data, "operations")["pageIds"] = ["soc-overview", "my-assets", "soc-alerts"]
    section(data, "operations")["defaultPageId"] = "my-assets"
    data["sections"].insert(1, {"id": "hunting", "label": "狩猎", "pageIds": ["my-threats", "soc-dashboard"]})
    section(data, "posture")["pageIds"] = []
    data["defaultPageId"] = "my-threats"
    save_workspace(root, data)
    mark_outdated()

    await confirmed_update(client)

    merged = workspace(root)
    assert [item["id"] for item in merged["sections"]] == ["posture", "hunting", "operations"]
    assert section(merged, "posture")["pageIds"] == ["soc-dashboard"]
    assert section(merged, "hunting") == {
        "id": "hunting", "label": "狩猎", "pageIds": ["my-threats"], "defaultPageId": None,
    }
    assert section(merged, "operations")["pageIds"] == ["soc-overview", "my-assets", "soc-alerts", "soc-cases"]
    assert section(merged, "operations")["defaultPageId"] == "my-assets"
    assert merged["defaultPageId"] == "my-threats"


async def test_edits_to_official_pages_are_still_replaced_and_backed_up(client):
    root = await installed_soc_ui()
    official = (root / "soc_dashboard/src/Page.tsx").read_text(encoding="utf-8")
    (root / "soc_dashboard/src/Page.tsx").write_text("// edited", encoding="utf-8")
    (root / "soc_dashboard/src/Extra.tsx").write_text("// added beside an official page", encoding="utf-8")
    add_page(root, "my_threats", "my-threats")
    mark_outdated()

    _, response = await confirmed_update(client)

    assert (root / "soc_dashboard/src/Page.tsx").read_text(encoding="utf-8") == official
    assert not (root / "soc_dashboard/src/Extra.tsx").exists()
    assert (root / "my_threats/src/index.tsx").is_file()
    backup = Path(response.json()["backupPath"])
    assert (backup / "0/package/soc_dashboard/src/Page.tsx").read_text(encoding="utf-8") == "// edited"


async def test_page_reusing_an_official_id_is_replaced_not_kept(client):
    root = await installed_soc_ui()
    add_page(root, "my_dashboard", "soc-dashboard", title="我的态势")
    mark_outdated()

    preview, response = await confirmed_update(client)

    item = preview["items"][0]
    assert item["keepPages"] == []
    assert item["replacePages"] == [{"id": "soc-dashboard", "title": "我的态势", "path": "my_dashboard"}]
    assert not (root / "my_dashboard").exists()
    assert (Path(response.json()["backupPath"]) / "0/package/my_dashboard/manifest.json").is_file()


@pytest.mark.parametrize("recorded", [True, False])
async def test_page_shipped_by_the_installed_release_is_not_carried(client, recorded):
    root = await installed_soc_ui()
    add_page(root, "soc_retired", "soc-retired")
    data = workspace(root)
    section(data, "operations")["pageIds"].append("soc-retired")
    save_workspace(root, data)
    record = local.get_record("webui", "soc_ui")
    baseline = {**record.fileHashes, "package/soc_retired/manifest.json": "sha"} if recorded else None
    local.save_installed_record(record.model_copy(update={"version": "0.0.1", "fileHashes": baseline}))

    preview, _ = await confirmed_update(client)

    kept = [page["id"] for page in preview["items"][0]["keepPages"]]
    # Without a record of what the installed release shipped (installs made
    # before it was kept), any page the new release lacks counts as added.
    assert kept == ([] if recorded else ["soc-retired"])
    assert (root / "soc_retired").exists() is not recorded
    assert ("soc-retired" in section(workspace(root), "operations")["pageIds"]) is not recorded


async def test_suite_update_keeps_custom_pages(client):
    await installer.install_plugin("component", "soc-workspace")
    root = Path(local.get_record("webui", "soc_ui").installPath)
    add_page(root, "my_threats", "my-threats")
    data = workspace(root)
    section(data, "posture")["pageIds"].append("my-threats")
    save_workspace(root, data)
    mark_outdated()

    preview, _ = await confirmed_update(client, "/hub/plugins/component/soc-workspace/update")

    item = next(item for item in preview["items"] if item["id"] == "soc_ui")
    assert [page["id"] for page in item["keepPages"]] == ["my-threats"]
    assert (root / "my_threats/src/index.tsx").is_file()
    assert section(workspace(root), "posture")["pageIds"] == ["soc-dashboard", "my-threats"]


async def test_unreadable_workspace_manifest_keeps_pages_without_nav_entries(client):
    root = await installed_soc_ui()
    add_page(root, "my_threats", "my-threats")
    (root / "workspace.json").write_text("{broken", encoding="utf-8")
    mark_outdated()

    await confirmed_update(client)

    assert (root / "my_threats/src/index.tsx").is_file()
    assert "my-threats" not in json.dumps(workspace(root))


@pytest.mark.parametrize("stage", ["build", "access"])
async def test_failed_update_leaves_custom_pages_in_place(client, monkeypatch, stage):
    root = await installed_soc_ui()
    add_page(root, "my_threats", "my-threats")
    data = workspace(root)
    section(data, "operations")["pageIds"].append("my-threats")
    save_workspace(root, data)
    before = (root / "workspace.json").read_text(encoding="utf-8")
    mark_outdated()

    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(installer, "_build_webui_pages" if stage == "build" else "_copy_attached_access_contracts", fail)
    preview = (await client.post(URL + "/preview")).json()
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})

    assert response.status_code == 422
    assert (root / "my_threats/src/index.tsx").is_file()
    assert (root / "workspace.json").read_text(encoding="utf-8") == before


OFFICIAL_WORKSPACE = {
    "id": "soc_ui",
    "title": "SOC 工作区",
    "sections": [
        {"id": "posture", "label": "态势", "pageIds": ["soc-dashboard"]},
        {"id": "operations", "label": "告警运营", "pageIds": ["soc-overview", "soc-alerts"]},
    ],
}


def test_merge_places_a_page_once_and_at_the_front_when_it_led_its_section():
    installed = {
        "sections": [
            {"id": "operations", "label": "告警运营", "pageIds": ["mine", "soc-overview", "mine", "soc-alerts"]},
            {"id": "posture", "label": "态势", "pageIds": ["soc-dashboard", "mine"]},
        ],
    }
    merged = custom_pages.merge_workspace(OFFICIAL_WORKSPACE, installed, ["mine"])
    assert section(merged, "operations")["pageIds"] == ["mine", "soc-overview", "soc-alerts"]
    assert section(merged, "posture")["pageIds"] == ["soc-dashboard"]
    assert OFFICIAL_WORKSPACE["sections"][1]["pageIds"] == ["soc-overview", "soc-alerts"]


def test_merge_matches_ids_the_way_the_page_store_reads_them():
    installed = {
        "defaultPageId": " Mine ",
        "sections": [{"id": " Operations", "label": "告警运营", "pageIds": ["soc-overview", " MINE"], "defaultPageId": "MINE"}],
    }
    merged = custom_pages.merge_workspace(OFFICIAL_WORKSPACE, installed, ["mine"])
    assert [item["id"] for item in merged["sections"]] == ["posture", "operations"]
    assert section(merged, "operations")["pageIds"] == ["soc-overview", "mine", "soc-alerts"]
    assert section(merged, "operations")["defaultPageId"] == "mine"
    assert merged["defaultPageId"] == "mine"


def test_merge_keeps_a_default_page_the_section_does_not_list():
    installed = {
        "defaultPageId": "mine",
        "sections": [{"id": "operations", "label": "告警运营", "pageIds": ["soc-overview"], "defaultPageId": "mine"}],
    }
    merged = custom_pages.merge_workspace(OFFICIAL_WORKSPACE, installed, ["mine"])
    assert section(merged, "operations")["pageIds"] == ["mine", "soc-overview", "soc-alerts"]
    assert section(merged, "operations")["defaultPageId"] == "mine"
    assert merged["defaultPageId"] == "mine"


def test_merge_without_official_sections_keeps_a_custom_workspace_default():
    official = {"id": "audit", "title": "代码审计", "defaultPageId": "audit-home"}
    installed = {"defaultPageId": "mine", "sections": [{"id": "custom", "label": "自定义", "pageIds": ["mine"]}]}
    assert custom_pages.merge_workspace(official, installed, ["mine"]) == {**official, "defaultPageId": "mine"}
    assert custom_pages.merge_workspace(official, {"defaultPageId": "audit-home"}, ["mine"]) is official


def test_merge_skips_malformed_sections_without_failing():
    installed = {
        "sections": [
            "not a section",
            {"id": "operations", "pageIds": 123},
            {"id": None, "label": "无效", "pageIds": ["mine"]},
            {"id": "posture", "label": "态势", "pageIds": ["soc-dashboard", "mine"]},
        ],
    }
    merged = custom_pages.merge_workspace(OFFICIAL_WORKSPACE, installed, ["mine"])
    assert section(merged, "posture")["pageIds"] == ["soc-dashboard", "mine"]
    assert section(merged, "operations")["pageIds"] == ["soc-overview", "soc-alerts"]


@pytest.mark.parametrize(
    "bad_section",
    [
        {"id": "Bad Id!", "label": "坏栏目", "pageIds": ["mine"]},
        {"id": "custom", "pageIds": ["mine"]},
        {"id": "custom", "label": "坏栏目", "pageIds": ["mine"], "contentPadding": "wide"},
    ],
)
def test_merge_skips_sections_the_workspace_would_reject(bad_section):
    merged = custom_pages.merge_workspace(OFFICIAL_WORKSPACE, {"sections": [bad_section]}, ["mine"])
    assert merged == OFFICIAL_WORKSPACE


@pytest.mark.parametrize("installed", [None, [], {"sections": "oops"}, {"sections": []}])
def test_merge_without_usable_installed_sections_keeps_the_release(installed):
    assert custom_pages.merge_workspace(OFFICIAL_WORKSPACE, installed, ["mine"]) == OFFICIAL_WORKSPACE


def test_merge_leaves_a_workspace_without_sections_alone():
    official = {"id": "audit", "title": "代码审计"}
    installed = {"sections": [{"id": "custom", "label": "自定义", "pageIds": ["mine"]}]}
    assert custom_pages.merge_workspace(official, installed, ["mine"]) is official


def write_page(root: Path, path: str, page_id: str) -> None:
    page = root / path
    page.mkdir(parents=True, exist_ok=True)
    (page / "manifest.json").write_text(json.dumps({"id": page_id, "title": page_id}), encoding="utf-8")


def test_classify_lists_nested_pages_and_skips_hidden_generated_and_symlinked_dirs(tmp_path):
    installed, official = tmp_path / "installed", tmp_path / "official"
    write_page(official, "soc_dashboard", "soc-dashboard")
    write_page(installed, "soc_dashboard", "soc-dashboard")
    write_page(installed, "soc_dashboard/inner", "inner")
    write_page(installed, "custom/area", "area")
    write_page(installed, "custom/area/sub", "sub")
    write_page(installed, ".soc_ui.bak/page", "stale")
    write_page(installed, "custom/area/dist/page", "built")
    write_page(tmp_path / "elsewhere", "page", "linked")
    (installed / "linked").symlink_to(tmp_path / "elsewhere/page", target_is_directory=True)
    (installed / "manifest.json").write_text(json.dumps({"id": "soc-ui"}), encoding="utf-8")

    keep, replace = custom_pages.classify_pages(installed, official)

    assert keep == [
        {"id": "area", "title": "area", "path": "custom/area"},
        {"id": "sub", "title": "sub", "path": "custom/area/sub"},
    ]
    # Added inside an official page, which the release replaces as a whole.
    assert replace == [{"id": "inner", "title": "inner", "path": "soc_dashboard/inner"}]


def test_classify_replaces_pages_where_the_release_ships_files(tmp_path):
    installed, official = tmp_path / "installed", tmp_path / "official"
    write_page(installed, "shared/page", "shared-page")
    write_page(installed, "assets", "assets-page")
    official.mkdir()
    (official / "shared").write_text("a file now", encoding="utf-8")
    (official / "assets").mkdir()

    keep, replace = custom_pages.classify_pages(installed, official)

    assert keep == []
    assert [page["path"] for page in replace] == ["assets", "shared/page"]


def test_carry_over_rejects_paths_outside_the_package(tmp_path):
    with pytest.raises(ValueError):
        custom_pages.carry_over(tmp_path, tmp_path / "staged", [{"id": "x", "path": "../x"}])


async def test_nested_pages_keep_their_entries_and_clashing_ones_stay_out(client):
    root = await installed_soc_ui()
    add_page(root, "custom_area", "area", title="自建区")
    add_page(root, "custom_area/sub", "sub", title="子页")
    add_page(root, "custom_area/dash", "soc-dashboard", title="同名子页")
    add_page(root, "soc_dashboard/extra", "dash-extra", title="塞进官方页的页面")
    data = workspace(root)
    section(data, "operations")["pageIds"] += ["area", "sub"]
    save_workspace(root, data)
    mark_outdated()

    preview, _ = await confirmed_update(client)

    item = preview["items"][0]
    assert [page["path"] for page in item["keepPages"]] == ["custom_area", "custom_area/sub"]
    assert [page["path"] for page in item["replacePages"]] == ["custom_area/dash", "soc_dashboard/extra"]
    assert (root / "custom_area/sub/src/index.tsx").is_file()
    assert not (root / "custom_area/dash").exists()
    assert not (root / "soc_dashboard/extra").exists()
    assert section(workspace(root), "operations")["pageIds"] == ["soc-overview", "soc-alerts", "soc-cases", "area", "sub"]


async def test_bundle_copied_mid_build_is_rebuilt_and_a_finished_one_is_kept(client):
    root = await installed_soc_ui()
    finished = add_page(root, "my_threats", "my-threats")
    half_built = add_page(root, "my_assets", "my-assets")
    digest = hashlib.sha256(b"built").hexdigest()[:16]
    for page, recorded in ((finished, digest), (half_built, "0123456789abcdef")):
        (page / "dist/meta.json").write_text(json.dumps({"status": "ready", "hash": recorded}), encoding="utf-8")
    mark_outdated()

    await confirmed_update(client)

    assert json.loads((finished / "dist/meta.json").read_text())["status"] == "ready"
    assert json.loads((half_built / "dist/meta.json").read_text())["status"] == "idle"
    assert (half_built / "dist/page.js").read_text() == "built"


async def test_kept_page_below_a_replaced_one_is_copied_on_its_own(client):
    root = await installed_soc_ui()
    add_page(root, "area", "area")
    add_page(root, "area/clash", "soc-dashboard", title="同名中间页")
    add_page(root, "area/clash/leaf", "leaf")
    add_page(root, "area/clash/leaf/deeper", "deeper")
    data = workspace(root)
    section(data, "operations")["pageIds"] += ["area", "leaf", "deeper"]
    save_workspace(root, data)
    mark_outdated()

    preview, _ = await confirmed_update(client)

    item = preview["items"][0]
    assert [page["path"] for page in item["keepPages"]] == ["area", "area/clash/leaf", "area/clash/leaf/deeper"]
    assert [page["path"] for page in item["replacePages"]] == ["area/clash"]
    assert (root / "area/src/index.tsx").is_file()
    assert not (root / "area/clash/manifest.json").exists()
    assert not (root / "area/clash/src").exists()
    assert (root / "area/clash/leaf/src/index.tsx").is_file()
    assert (root / "area/clash/leaf/deeper/src/index.tsx").is_file()
    assert section(workspace(root), "operations")["pageIds"] == ["soc-overview", "soc-alerts", "soc-cases", "area", "leaf", "deeper"]


@pytest.mark.parametrize("linked", ["dist", "meta"])
async def test_stale_build_check_never_writes_through_symlinks(client, tmp_path, linked):
    root = await installed_soc_ui()
    page = add_page(root, "my_threats", "my-threats")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.js").write_text("elsewhere")
    stale = json.dumps({"status": "ready", "hash": "0123456789abcdef"})
    (outside / "meta.json").write_text(stale, encoding="utf-8")
    if linked == "dist":
        for item in (page / "dist").iterdir():
            item.unlink()
        (page / "dist").rmdir()
        (page / "dist").symlink_to(outside, target_is_directory=True)
    else:
        (page / "dist/meta.json").symlink_to(outside / "meta.json")
    mark_outdated()

    await confirmed_update(client)

    assert (outside / "meta.json").read_text(encoding="utf-8") == stale
    assert (page / ("dist" if linked == "dist" else "dist/meta.json")).is_symlink()
