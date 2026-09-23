"""Installer for bundled Hub plugins."""

from __future__ import annotations

import asyncio
import errno
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable

from flocks.hub import local
from flocks.hub.catalog import _catalog_install_state, clear_catalog_caches, load_manifest
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
from flocks.hub.update_protection import (
    UpdateConfirmationRequired,
    build_plan,
    create_backup,
    mutation_lock,
    payload_hashes,
    public_plan,
    tree_hashes,
)


_TOOL_TYPE_DIRS = {"api", "device", "python", "mcp", "generated"}
InstallProgressCallback = Callable[[HubInstallProgressEvent], Awaitable[None]]


def _validate_uninstall_plugin_id(plugin_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", plugin_id) or plugin_id.endswith("."):
        raise ValueError("Invalid Hub plugin id: expected a single plugin name without path separators")


def _validate_uninstall_target(plugin_type: PluginType, plugin_id: str, path: Path, scope: str) -> None:
    """Accept only the exact managed payload locations for this plugin id."""
    _validate_uninstall_plugin_id(plugin_id)
    if _is_project_install_path(plugin_type, path):
        # A stale project record may be reconciled without touching its payload
        # or attached contracts. Existing project payloads remain immutable here.
        if scope != "project" or path.exists():
            raise ValueError("Built-in project Hub plugins cannot be removed")
    elif scope == "project":
        raise ValueError("Project plugin path escapes its managed location")
    base = local.install_root(plugin_type, scope).resolve()
    allowed = {base / plugin_id}
    if plugin_type == "tool":
        allowed.update(base / group / plugin_id for group in _TOOL_TYPE_DIRS)
        allowed.update(
            parent / f"{plugin_id}{suffix}"
            for parent in (base, *(base / group for group in _TOOL_TYPE_DIRS))
            for suffix in (".yaml", ".yml", ".py")
        )
    elif plugin_type == "device":
        allowed.add(local.install_root("tool", scope).resolve() / plugin_id)
    if path.is_symlink() or path.resolve() not in allowed:
        raise ValueError("Only this plugin's exact user-managed install location can be removed")


def _validate_access_uninstall_target(plugin_id: str, scope: str) -> Path:
    _validate_uninstall_plugin_id(plugin_id)
    if scope != "global":
        raise ValueError("Built-in project Hub plugins cannot be removed")
    access_root = local.install_root("webui", scope).resolve().parent / "access"
    access_path = access_root / plugin_id
    if access_path.resolve() != access_path:
        raise ValueError("Access contract path escapes this plugin's managed location")
    return access_path


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


def _purge_stale_scratch(parent: Path, name: str) -> None:
    """Remove leftover ``.<name>.<rand>`` / ``.<name>.bak`` staging dirs.

    A failed atomic swap (see :func:`_replace_prepared_path`) can leave
    scratch and backup dirs behind next to *parent*/*name*. They are never
    valid installs, but on Windows a lingering ``.<name>.bak`` blocks the
    next swap, so we clear both before staging a fresh copy.
    """
    if not parent.is_dir():
        return
    for entry in parent.iterdir():
        stale = entry.name.startswith(f".{name}.") or entry.name == f".{name}.bak"
        if not stale:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            pass


def _replace_with_retry(src: Path, dst: Path) -> None:
    """``src.replace(dst)`` with a Windows access-denied backoff.

    On Windows an antivirus scan or a directory watcher (e.g. the WebUI
    page watcher over ``~/.flocks/plugins/contracts/webui``) can hold a
    transient handle on the freshly written tree, making the atomic swap
    fail with ``PermissionError`` (WinError 5 / 32). Elsewhere the rename
    is atomic and never needs retrying.
    """
    if sys.platform != "win32":
        src.replace(dst)
        return
    delay = 0.1
    for attempt in range(6):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def _replace_prepared_path(
    prepared: Path, dst: Path, *, backup_destination: Path | None = None,
) -> Path | None:
    backup: Path | None = None
    if dst.exists() or dst.is_symlink():
        backup = backup_destination if backup_destination is not None else dst.parent / f".{dst.name}.bak"
        if backup_destination is None:
            _remove_path(backup)
        else:
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if backup.exists() or backup.is_symlink():
                raise FileExistsError(f"Retained original already exists: {backup}")
        # Keep the original inode tree, including writes made after the last
        # hash check or through already-open file handles. Never copy/delete
        # as a cross-device fallback: failed retention must stop replacement.
        try:
            _replace_with_retry(dst, backup)
        except OSError as exc:
            if backup_destination is not None and exc.errno == errno.EXDEV:
                raise RuntimeError(
                    "Cannot retain original directory across filesystems; "
                    "keep the Hub backup directory and plugin on the same filesystem, then retry"
                ) from exc
            raise
    try:
        _replace_with_retry(prepared, dst)
    except Exception:
        if backup is not None and (backup.exists() or backup.is_symlink()):
            _replace_with_retry(backup, dst)
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
        _replace_with_retry(backup, dst)


def _copy_package(
    src: Path, dst: Path, *, retain_backup: bool = False,
    before_replace: Callable[[], None] | None = None,
    backup_destination: Path | None = None,
) -> Path | None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    _purge_stale_scratch(parent, dst.name)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        if before_replace is not None:
            before_replace()
        backup = _replace_prepared_path(tmp, dst, backup_destination=backup_destination)
        if not retain_backup and backup_destination is None:
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
    before_replace: Callable[[], None] | None = None,
    backup_destination: Path | None = None,
) -> tuple[Path, Path | None] | None:
    if plugin_type != "webui":
        return None
    access_src = src / "access"
    if not access_src.is_dir():
        return None
    access_dst = _contracts_access_dir(plugin_id, scope)
    backup = _copy_package(
        access_src, access_dst, retain_backup=retain_backup,
        before_replace=before_replace, backup_destination=backup_destination,
    )
    return access_dst, backup


