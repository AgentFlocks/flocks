"""Installer for bundled Hub plugins."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from flocks.hub import local
from flocks.hub.catalog import clear_catalog_caches, load_manifest
from flocks.hub.files import plugin_root
from flocks.hub.models import (
    HubComponentRef,
    HubInstallProgressEvent,
    HubInstallProgressItem,
    HubPluginManifest,
    InstalledPluginRecord,
    PluginType,
)
from flocks.hub.security import SKIP_NAMES, validate_package


_TOOL_TYPE_DIRS = {"api", "device", "python", "mcp", "generated"}
InstallProgressCallback = Callable[[HubInstallProgressEvent], Awaitable[None]]


def _copytree_skip_caches(src: Path, dst: Path) -> None:
    """``shutil.copytree`` wrapper that prunes ``SKIP_NAMES`` entries.

    Bundled flockshub trees can carry leftover ``__pycache__``/VCS dirs
    after dev runs; we strip them on install so downstream loaders see
    a clean payload (and so our own validate_package can stay strict).
    """
    shutil.copytree(
        src,
        dst,
        ignore=lambda _src, names: [n for n in names if n in SKIP_NAMES],
    )


def _resolve_install_destination(
    plugin_type: PluginType,
    plugin_id: str,
    src: Path,
    scope: str,
) -> Path:
    """Pick an install destination that mirrors the source's layout.

    The default ``local.install_dir`` returns ``<base>/<plugin_id>``,
    which is fine for skills/agents/workflows but loses the
    ``api/``/``python/`` group prefix that tool plugins can ship with
    (whether bundled in flockshub or living under a project's
    ``.flocks/plugins/tools/api/<id>/`` tree). Dropping that prefix
    silently breaks :mod:`flocks.config.api_versioning`'s
    ``_provider.yaml`` discovery, which expects
    ``<plugins>/tools/api/<id>/_provider.yaml``.

    For ``plugin_type == "tool"`` we therefore inspect the source's
    immediate parent: when it is one of the recognised group dirs
    (``api/``, ``python/``, ``mcp/``, ``generated/``) we install to
    ``<base>/<group>/<plugin_id>/`` — regardless of whether the source
    is the bundled flockshub copy or an existing project-level install
    being re-installed at user scope.

    For ``plugin_type == "device"`` we always install to
    ``<user_plugins>/tools/device/<plugin_id>/`` (resolved through
    :func:`local.install_dir`). That keeps every device plugin in a
    canonical location regardless of how the source was laid out, and
    matches the search root used by
    :func:`flocks.config.api_versioning._api_plugin_roots`.

    All other plugin types and sources without a recognised group
    prefix fall back to the standard ``<base>/<plugin_id>/`` layout.
    """
    if plugin_type == "device":
        return local.install_dir(plugin_type, plugin_id, scope)

    if plugin_type != "tool":
        return local.install_dir(plugin_type, plugin_id, scope)

    try:
        parent_name = src.resolve().parent.name
        if parent_name in _TOOL_TYPE_DIRS:
            return local.install_root(plugin_type, scope) / parent_name / plugin_id
    except OSError:
        pass

    return local.install_dir(plugin_type, plugin_id, scope)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _replace_prepared_path(prepared: Path, dst: Path) -> Path | None:
    backup: Path | None = None
    if dst.exists() or dst.is_symlink():
        backup = dst.parent / f".{dst.name}.bak"
        _remove_path(backup)
        dst.replace(backup)
    try:
        prepared.replace(dst)
    except Exception:
        if backup is not None and (backup.exists() or backup.is_symlink()):
            backup.replace(dst)
        raise
    return backup


def _commit_replacement(backup: Path | None) -> None:
    if backup is None:
        return
    try:
        _remove_path(backup)
    except OSError:
        pass


def _rollback_replacement(dst: Path, backup: Path | None) -> None:
    _remove_path(dst)
    if backup is not None and (backup.exists() or backup.is_symlink()):
        backup.replace(dst)


def _copy_package(src: Path, dst: Path, *, retain_backup: bool = False) -> Path | None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        backup = _replace_prepared_path(tmp, dst)
        if not retain_backup:
            _commit_replacement(backup)
            return None
        return backup
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise


def _copy_package_contents(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name == "manifest.json" or item.name in SKIP_NAMES:
            continue
        target = dst / item.name
        if item.is_dir():
            _copytree_skip_caches(item, target)
        else:
            shutil.copy2(item, target)


def _contracts_access_dir(plugin_id: str, scope: str) -> Path:
    return local.install_root("webui", scope).parent / "access" / plugin_id


def _copy_attached_access_contracts(
    plugin_type: PluginType,
    plugin_id: str,
    src: Path,
    scope: str,
    *,
    retain_backup: bool = False,
) -> tuple[Path, Path | None] | None:
    if plugin_type != "webui":
        return None
    access_src = src / "access"
    if not access_src.is_dir():
        return None
    access_dst = _contracts_access_dir(plugin_id, scope)
    backup = _copy_package(access_src, access_dst, retain_backup=retain_backup)
    return access_dst, backup


def _remove_attached_access_contracts(plugin_type: PluginType, plugin_id: str, scope: str) -> None:
    if plugin_type != "webui":
        return
    access_dst = _contracts_access_dir(plugin_id, scope)
    if access_dst.is_dir():
        shutil.rmtree(access_dst)
    elif access_dst.exists():
        access_dst.unlink()


def _build_webui_pages(plugin_id: str, install_dir: Path) -> None:
    from flocks.contracts.webui.builder import WebUIPageBuilder
    from flocks.contracts.webui.store import WebUIPagesStore

    store = WebUIPagesStore(root=install_dir, project_root=None, legacy_root=None)
    pages = store.list_pages(enabled_only=False)
    if not pages:
        raise RuntimeError(f"WebUI package {plugin_id} does not contain any pages to build.")

    builder = WebUIPageBuilder(store)
    for page in pages:
        try:
            meta = builder.build(page.id)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to build WebUI page bundle for {plugin_id}/{page.id}: {exc}"
            ) from exc
        if meta.status != "ready":
            detail = f": {meta.error}" if meta.error else ""
            raise RuntimeError(f"Failed to build WebUI page bundle for {plugin_id}/{page.id}{detail}")


def _copy_webui_package_with_build(
    plugin_id: str,
    src: Path,
    dst: Path,
    *,
    retain_backup: bool = False,
) -> Path | None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        _build_webui_pages(plugin_id, tmp)
        backup = _replace_prepared_path(tmp, dst)
        if not retain_backup:
            _commit_replacement(backup)
            return None
        return backup
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise


async def _refresh_runtime(
    plugin_type: PluginType,
    changed_path: Path | None = None,
) -> None:
    if plugin_type == "skill":
        from flocks.skill.skill import Skill

        Skill.clear_cache()
        try:
            from flocks.agent.registry import Agent

            Agent.invalidate_cache()
        except Exception:
            pass
    elif plugin_type == "agent":
        from flocks.agent.registry import Agent

        Agent.invalidate_cache()
    elif plugin_type in {"tool", "device"}:
        # ``device`` plugins live under ``<plugins>/tools/device/<id>/``
        # and are loaded by the same ``ToolRegistry`` machinery as ``tool``
        # plugins — refreshing one means refreshing both, so a freshly
        # installed device is picked up by both the Tool API summary and
        # the Device Access wizard (the latter consumes
        # ``api_services[storage_key]`` shaped by ``discover_api_service_descriptors``).
        from flocks.config.api_versioning import discover_api_service_descriptors
        from flocks.tool.device.plugin_index import clear_device_template_cache
        from flocks.tool.registry import ToolRegistry

        await ToolRegistry.init_async()
        if changed_path is None:
            await asyncio.to_thread(ToolRegistry.refresh_plugin_tools)
        else:
            await asyncio.to_thread(
                ToolRegistry.refresh_plugin_tools,
                changed_path=changed_path,
            )
        clear_device_template_cache()
        # Drop the descriptor cache so freshly installed/uninstalled
        # API plugins surface in ``_load_provider_yaml_metadata`` (and
        # therefore in the Tool API summary metadata) without waiting
        # for the next process restart.
        discover_api_service_descriptors(refresh=True)
    elif plugin_type == "workflow":
        try:
            from flocks.workflow.center import scan_skill_workflows

            await scan_skill_workflows()
        except Exception:
            pass
    elif plugin_type == "webui":
        try:
            from flocks.contracts.webui.bootstrap import reconcile_webui_pages

            await reconcile_webui_pages()
        except Exception:
            pass
        try:
            from flocks.server.routes.event import publish_event

            await publish_event("contracts.webui.pages.nav_changed", {"source": "hub"})
        except Exception:
            pass


def component_install_items(manifest: HubPluginManifest) -> list[HubInstallProgressItem]:
    items: list[HubInstallProgressItem] = []
    seen: set[tuple[PluginType, str]] = set()
    for ref in manifest.components:
        key = (ref.type, ref.id)
        if key in seen:
            continue
        seen.add(key)
        name = ref.id
        name_cn = None
        try:
            ref_manifest = load_manifest(ref.type, ref.id)
            name = ref_manifest.name or ref.id
            name_cn = ref_manifest.nameCn
        except Exception:
            pass
        items.append(
            HubInstallProgressItem(
                type=ref.type,
                id=ref.id,
                name=name,
                nameCn=name_cn,
                optional=ref.optional,
            )
        )
    return items


async def _emit_component_progress(
    callback: InstallProgressCallback | None,
    manifest: HubPluginManifest,
    event: str,
    *,
    item: HubInstallProgressItem | None = None,
    items: list[HubInstallProgressItem] | None = None,
    record: InstalledPluginRecord | None = None,
    message: str | None = None,
) -> None:
    if callback is None:
        return
    event_item = item.model_copy(deep=True) if item is not None else None
    event_items = [entry.model_copy(deep=True) for entry in items] if items is not None else []
    await callback(
        HubInstallProgressEvent(
            event=event,
            id=manifest.id,
            type=manifest.type,
            name=manifest.name,
            nameCn=manifest.nameCn,
            total=len(event_items) if items is not None else len(component_install_items(manifest)),
            item=event_item,
            items=event_items,
            record=record,
            message=message,
        )
    )


async def _rollback_component_ref_installs(
    refs: list[tuple[PluginType, str]],
    component_key: str,
) -> None:
    seen: set[tuple[PluginType, str]] = set()
    for plugin_type, plugin_id in reversed(refs):
        key = (plugin_type, plugin_id)
        if key in seen:
            continue
        seen.add(key)
        record = local.get_record(plugin_type, plugin_id)
        if record is None or record.installedBy != component_key:
            continue
        try:
            await uninstall_plugin(plugin_type, plugin_id)
        except FileNotFoundError:
            local.remove_installed_record(plugin_type, plugin_id)
        except Exception:
            continue


def _is_project_install_path(plugin_type: PluginType, install_path: Path) -> bool:
    try:
        project_root = local.install_root(plugin_type, "project").resolve()
        resolved_install_path = install_path.resolve()
    except OSError:
        return False
    return resolved_install_path == project_root or project_root in resolved_install_path.parents


def _bundled_source_for_ref(ref: HubComponentRef) -> str | None:
    try:
        ref_manifest = load_manifest(ref.type, ref.id)
    except Exception:
        return None
    if ref_manifest.source.kind != "bundled":
        return None
    return f"bundled:{ref_manifest.source.path or ''}"


def _can_adopt_existing_ref(
    ref: HubComponentRef,
    record: InstalledPluginRecord,
    component_key: str,
    install_path: Path | None,
) -> bool:
    if not ref.adoptExisting:
        return False
    if record.installedBy not in {None, component_key}:
        return False
    if record.scope == "project":
        return False
    if install_path is not None and _is_project_install_path(ref.type, install_path):
        return False
    return record.source == _bundled_source_for_ref(ref)


async def _install_component_refs(
    manifest: HubPluginManifest,
    *,
    scope: str,
    progress: InstallProgressCallback | None = None,
) -> list[tuple[PluginType, str]]:
    seen: set[tuple[PluginType, str]] = set()
    component_key = f"component:{manifest.id}"
    rollback_refs: list[tuple[PluginType, str]] = []
    adopted_records: list[InstalledPluginRecord] = []
    progress_items = component_install_items(manifest)
    await _emit_component_progress(progress, manifest, "start", items=progress_items)
    item_lookup = {(item.type, item.id): item for item in progress_items}
    try:
        for ref in manifest.components:
            key = (ref.type, ref.id)
            if key in seen:
                continue
            seen.add(key)
            item = item_lookup.get(key) or HubInstallProgressItem(type=ref.type, id=ref.id, optional=ref.optional)
            if ref.type == "component":
                item.status = "failed"
                item.message = "Nested Hub components are not supported"
                await _emit_component_progress(progress, manifest, "item", item=item)
                raise ValueError("Nested Hub components are not supported")
            existing_path = local.infer_local_install(ref.type, ref.id)
            if existing_path is not None:
                existing_record = local.get_record(ref.type, ref.id)
                if existing_record is not None and existing_record.installedBy == component_key:
                    item.status = "installing"
                    item.message = "Updating component-managed dependency"
                    await _emit_component_progress(progress, manifest, "item", item=item)
                    await install_plugin(
                        ref.type,
                        ref.id,
                        scope=scope,
                        installed_by=component_key,
                    )
                    item.status = "installed"
                    item.message = "Updated"
                    await _emit_component_progress(progress, manifest, "item", item=item)
                    continue
                if existing_record is not None and _can_adopt_existing_ref(ref, existing_record, component_key, existing_path):
                    adopted_records.append(existing_record)
                    local.save_installed_record(existing_record.model_copy(update={"installedBy": component_key}))
                    item.status = "installed"
                    item.message = "Already installed; adopted by component"
                    await _emit_component_progress(progress, manifest, "item", item=item)
                    continue
                item.status = "skipped"
                item.message = "Already installed"
                await _emit_component_progress(progress, manifest, "item", item=item)
                continue
            item.status = "installing"
            await _emit_component_progress(progress, manifest, "item", item=item)
            rollback_refs.append(key)
            try:
                await install_plugin(ref.type, ref.id, scope=scope, installed_by=component_key)
            except Exception as exc:
                await _rollback_component_ref_installs([key], component_key)
                if ref.optional:
                    item.status = "skipped"
                    item.message = f"Optional dependency failed to install: {exc}"
                    await _emit_component_progress(progress, manifest, "item", item=item)
                    continue
                item.status = "failed"
                item.message = str(exc) or "Install failed"
                await _emit_component_progress(progress, manifest, "item", item=item)
                raise
            item.status = "installed"
            await _emit_component_progress(progress, manifest, "item", item=item)
    except Exception:
        for original_record in reversed(adopted_records):
            local.save_installed_record(original_record)
        await _rollback_component_ref_installs(rollback_refs, component_key)
        raise
    return rollback_refs


async def _uninstall_component_refs(manifest: HubPluginManifest) -> bool:
    component_key = f"component:{manifest.id}"
    seen: set[tuple[PluginType, str]] = set()
    removed = False
    for ref in reversed(manifest.components):
        key = (ref.type, ref.id)
        if key in seen or ref.type == "component":
            continue
        seen.add(key)
        record = local.get_record(ref.type, ref.id)
        if record is None:
            install_path = local.infer_local_install(ref.type, ref.id)
            if install_path is None or _is_project_install_path(ref.type, install_path):
                continue
        elif record.installedBy != component_key:
            install_path = Path(record.installPath) if record.installPath else local.infer_local_install(ref.type, ref.id)
            if not _can_adopt_existing_ref(ref, record, component_key, install_path):
                continue
        removed = True
        try:
            await uninstall_plugin(ref.type, ref.id)
        except FileNotFoundError:
            local.remove_installed_record(ref.type, ref.id)
        except Exception:
            if ref.optional:
                continue
            raise
    return removed


def _clear_device_template_cache_if_needed(plugin_type: PluginType) -> None:
    if plugin_type not in {"tool", "device"}:
        return
    try:
        from flocks.tool.device.plugin_index import clear_device_template_cache

        clear_device_template_cache()
    except Exception:
        pass


async def install_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    *,
    scope: str = "global",
    installed_by: str | None = None,
    progress: InstallProgressCallback | None = None,
) -> InstalledPluginRecord:
    manifest = load_manifest(plugin_type, plugin_id)
    src = plugin_root(plugin_type, plugin_id)
    validate_package(src, manifest)
    dst = _resolve_install_destination(plugin_type, plugin_id, src, scope)
    component_key = f"component:{plugin_id}"
    component_ref_installs: list[tuple[PluginType, str]] = []
    previous_record = local.get_record(plugin_type, plugin_id)
    package_backup: Path | None = None
    package_replaced = False
    access_replacement: tuple[Path, Path | None] | None = None
    try:
        if plugin_type == "component":
            component_ref_installs = await _install_component_refs(manifest, scope=scope, progress=progress)
        if plugin_type == "webui":
            package_backup = _copy_webui_package_with_build(
                plugin_id,
                src,
                dst,
                retain_backup=True,
            )
        else:
            package_backup = _copy_package(src, dst, retain_backup=True)
        package_replaced = True
        access_replacement = _copy_attached_access_contracts(
            plugin_type,
            plugin_id,
            src,
            scope,
            retain_backup=True,
        )
        record = local.make_record(
            plugin_type=plugin_type,
            plugin_id=plugin_id,
            version=manifest.version,
            source=f"bundled:{manifest.source.path or ''}",
            install_path=dst,
            enabled=True,
            scope=scope,
            installed_by=installed_by,
        )
        local.save_installed_record(record)
        clear_catalog_caches()
        await _refresh_runtime(plugin_type, dst)
        if plugin_type == "component":
            await _emit_component_progress(progress, manifest, "complete", record=record, message="Installed")
        if access_replacement is not None:
            _commit_replacement(access_replacement[1])
        _commit_replacement(package_backup)
        return record
    except Exception:
        if access_replacement is not None:
            _rollback_replacement(*access_replacement)
        if package_replaced:
            _rollback_replacement(dst, package_backup)
        if previous_record is None:
            local.remove_installed_record(plugin_type, plugin_id)
        else:
            local.save_installed_record(previous_record)
        clear_catalog_caches()
        if plugin_type == "component":
            await _rollback_component_ref_installs(component_ref_installs, component_key)
        if package_replaced:
            try:
                await _refresh_runtime(plugin_type, dst)
            except Exception:
                pass
        raise


async def update_plugin(plugin_type: PluginType, plugin_id: str, *, scope: str = "global") -> InstalledPluginRecord:
    return await install_plugin(plugin_type, plugin_id, scope=scope)


def _collect_storage_keys(install_path: Path) -> list[str]:
    """Return ``api_services`` storage keys declared inside *install_path*.

    Reads any ``_provider.yaml`` shipped with the plugin and computes
    the same ``derive_storage_key(service_id, version)`` that
    :mod:`flocks.config.api_versioning` uses, so callers can target
    exactly the entries the runtime would have bootstrapped for this
    plugin. Returns ``[]`` when no provider yaml is present (e.g.
    skill / agent / workflow plugins).
    """
    from flocks.config.api_versioning import _descriptor_for_plugin_dir

    keys: list[str] = []
    if not install_path.is_dir():
        return keys
    descriptor = _descriptor_for_plugin_dir(install_path)
    if descriptor is not None:
        keys.append(descriptor.storage_key)
    return keys


def _cleanup_orphan_api_services(storage_keys: list[str]) -> None:
    """Drop ``api_services`` config entries (and cached statuses) whose
    backing plugin has just been uninstalled.

    Skips a key when another installed plugin still declares it — e.g.
    if two on-disk plugin dirs happen to ship identical
    ``service_id``+``version``. That keeps remaining installs working
    while still cleaning up orphans.
    """
    if not storage_keys:
        return
    from flocks.config.api_versioning import discover_api_service_descriptors
    from flocks.config.config_writer import ConfigWriter

    surviving = {d.storage_key for d in discover_api_service_descriptors(refresh=True)}
    for storage_key in storage_keys:
        if storage_key in surviving:
            continue
        try:
            ConfigWriter.remove_api_service(storage_key)
        except Exception:
            pass


async def uninstall_plugin(plugin_type: PluginType, plugin_id: str) -> bool:
    manifest = load_manifest(plugin_type, plugin_id) if plugin_type == "component" else None
    record = local.get_record(plugin_type, plugin_id)
    install_path = Path(record.installPath) if record and record.installPath else local.infer_local_install(plugin_type, plugin_id)
    if install_path is None or not install_path.exists():
        changed_path = install_path
        if changed_path is None and record is not None:
            try:
                changed_path = _resolve_install_destination(
                    plugin_type,
                    plugin_id,
                    plugin_root(plugin_type, plugin_id),
                    record.scope,
                )
            except Exception:
                changed_path = local.install_dir(plugin_type, plugin_id, record.scope)
        children_removed = await _uninstall_component_refs(manifest) if manifest is not None else False
        had_record = record is not None
        local.remove_installed_record(plugin_type, plugin_id)
        clear_catalog_caches()
        _clear_device_template_cache_if_needed(plugin_type)
        if children_removed or had_record:
            await _refresh_runtime(plugin_type, changed_path)
        return children_removed or had_record
    project_root = local.install_root(plugin_type, "project").resolve()
    resolved_install_path = install_path.resolve()
    if resolved_install_path == project_root or project_root in resolved_install_path.parents:
        raise ValueError("Built-in project Hub plugins cannot be removed")
    if ".flocks/plugins" not in install_path.as_posix():
        raise ValueError("Only user-managed Hub plugin installs can be removed")
    # Capture provider metadata BEFORE rmtree — once the dir is gone we
    # can't read its ``_provider.yaml`` to know which api_services keys
    # were derived from it. ``device`` plugins reuse the same provider
    # yaml machinery as ``tool``/``api`` plugins, so we collect orphan
    # storage keys for both types.
    orphan_keys = (
        _collect_storage_keys(install_path)
        if plugin_type in {"tool", "device"}
        else []
    )
    if manifest is not None:
        await _uninstall_component_refs(manifest)
    if install_path.is_dir():
        shutil.rmtree(install_path)
    else:
        install_path.unlink()
    _remove_attached_access_contracts(plugin_type, plugin_id, record.scope if record else "global")
    local.remove_installed_record(plugin_type, plugin_id)
    clear_catalog_caches()
    _cleanup_orphan_api_services(orphan_keys)
    await _refresh_runtime(plugin_type, install_path)
    return True
