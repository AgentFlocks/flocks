"""Keep optional-manifest handling consistent between preview and installation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from flocks.hub import catalog, installer, local, update_protection


SUITE = "optional-preflight-suite"
REQUIRED = "required-workflow"
OPTIONAL = "optional-workflow"


@pytest.fixture
def bundle(isolated_hub_env, tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    monkeypatch.setenv("FLOCKS_HUB_ROOT", str(hub))
    monkeypatch.setenv("FLOCKS_ROOT", str(isolated_hub_env["home"] / ".flocks"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    catalog.clear_catalog_caches()

    async def refresh(*args):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", refresh)

    def package(kind, identifier, **extra):
        path = hub / "plugins" / f"{kind}s" / identifier
        path.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schemaVersion": "hub.plugin.v1",
            "type": kind,
            "id": identifier,
            "name": identifier,
            "version": "1.0.0",
            "source": {"kind": "bundled", "path": f"plugins/{kind}s/{identifier}"},
            **extra,
        }
        (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if kind == "workflow":
            (path / "workflow.json").write_text(
                json.dumps({"id": identifier, "nodes": [], "edges": []}), encoding="utf-8"
            )
        return path

    yield package
    catalog.clear_catalog_caches()


def suite_refs(*, optional=True):
    return [
        {"type": "workflow", "id": REQUIRED},
        {"type": "workflow", "id": OPTIONAL, "optional": optional},
    ]


def damage_manifest(path: Path, damage: str):
    manifest = path / "manifest.json"
    if damage == "missing":
        manifest.unlink()
        # No payload must remain available for legacy manifest inference.
        (path / "workflow.json").unlink()
    else:
        manifest.write_bytes({"json": b"{broken json", "schema": b"{}", "encoding": b"\xff"}[damage])


@pytest.mark.parametrize("operation", ["install", "update"])
@pytest.mark.parametrize("damage", ["json", "schema", "encoding", "missing"])
async def test_invalid_optional_manifest_is_skipped_and_required_workflow_installs(bundle, operation, damage):
    bundle("workflow", REQUIRED)
    if operation == "update":
        bundle("component", SUITE)
        await installer.install_plugin("component", SUITE)
    bundle("component", SUITE, components=suite_refs())
    damage_manifest(bundle("workflow", OPTIONAL), damage)

    plan = update_protection.build_plan("component", SUITE)
    assert {(item["type"], item["id"]) for item in plan["items"]} == {
        ("component", SUITE), ("workflow", REQUIRED)
    }
    events = []

    async def progress(event):
        if event.item:
            events.append(event.item)

    # update_plugin delegates to the same protected install entry point, but
    # suite progress is only exposed by install_plugin.
    if operation == "install":
        record = await installer.install_plugin("component", SUITE, progress=progress)
        assert any(item.id == OPTIONAL and item.status == "skipped" for item in events)
    else:
        record = await installer.update_plugin("component", SUITE)
    assert record.id == SUITE
    assert local.get_record("component", SUITE) is not None
    assert local.get_record("workflow", REQUIRED) is not None
    assert local.get_record("workflow", OPTIONAL) is None
    assert not local.install_dir("workflow", OPTIONAL).exists()


@pytest.mark.parametrize("damage", ["json", "schema", "encoding", "missing"])
async def test_invalid_required_manifest_still_blocks_before_installation(bundle, damage):
    bundle("component", SUITE, components=suite_refs(optional=False))
    bundle("workflow", REQUIRED)
    damage_manifest(bundle("workflow", OPTIONAL), damage)
    error = {
        "json": json.JSONDecodeError,
        "schema": ValidationError,
        "encoding": UnicodeDecodeError,
        "missing": FileNotFoundError,
    }[damage]

    with pytest.raises(error):
        update_protection.build_plan("component", SUITE)
    with pytest.raises(error):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("component", SUITE) is None
    assert local.get_record("workflow", REQUIRED) is None
    assert not local.install_dir("workflow", REQUIRED).exists()


@pytest.mark.parametrize("damage", ["json", "schema"])
async def test_soc_update_still_confirms_and_backs_up_when_optional_manifest_is_invalid(bundle, damage):
    bundle("component", "soc-workspace", components=suite_refs()[:1])
    bundle("workflow", REQUIRED)
    await installer.install_plugin("component", "soc-workspace")
    previous = local.get_record("workflow", REQUIRED)
    local.save_installed_record(previous.model_copy(update={"version": "0.0.1"}))
    root = Path(previous.installPath)
    (root / "custom.txt").write_text("preserve this edit", encoding="utf-8")
    bundle("component", "soc-workspace", components=suite_refs())
    damage_manifest(bundle("workflow", OPTIONAL), damage)

    plan = update_protection.build_plan("component", "soc-workspace")
    assert plan["requiresConfirmation"]
    with pytest.raises(update_protection.UpdateConfirmationRequired):
        await installer.update_plugin("component", "soc-workspace")
    assert (root / "custom.txt").read_text() == "preserve this edit"

    result = await installer.update_plugin(
        "component", "soc-workspace", confirmation_token=plan["token"], confirm_changes=True
    )
    backup = Path(result.backupPath)
    index = next(index for index, item in enumerate(plan["items"]) if item["id"] == REQUIRED)
    assert (backup / str(index) / "package/custom.txt").read_text() == "preserve this edit"
    assert not (root / "custom.txt").exists()
    assert local.get_record("workflow", REQUIRED).version == "1.0.0"
    assert local.get_record("workflow", OPTIONAL) is None


@pytest.mark.parametrize("stage", ["manifest_io", "path_safety", "snapshot"])
async def test_optional_dependency_does_not_hide_io_safety_or_snapshot_errors(bundle, monkeypatch, stage):
    bundle("component", SUITE, components=suite_refs())
    bundle("workflow", REQUIRED)
    bundle("workflow", OPTIONAL)
    if stage == "manifest_io":
        original = update_protection.load_manifest

        def load(kind, identifier):
            if identifier == OPTIONAL:
                raise PermissionError("manifest is unreadable")
            return original(kind, identifier)

        monkeypatch.setattr(update_protection, "load_manifest", load)
        error = PermissionError
    elif stage == "path_safety":
        original = update_protection.plugin_root

        def source(kind, identifier, **kwargs):
            if identifier == OPTIONAL:
                raise ValueError("plugin path escapes hub root")
            return original(kind, identifier, **kwargs)

        monkeypatch.setattr(update_protection, "plugin_root", source)
        error = ValueError
    else:
        original = update_protection.payload_hashes

        def hashes(kind, root, *args, **kwargs):
            if root.name == OPTIONAL:
                raise RuntimeError("snapshot changed during update")
            return original(kind, root, *args, **kwargs)

        monkeypatch.setattr(update_protection, "payload_hashes", hashes)
        error = RuntimeError

    with pytest.raises(error):
        await installer.install_plugin("component", SUITE)
    assert local.get_record("component", SUITE) is None
    assert local.get_record("workflow", REQUIRED) is None