def _remove_attached_access_contracts(plugin_type: PluginType, plugin_id: str, scope: str) -> bool:
    if plugin_type != "webui":
        return False
    access_dst = _validate_access_uninstall_target(plugin_id, scope)
    if access_dst.is_dir():
        shutil.rmtree(access_dst)
    elif access_dst.exists():
        access_dst.unlink()
    else:
        return False
    return True


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
    before_replace: Callable[[], None] | None = None,
    backup_destination: Path | None = None,
) -> Path | None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    _purge_stale_scratch(parent, dst.name)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        _build_webui_pages(plugin_id, tmp)
        if before_replace is not None:
            before_replace()
        backup = _replace_prepared_path(tmp, dst, backup_destination=backup_destination)
        if not retain_backup and backup_destination is None:
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
            await _uninstall_plugin(plugin_type, plugin_id)
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


def _component_ref_is_outdated(
    ref: HubComponentRef,
    install_path: Path,
    record: InstalledPluginRecord | None,
) -> bool:
    """Use the catalog's update decision, including unversioned legacy installs."""
    try:
        available = load_manifest(ref.type, ref.id).version
    except Exception:
        return False
    state, _ = _catalog_install_state(ref.type, install_path, record, available)
    return state == "updateAvailable"


async def _install_component_refs(
    manifest: HubPluginManifest,
    *,
    scope: str,
    progress: InstallProgressCallback | None = None,
    protection_plan: dict | None = None,
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
                # Installing or updating the suite has to bring an outdated
                # child along, otherwise the suite reads as current while its
                # pages stay on the old version.
                if _component_ref_is_outdated(ref, existing_path, existing_record):
                    item.status = "installing"
                    await _emit_component_progress(progress, manifest, "item", item=item)
                    try:
                        await _install_plugin(ref.type, ref.id, scope=scope, installed_by=component_key, protection_plan=protection_plan)
                    except Exception as exc:
                        if ref.optional:
                            item.status = "skipped"
                            item.message = f"Optional dependency failed to update: {exc}"
                            await _emit_component_progress(progress, manifest, "item", item=item)
                            continue
                        item.status = "failed"
                        item.message = str(exc) or "Update failed"
                        await _emit_component_progress(progress, manifest, "item", item=item)
                        raise
                    item.status = "installed"
                    item.message = "Updated by component"
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
            try:
                source = plugin_root(ref.type, ref.id, prefer_bundled=True)
                target = _resolve_install_destination(ref.type, ref.id, source, scope)
                access = _contracts_access_dir(ref.id, scope) if ref.type == "webui" else None
                # infer_local_install can miss damaged payloads. Only truly
                # new dependencies belong to the suite's uninstall rollback.
                had_previous_state = (
                    local.get_record(ref.type, ref.id) is not None
                    or target.exists() or target.is_symlink()
                    or (access is not None and (access.exists() or access.is_symlink()))
                )
                await _install_plugin(ref.type, ref.id, scope=scope, installed_by=component_key, protection_plan=protection_plan)
                if not had_previous_state:
                    rollback_refs.append(key)
            except Exception as exc:
                # _install_plugin has already restored its previous payload
                # and record. Uninstalling here would delete that restoration.
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
            await _uninstall_plugin(ref.type, ref.id)
        except FileNotFoundError:
            local.remove_installed_record(ref.type, ref.id)
        except Exception as exc:
            if ref.optional:
                continue
            raise RuntimeError(
                f"Cannot uninstall scene suite {manifest.id}: required component "
                f"{ref.type}/{ref.id} could not be removed: {exc}"
            ) from exc
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
    confirmation_token: str | None = None,
    confirm_changes: bool = False,
) -> InstalledPluginRecord:
    async with mutation_lock():
        plan = build_plan(plugin_type, plugin_id, scope)
        if (confirmation_token is not None and confirmation_token != plan["token"]) or (
            plan["requiresConfirmation"] and (not confirm_changes or confirmation_token != plan["token"])
        ):
            raise UpdateConfirmationRequired(public_plan(plan))
        try:
            backup = await asyncio.to_thread(create_backup, plan) if plan["requiresConfirmation"] else None
        except Exception as exc:
            raise RuntimeError(f"Backup failed; update cancelled: {exc}") from exc
        try:
            refreshed = build_plan(plugin_type, plugin_id, scope)
            if refreshed["token"] != plan["token"]:
                if backup is not None:
                    raise RuntimeError("Files changed after backup; retry the update")
                raise UpdateConfirmationRequired(public_plan(refreshed))
            if backup is not None:
                for index, item in enumerate(plan["items"]):
                    item["_retainedRoots"] = {
                        label: backup / "replaced" / str(index) / label for label in item["_roots"]
                    }
            record = await _install_plugin(
                plugin_type, plugin_id, scope=scope, installed_by=installed_by,
                progress=progress, protection_plan=plan,
            )
            if backup is not None:
                record = record.model_copy(update={"backupPath": str(backup)})
                local.save_installed_record(record)
            return record
        except Exception as exc:
            if backup is not None:
                raise RuntimeError(f"Update failed; backup preserved at {backup}: {exc}") from exc
            raise


