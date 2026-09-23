"""Exercise real update endpoints against an isolated home and official packages."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.hub import installer, local
from flocks.hub.update_protection import tree_hashes
from flocks.server.auth import require_admin
from flocks.server.routes.hub import router


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


TARGET = "workflow/stream_alert_triage"
URL = f"/hub/plugins/{TARGET}/update"


async def installed_workflow():
    record = await installer.install_plugin("workflow", "stream_alert_triage")
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    return Path(record.installPath)


def backup_manifest(response):
    path = Path(response.json()["backupPath"])
    assert path.is_dir()
    return path, json.loads((path / "backup.json").read_text())["plan"]


@pytest.mark.parametrize(
    "kind,identifier",
    [
        ("workflow", "stream_alert_triage"),
        ("workflow", "stream_alert_denoise"),
        ("tool", "soc_workspace_query"),
        ("webui", "soc_ui"),
        ("component", "soc-workspace"),
    ],
)
async def test_clean_soc_replacement_always_confirms_and_backs_up(client, kind, identifier):
    record = await installer.install_plugin(kind, identifier)
    root = Path(record.installPath)
    before = tree_hashes(root)
    url = f"/hub/plugins/{kind}/{identifier}/update"
    preview = (await client.post(url + "/preview")).json()
    assert preview["requiresConfirmation"] is True
    assert (await client.post(url)).status_code == 409
    assert (await client.post(url.replace("/update", "/install"))).status_code == 409
    for _ in range(2):
        preview = (await client.post(url + "/preview")).json()
        assert preview["requiresConfirmation"]
        result = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
        assert result.status_code == 200, result.text
        backup, _ = backup_manifest(result)
        assert tree_hashes(backup / "0/package") == before
        assert tree_hashes(root) == before


async def test_first_install_without_existing_content_does_not_prompt(client):
    preview = (await client.post(URL + "/preview")).json()
    assert not preview["requiresConfirmation"]
    response = await client.post(URL.replace("/update", "/install"))
    assert response.status_code == 200
    assert response.json()["backupPath"] is None


@pytest.mark.parametrize("mode", ["child", "suite"])
async def test_edits_require_confirmation_then_backup_and_overwrite(client, mode):
    await installer.install_plugin("component", "soc-workspace")
    record = local.get_record("workflow", "stream_alert_triage")
    root = Path(record.installPath)
    original = (root / "workflow.json").read_bytes()
    (root / "workflow.json").write_text('{"custom": true}')
    (root / "user-notes.txt").write_text("keep me")
    deleted = next(p for p in root.rglob("*") if p.is_file() and p.name not in {"workflow.json", "user-notes.txt"})
    deleted.unlink()
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    before = local._record_path().read_bytes()
    url = URL if mode == "child" else "/hub/plugins/component/soc-workspace/update"
    preview = (await client.post(url + "/preview")).json()
    assert preview["requiresConfirmation"] is True
    item = next(i for i in preview["items"] if i["id"] == record.id)
    assert not any(k.startswith("_") for k in item)
    for body in ({}, {"confirmChanges": True}, {"confirmationToken": preview["token"]}):
        denied = await client.post(url, json=body)
        assert denied.status_code == 409
        assert local._record_path().read_bytes() == before
        assert (root / "workflow.json").read_text() == '{"custom": true}'
    # The install endpoint cannot bypass the guard either.
    assert (await client.post(url.replace("/update", "/install"))).status_code == 409
    result = await client.post(url, json={"confirmChanges": True, "confirmationToken": preview["token"]})
    assert result.status_code == 200, result.text
    backup, plan = backup_manifest(result)
    index = next(n for n, i in enumerate(plan["items"]) if i["id"] == record.id)
    assert (backup / str(index) / "package/workflow.json").read_text() == '{"custom": true}'
    assert (backup / str(index) / "package/user-notes.txt").read_text() == "keep me"
    assert plan["items"][index]["_record"]["version"] == "0.0.1"
    assert (root / "workflow.json").read_bytes() == original
    assert not (root / "user-notes.txt").exists()
    assert deleted.exists()
    assert (await client.post(url + "/preview")).json()["requiresConfirmation"]


async def test_webui_access_is_protected_and_generated_files_ignored(client):
    record = await installer.install_plugin("webui", "soc_ui")
    root = Path(record.installPath)
    dist = root / "soc_overview/dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "page.js").write_text("generated")
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "cache.pyc").write_bytes(b"cache")
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    url = "/hub/plugins/webui/soc_ui/update"
    assert (await client.post(url + "/preview")).json()["requiresConfirmation"]
    access = local.install_root("webui").parent / "access/soc_ui"
    (access / "user-contract.json").write_text('{"custom": true}')
    preview = (await client.post(url + "/preview")).json()
    result = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert result.status_code == 200, result.text
    backup, _ = backup_manifest(result)
    assert (backup / "0/access/user-contract.json").exists()
    assert (backup / "0/package/soc_overview/dist/page.js").read_text() == "generated"
    assert not (access / "user-contract.json").exists()


@pytest.mark.parametrize("baseline", [None, {}, {"package/custom.txt": "pretend-official-hash"}])
async def test_recorded_baseline_cannot_waive_confirmation(client, baseline):
    record = await installer.install_plugin("workflow", "stream_alert_triage")
    local.save_installed_record(record.model_copy(update={"fileHashes": baseline}))
    plan = (await client.post(URL + "/preview")).json()
    assert plan["requiresConfirmation"]
    assert "baselineKnown" not in plan["items"][0]
    assert "changes" not in plan["items"][0]
    assert (await client.post(URL)).status_code == 409


async def test_stale_confirmation_cannot_overwrite_new_edits(client, isolated_hub_env):
    root = await installed_workflow()
    (root / "custom.txt").write_text("first edit")
    preview = (await client.post(URL + "/preview")).json()
    (root / "custom.txt").write_text("second edit")
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 409
    assert response.json()["detail"]["plan"]["token"] != preview["token"]
    assert (root / "custom.txt").read_text() == "second edit"
    assert not (isolated_hub_env["data_dir"] / "hub/backups").exists()


@pytest.mark.parametrize("modified", [False, True])
async def test_backup_failure_aborts_entire_suite_before_replacement(client, monkeypatch, modified):
    await installer.install_plugin("component", "soc-workspace")
    for identifier in ("stream_alert_triage", "stream_alert_denoise"):
        record = local.get_record("workflow", identifier)
        local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
        if modified:
            (Path(record.installPath) / "custom.txt").write_text(identifier)
    before_records = local._record_path().read_bytes()
    before_files = tree_hashes(local.install_root("workflow"))
    url = "/hub/plugins/component/soc-workspace/update"
    preview = (await client.post(url + "/preview")).json()
    assert len([i for i in preview["items"] if i["requiresConfirmation"]]) == 3

    def fail_backup(plan):
        raise OSError("disk full")

    monkeypatch.setattr(installer, "create_backup", fail_backup)
    result = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert result.status_code == 422
    assert "Backup failed; update cancelled" in result.json()["detail"]
    assert local._record_path().read_bytes() == before_records
    assert tree_hashes(local.install_root("workflow")) == before_files


async def test_failed_update_preserves_backup_and_rolls_back(client, monkeypatch):
    root = await installed_workflow()
    (root / "custom.txt").write_text("preserve")
    before = local.get_record("workflow", "stream_alert_triage")
    preview = (await client.post(URL + "/preview")).json()

    async def fail_refresh(*args):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(installer, "_refresh_runtime", fail_refresh)
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    detail = response.json()["detail"]
    backup = Path(detail.split("backup preserved at ")[1].split(": runtime")[0])
    assert (backup / "0/package/custom.txt").read_text() == "preserve"
    assert (root / "custom.txt").read_text() == "preserve"
    assert local.get_record("workflow", "stream_alert_triage") == before


async def test_edit_during_staging_is_not_overwritten(client, monkeypatch):
    root = await installed_workflow()
    copy = installer._copy_package_contents

    def copy_with_concurrent_edit(src, dst):
        copy(src, dst)
        (root / "just-edited.txt").write_text("new user change")

    monkeypatch.setattr(installer, "_copy_package_contents", copy_with_concurrent_edit)
    preview = (await client.post(URL + "/preview")).json()
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    assert "changed during update" in response.json()["detail"]
    assert (root / "just-edited.txt").read_text() == "new user change"


async def test_project_scope_uses_its_own_files(client):
    global_record = await installer.install_plugin("workflow", "stream_alert_triage")
    project = await installer.install_plugin("workflow", "stream_alert_triage", scope="project")
    (Path(project.installPath) / "custom.txt").write_text("project edit")
    preview = (await client.post(URL + "/preview", json={"scope": "project"})).json()
    assert preview["requiresConfirmation"]
    response = await client.post(
        URL, json={"scope": "project", "confirmationToken": preview["token"], "confirmChanges": True}
    )
    assert response.status_code == 200, response.text
    backup, _ = backup_manifest(response)
    assert (backup / "0/package/custom.txt").read_text() == "project edit"
    assert Path(global_record.installPath).exists()
    assert not (Path(project.installPath) / "custom.txt").exists()


async def test_backup_detects_changes_during_copy_and_aborts(client, monkeypatch, isolated_hub_env):
    from flocks.hub import update_protection

    root = await installed_workflow()
    (root / "custom.txt").write_text("first")
    preview = (await client.post(URL + "/preview")).json()
    copytree = update_protection.shutil.copytree

    def changed_copy(src, dst, *args, **kwargs):
        if Path(src) == root:
            (root / "custom.txt").write_text("changed during backup")
        return copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(update_protection.shutil, "copytree", changed_copy)
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    assert "Backup failed; update cancelled" in response.json()["detail"]
    assert (root / "custom.txt").read_text() == "changed during backup"
    assert not list((isolated_hub_env["data_dir"] / "hub/backups").iterdir())


async def test_new_release_invalidates_preview_token(client, monkeypatch):
    from flocks.hub import update_protection

    await installed_workflow()
    preview = (await client.post(URL + "/preview")).json()
    original_load = update_protection.load_manifest

    def newer_release(kind, identifier):
        manifest = original_load(kind, identifier)
        return manifest.model_copy(update={"version": "99.0.0"}) if identifier == "stream_alert_triage" else manifest

    monkeypatch.setattr(update_protection, "load_manifest", newer_release)
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 409
    assert local.get_record("workflow", "stream_alert_triage").version == "0.0.1"


async def test_mutations_wait_for_backup_lock(client):
    import asyncio
    from flocks.hub.update_protection import mutation_lock

    await installed_workflow()
    async with mutation_lock():
        task = asyncio.create_task(installer.uninstall_plugin("workflow", "stream_alert_triage"))
        await asyncio.sleep(0.08)
        assert not task.done()
        assert local.get_record("workflow", "stream_alert_triage") is not None
    assert await asyncio.wait_for(task, timeout=2)


async def test_missing_suite_manifest_does_not_disable_child_protection(client, monkeypatch):
    from flocks.hub import update_protection

    root = await installed_workflow()
    (root / "custom.txt").write_text("keep")
    load = update_protection.load_manifest

    def missing_suite(kind, identifier):
        if identifier == "soc-workspace":
            raise FileNotFoundError(identifier)
        return load(kind, identifier)

    monkeypatch.setattr(update_protection, "load_manifest", missing_suite)
    assert (await client.post(URL)).status_code == 409
    assert (root / "custom.txt").read_text() == "keep"


async def test_missing_optional_dependency_keeps_suite_install_working(client, monkeypatch):
    from flocks.hub import update_protection
    from flocks.hub.models import HubComponentRef

    load = installer.load_manifest

    def with_optional(kind, identifier):
        manifest = load(kind, identifier)
        if identifier == "soc-workspace":
            return manifest.model_copy(
                update={
                    "components": [
                        *manifest.components,
                        HubComponentRef(type="workflow", id="missing-optional", optional=True),
                    ]
                }
            )
        return manifest

    monkeypatch.setattr(installer, "load_manifest", with_optional)
    monkeypatch.setattr(update_protection, "load_manifest", with_optional)
    response = await client.post("/hub/plugins/component/soc-workspace/install")
    assert response.status_code == 200, response.text
    assert local.get_record("component", "soc-workspace") is not None


@pytest.mark.parametrize("mode", ["child", "suite", "webui", "access"])
async def test_final_swap_preserves_edits_after_last_check(client, monkeypatch, mode):
    if mode == "suite":
        await installer.install_plugin("component", "soc-workspace")
    kind, identifier = ("webui", "soc_ui") if mode in {"webui", "access"} else ("workflow", "stream_alert_triage")
    if mode != "suite":
        await installer.install_plugin(kind, identifier)
    record = local.get_record(kind, identifier)
    local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
    root = installer._contracts_access_dir(identifier, "global") if mode == "access" else Path(record.installPath)
    url = "/hub/plugins/component/soc-workspace/update" if mode == "suite" else f"/hub/plugins/{kind}/{identifier}/update"
    preview = (await client.post(url + "/preview")).json()
    replace = installer._replace_with_retry

    def write_just_before_rename(src, dst):
        if src == root:
            (root / "last-moment.txt").write_text("edit after final verification")
        return replace(src, dst)

    monkeypatch.setattr(installer, "_replace_with_retry", write_just_before_rename)
    response = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 200, response.text
    backup, plan = backup_manifest(response)
    index = next(i for i, item in enumerate(plan["items"]) if item["id"] == identifier)
    label = "access" if mode == "access" else "package"
    assert not (backup / str(index) / label / "last-moment.txt").exists()
    assert (backup / "replaced" / str(index) / label / "last-moment.txt").read_text() == "edit after final verification"
    assert not (root / "last-moment.txt").exists()
    # A later update must not purge the retained original as stale staging data.
    preview = (await client.post(url + "/preview")).json()
    response = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 200, response.text
    assert (backup / "replaced" / str(index) / label / "last-moment.txt").exists()


async def test_open_writer_to_old_inode_is_retained_after_update(client, monkeypatch):
    import sys
    if sys.platform == "win32":
        pytest.skip("Windows prevents renaming directories with open file handles")
    root = await installed_workflow()
    (root / "open-editor.txt").write_text("original")
    preview = (await client.post(URL + "/preview")).json()
    with (root / "open-editor.txt").open("a") as writer:
        async def write_after_swap(*args):
            writer.write(" + concurrent edit")
            writer.flush()
        monkeypatch.setattr(installer, "_refresh_runtime", write_after_swap)
        response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
        assert response.status_code == 200, response.text
        backup, _ = backup_manifest(response)
        writer.write(" + after update returned")
        writer.flush()
    assert (backup / "0/package/open-editor.txt").read_text() == "original"
    assert (backup / "replaced/0/package/open-editor.txt").read_text() == "original + concurrent edit + after update returned"


async def test_cannot_retain_original_aborts_before_overwrite(client, monkeypatch):
    import errno
    root = await installed_workflow()
    (root / "custom.txt").write_text("keep original")
    before = local.get_record("workflow", "stream_alert_triage")
    preview = (await client.post(URL + "/preview")).json()
    replace = installer._replace_with_retry
    def fail_retention(src, dst):
        if src == root:
            raise OSError(errno.EXDEV, "Cross-device link")
        return replace(src, dst)
    monkeypatch.setattr(installer, "_replace_with_retry", fail_retention)
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    assert "backup preserved at" in response.json()["detail"]
    assert (root / "custom.txt").read_text() == "keep original"
    assert local.get_record("workflow", "stream_alert_triage") == before


@pytest.mark.parametrize("record_exists", [True, False])
@pytest.mark.parametrize("failure_at", ["workflow", "component"])
async def test_suite_repair_failure_never_uninstalls_preexisting_content(client, monkeypatch, record_exists, failure_at):
    await installer.install_plugin("component", "soc-workspace")
    record = local.get_record("workflow", "stream_alert_triage")
    root = Path(record.installPath)
    for name in ("workflow.json", "workflow.md"):
        (root / name).unlink(missing_ok=True)
    (root / "custom.txt").write_text("partial old data")
    if not record_exists:
        local.remove_installed_record("workflow", "stream_alert_triage")
    before = tree_hashes(root)
    url = "/hub/plugins/component/soc-workspace/update"
    preview = (await client.post(url + "/preview")).json()
    async def fail_refresh(kind, *args):
        if kind == failure_at:
            raise RuntimeError("injected repair failure")
    monkeypatch.setattr(installer, "_refresh_runtime", fail_refresh)
    response = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422, response.text
    assert root.is_dir()
    if failure_at == "workflow":
        assert tree_hashes(root) == before
        assert local.get_record("workflow", "stream_alert_triage") == (record if record_exists else None)
    else:
        # A repaired child already committed before the parent failed; it must
        # remain installed, just like a successfully upgraded existing child.
        assert (root / "workflow.json").exists()
        assert local.get_record("workflow", "stream_alert_triage") is not None
    from flocks.config.config import Config
    assert any(p.read_text() == "partial old data" for p in (Config.get_data_path() / "hub/backups").rglob("custom.txt"))


async def test_late_edit_is_restored_when_update_fails(client, monkeypatch):
    root = await installed_workflow()
    before = local.get_record("workflow", "stream_alert_triage")
    preview = (await client.post(URL + "/preview")).json()
    replace = installer._replace_with_retry
    def write_before_rename(src, dst):
        if src == root:
            (root / "last-moment.txt").write_text("keep latest edit on rollback")
        return replace(src, dst)
    async def fail_refresh(*args):
        raise RuntimeError("injected failure after replacement")
    monkeypatch.setattr(installer, "_replace_with_retry", write_before_rename)
    monkeypatch.setattr(installer, "_refresh_runtime", fail_refresh)
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    assert (root / "last-moment.txt").read_text() == "keep latest edit on rollback"
    assert local.get_record("workflow", "stream_alert_triage") == before


@pytest.mark.parametrize("mode", ["child", "suite", "webui", "access"])
async def test_failed_update_preserves_edits_to_new_directory(client, monkeypatch, mode):
    from flocks.config.config import Config
    await installer.install_plugin("component", "soc-workspace")
    kind, identifier = ("webui", "soc_ui") if mode in {"webui", "access"} else ("tool", "soc_workspace_query")
    old = local.get_record(kind, identifier).model_copy(update={"version": "0.0.1"})
    local.save_installed_record(old)
    root = installer._contracts_access_dir(identifier, "global") if mode == "access" else Path(old.installPath)
    (root / "old-custom.txt").write_text("before update")
    url = "/hub/plugins/component/soc-workspace/update" if mode == "suite" else f"/hub/plugins/{kind}/{identifier}/update"
    preview = (await client.post(url + "/preview")).json()
    failed = False
    async def edit_then_fail(plugin_type, path=None):
        nonlocal failed
        if plugin_type == kind and not failed:
            failed = True
            (root / "new-custom.txt").write_text("edit in replacement directory")
            raise RuntimeError("injected failure after concurrent edit")
    monkeypatch.setattr(installer, "_refresh_runtime", edit_then_fail)
    response = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    assert (root / "old-custom.txt").read_text() == "before update"
    assert local.get_record(kind, identifier) == old
    saved = list((Config.get_data_path() / "hub/backups").rglob("new-custom.txt"))
    assert len(saved) == 1
    assert saved[0].read_text() == "edit in replacement directory"
    assert "failed" in saved[0].parts
    assert "backup preserved at" in response.json()["detail"]


@pytest.mark.parametrize("failure_at", ["tool", "component"])
async def test_new_suite_children_are_preserved_before_rollback_cleanup(client, monkeypatch, failure_at):
    from flocks.config.config import Config
    webui_root = local.install_dir("webui", "soc_ui")
    access_root = installer._contracts_access_dir("soc_ui", "global")
    failed = False
    async def edit_then_fail(kind, path=None):
        nonlocal failed
        if kind == failure_at and not failed:
            failed = True
            (webui_root / "new-custom.txt").write_text("newly installed page edit")
            (access_root / "new-access.txt").write_text("newly installed access edit")
            raise RuntimeError("injected suite failure")
    monkeypatch.setattr(installer, "_refresh_runtime", edit_then_fail)
    response = await client.post("/hub/plugins/component/soc-workspace/install")
    assert response.status_code == 422
    assert "backup preserved at" in response.json()["detail"]
    assert not webui_root.exists()
    assert not access_root.exists()
    assert local.get_record("webui", "soc_ui") is None
    backups = Config.get_data_path() / "hub/backups"
    assert any(p.read_text() == "newly installed page edit" for p in backups.rglob("new-custom.txt"))
    assert any(p.read_text() == "newly installed access edit" for p in backups.rglob("new-access.txt"))


async def test_rollback_keeps_new_tree_if_retention_fails(client, monkeypatch):
    root = await installed_workflow()
    (root / "old-custom.txt").write_text("before update")
    preview = (await client.post(URL + "/preview")).json()
    failed = False
    async def edit_then_fail(*args):
        nonlocal failed
        if not failed:
            failed = True
            (root / "new-custom.txt").write_text("must stay live")
            raise RuntimeError("injected update failure")
    replace = installer._replace_with_retry
    def deny_retention(src, dst):
        if src == root and "failed" in dst.parts:
            raise PermissionError("cannot preserve failed tree")
        return replace(src, dst)
    monkeypatch.setattr(installer, "_refresh_runtime", edit_then_fail)
    monkeypatch.setattr(installer, "_replace_with_retry", deny_retention)
    response = await client.post(URL, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == 422
    assert (root / "new-custom.txt").read_text() == "must stay live"
    from flocks.config.config import Config
    assert any(p.read_text() == "before update" for p in (Config.get_data_path() / "hub/backups").rglob("old-custom.txt"))
    assert "cannot preserve failed tree" in response.json()["detail"]


@pytest.mark.parametrize("mode", ["child", "suite"])
@pytest.mark.parametrize("fail_update", [False, True])
async def test_soc_update_retains_group_and_backup_protection(client, monkeypatch, mode, fail_update):
    from flocks.config.config import Config

    await installer.install_plugin("component", "soc-workspace")
    record = local.get_record("workflow", "stream_alert_triage").model_copy(update={"version": "0.0.1"})
    local.save_installed_record(record)
    root = Path(record.installPath)
    definition = json.loads((root / "workflow.json").read_text())
    official_description = definition["description"]
    definition["description"] = "Local body from an older release"
    (root / "workflow.json").write_text(json.dumps(definition))
    metadata = json.loads((root / "meta.json").read_text())
    metadata["group"] = "My SOC workflows"
    (root / "meta.json").write_text(json.dumps(metadata))
    url = "/hub/plugins/component/soc-workspace/update" if mode == "suite" else URL
    preview = (await client.post(url + "/preview")).json()
    assert preview["requiresConfirmation"]
    failed = False

    async def edit_then_fail(kind, path=None):
        nonlocal failed
        if kind == "workflow" and path == root and not failed:
            failed = True
            assert json.loads((root / "meta.json").read_text())["group"] == "My SOC workflows"
            assert json.loads((root / "workflow.json").read_text())["description"] == official_description
            (root / "concurrent-edit.txt").write_text("keep the edit after group preservation")
            raise RuntimeError("injected update failure")

    if fail_update:
        monkeypatch.setattr(installer, "_refresh_runtime", edit_then_fail)
    response = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert response.status_code == (422 if fail_update else 200), response.text
    assert json.loads((root / "meta.json").read_text())["group"] == "My SOC workflows"
    expected_description = definition["description"] if fail_update else official_description
    assert json.loads((root / "workflow.json").read_text())["description"] == expected_description
    if fail_update:
        assert failed
        assert local.get_record("workflow", "stream_alert_triage") == record
        saved = list((Config.get_data_path() / "hub/backups").rglob("concurrent-edit.txt"))
        assert len(saved) == 1 and "failed" in saved[0].parts
    else:
        backup, plan = backup_manifest(response)
        index = next(i for i, item in enumerate(plan["items"]) if item["id"] == "stream_alert_triage")
        for tree in (backup / str(index) / "package", backup / "replaced" / str(index) / "package"):
            assert json.loads((tree / "meta.json").read_text())["group"] == "My SOC workflows"
            assert json.loads((tree / "workflow.json").read_text())["description"] == definition["description"]
