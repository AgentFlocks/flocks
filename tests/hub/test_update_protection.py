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


async def test_clean_update_needs_no_confirmation_or_backup(client, isolated_hub_env):
    root = await installed_workflow()
    before = tree_hashes(root)
    preview = (await client.post(URL + "/preview")).json()
    assert preview["requiresConfirmation"] is False
    assert preview["items"][0]["changes"] == []
    result = await client.post(URL)
    assert result.status_code == 200, result.text
    assert result.json()["backupPath"] is None
    assert tree_hashes(root) == before
    assert not (isolated_hub_env["data_dir"] / "hub/backups").exists()
    assert result.json()["fileHashes"]


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
    assert {c["kind"] for c in item["changes"]} == {"added", "modified", "deleted"}
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
    assert not (await client.post(url + "/preview")).json()["requiresConfirmation"]


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
    assert not (await client.post(url + "/preview")).json()["requiresConfirmation"]
    access = local.install_root("webui").parent / "access/soc_ui"
    (access / "user-contract.json").write_text('{"custom": true}')
    preview = (await client.post(url + "/preview")).json()
    assert {"path": "access/user-contract.json", "kind": "added"} in preview["items"][0]["changes"]
    result = await client.post(url, json={"confirmationToken": preview["token"], "confirmChanges": True})
    assert result.status_code == 200, result.text
    backup, _ = backup_manifest(result)
    assert (backup / "0/access/user-contract.json").exists()
    assert (backup / "0/package/soc_overview/dist/page.js").read_text() == "generated"
    assert not (access / "user-contract.json").exists()


@pytest.mark.parametrize(
    "legacy_version,modified,requires,known",
    [
        ("same", False, False, True),
        ("same", True, True, True),
        ("older", False, True, False),
    ],
)
async def test_legacy_baseline_handling(client, legacy_version, modified, requires, known):
    record = await installer.install_plugin("workflow", "stream_alert_triage")
    local.save_installed_record(
        record.model_copy(
            update={
                "fileHashes": None,
                "version": record.version if legacy_version == "same" else "0.0.1",
            }
        )
    )
    if modified:
        (Path(record.installPath) / "custom.txt").write_text("custom")
    plan = (await client.post(URL + "/preview")).json()
    assert plan["requiresConfirmation"] is requires
    assert plan["items"][0]["baselineKnown"] is known


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


async def test_backup_failure_aborts_entire_suite_before_replacement(client, monkeypatch):
    await installer.install_plugin("component", "soc-workspace")
    for identifier in ("stream_alert_triage", "stream_alert_denoise"):
        record = local.get_record("workflow", identifier)
        local.save_installed_record(record.model_copy(update={"version": "0.0.1"}))
        (Path(record.installPath) / "custom.txt").write_text(identifier)
    before_records = local._record_path().read_bytes()
    before_files = tree_hashes(local.install_root("workflow"))
    url = "/hub/plugins/component/soc-workspace/update"
    preview = (await client.post(url + "/preview")).json()
    assert len([i for i in preview["items"] if i["requiresConfirmation"]]) == 2

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
    response = await client.post(URL)
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