async def _install_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    *,
    scope: str = "global",
    installed_by: str | None = None,
    progress: InstallProgressCallback | None = None,
    protection_plan: dict | None = None,
) -> InstalledPluginRecord:
    manifest = load_manifest(plugin_type, plugin_id)
    # Match the official manifest with its own payload. The preview resolver
    # prefers project overrides and can otherwise reinstall old/custom files
    # while recording the official release's new version.
    src = plugin_root(plugin_type, plugin_id, prefer_bundled=True)
    validate_package(src, manifest)
    dst = _resolve_install_destination(plugin_type, plugin_id, src, scope)
    access_dst = _contracts_access_dir(plugin_id, scope) if plugin_type == "webui" and (src / "access").is_dir() else None
    expected = None
    if protection_plan is not None:
        expected = next(item for item in protection_plan["items"] if (item["type"], item["id"]) == (plugin_type, plugin_id))
        if payload_hashes(plugin_type, dst, access_dst) != expected["_current"]:
            raise RuntimeError(f"Plugin files changed during update: {plugin_type}/{plugin_id}; retry the update")

    def verify_root(label: str, target: Path) -> None:
        if expected is None:
            return
        current = {label + "/" + key: value for key, value in tree_hashes(
            target, webui=plugin_type == "webui" and label == "package",
        ).items()}
        baseline = {key: value for key, value in expected["_current"].items() if key.startswith(label + "/")}
        if current != baseline:
            raise RuntimeError(f"Plugin files changed during update: {plugin_type}/{plugin_id}; retry the update")
        if payload_hashes(plugin_type, src, src / "access" if access_dst else None, source=True) != expected["_official"]:
            raise RuntimeError("Official package changed during update; retry the update")

    retained_roots = expected.get("_retainedRoots", {}) if expected is not None else {}
    component_key = f"component:{plugin_id}"
    component_ref_installs: list[tuple[PluginType, str]] = []
    previous_record = local.get_record(plugin_type, plugin_id)
    package_backup: Path | None = None
    package_replaced = False
    access_replacement: tuple[Path, Path | None] | None = None
    try:
        if plugin_type == "component":
            component_ref_installs = await _install_component_refs(manifest, scope=scope, progress=progress, protection_plan=protection_plan)
        if plugin_type == "webui":
            package_backup = _copy_webui_package_with_build(
                plugin_id,
                src,
                dst,
                retain_backup=True,
                before_replace=lambda: verify_root("package", dst),
                backup_destination=retained_roots.get("package"),
            )
        else:
            package_backup = _copy_package(
                src, dst, retain_backup=True, before_replace=lambda: verify_root("package", dst),
                backup_destination=retained_roots.get("package"),
            )
        package_replaced = True
        access_replacement = _copy_attached_access_contracts(
            plugin_type,
            plugin_id,
            src,
            scope,
            retain_backup=True,
            before_replace=lambda: verify_root("access", access_dst),
            backup_destination=retained_roots.get("access"),
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
        if access_replacement is not None and "access" not in retained_roots:
            _commit_replacement(access_replacement[1])
        if "package" not in retained_roots:
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


async def update_plugin(
    plugin_type: PluginType, plugin_id: str, *, scope: str = "global",
    confirmation_token: str | None = None, confirm_changes: bool = False,
) -> InstalledPluginRecord:
    previous_record = local.get_record(plugin_type, plugin_id)
    installed_by = previous_record.installedBy if previous_record and previous_record.scope == scope else None
    return await install_plugin(plugin_type, plugin_id, scope=scope, installed_by=installed_by,
                                confirmation_token=confirmation_token, confirm_changes=confirm_changes)


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
    async with mutation_lock():
        return await _uninstall_plugin(plugin_type, plugin_id)


async def _uninstall_plugin(plugin_type: PluginType, plugin_id: str) -> bool:
    _validate_uninstall_plugin_id(plugin_id)
    manifest = load_manifest(plugin_type, plugin_id) if plugin_type == "component" else None
    record = local.get_record(plugin_type, plugin_id)
    install_path = Path(record.installPath) if record and record.installPath else local.infer_local_install(plugin_type, plugin_id)
    scope = record.scope if record else "global"
    if install_path is not None:
        await asyncio.to_thread(_validate_uninstall_target, plugin_type, plugin_id, install_path, scope)
    if plugin_type == "webui" and scope != "project":
        await asyncio.to_thread(_validate_access_uninstall_target, plugin_id, scope)
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
        # A missing page payload can still leave its access contracts behind.
        # Keep project-managed contracts protected even when their pages vanished.
        access_removed = False
        if plugin_type == "webui" and (record is None or record.scope != "project"):
            access_removed = await asyncio.to_thread(
                _remove_attached_access_contracts, plugin_type, plugin_id, "global"
            )
        local.remove_installed_record(plugin_type, plugin_id)
        clear_catalog_caches()
        _clear_device_template_cache_if_needed(plugin_type)
        if children_removed or had_record or access_removed:
            await _refresh_runtime(plugin_type, changed_path)
        return children_removed or had_record or access_removed
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
