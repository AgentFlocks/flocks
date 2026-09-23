"""Installer for bundled Hub plugins."""

from __future__ import annotations

import asyncio
import errno
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from flocks.config.config import Config
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


def _ensure_no_pending_backup(dst: Path) -> None:
    backup = dst.parent / f".{dst.name}.bak"
    if backup.exists() or backup.is_symlink():
        raise RuntimeError(
            f"Plugin recovery is required before retrying: original data remains at {backup}; "
            f"current installation is at {dst}. Recover the previous installation before continuing."
        )


def _purge_stale_scratch(parent: Path, name: str) -> None:
    """Discard prepared trees only after ruling out an unfinished rollback.

    A .bak directory may be the sole old copy when retaining the replacement
    failed (for example across filesystems). Never classify it as scratch,
    even when the runtime failure has cleared on a later attempt.
    """
    _ensure_no_pending_backup(parent / name)
    if not parent.is_dir():
        return
    for entry in parent.iterdir():
        prefix = f".{name}."
        suffix = entry.name[len(prefix):] if entry.name.startswith(prefix) else ""
        # mkdtemp adds one suffix without dots. A dotted remainder may belong
        # to another legal plugin ID (e.g. .plugin.extra.bak), not this install.
        if not suffix or "." in suffix or suffix == "bak":
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
    _ensure_no_pending_backup(dst)
    backup: Path | None = None
    if dst.exists() or dst.is_symlink():
        backup = backup_destination if backup_destination is not None else dst.parent / f".{dst.name}.bak"
        if backup_destination is not None:
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


def _new_recovery_root() -> Path:
    return Config.get_data_path() / "hub" / "backups" / ("rollback-" + uuid.uuid4().hex) / "failed"


def _rollback_replacement(
    dst: Path, backup: Path | None, *, recovery_root: Path | None = None,
) -> None:
    if backup is not None and not (backup.exists() or backup.is_symlink()):
        raise RuntimeError(f"Rollback stopped: original directory is missing at {backup}; kept {dst}")
    preserved = None
    if dst.exists() or dst.is_symlink():
        root = recovery_root if recovery_root is not None else _new_recovery_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Unique containers avoid replacing an earlier recovery attempt. The
        # metadata is written before moving anything; failures leave dst intact.
        container = Path(tempfile.mkdtemp(prefix="tree-", dir=root))
        preserved = container / "content"
        (container / "recovery.json").write_text(
            json.dumps({"installPath": str(dst), "originalBackup": str(backup) if backup else None}, indent=2),
            encoding="utf-8",
        )
        try:
            _replace_with_retry(dst, preserved)
        except OSError as exc:
            raise RuntimeError(f"Rollback stopped; kept current directory at {dst}: {exc}") from exc
    if backup is not None:
        try:
            _replace_with_retry(backup, dst)
        except OSError as exc:
            raise RuntimeError(
                f"Rollback restore failed; current content preserved at {preserved}; "
                f"original directory remains at {backup}: {exc}"
            ) from exc


def _copy_package(
    src: Path,
    dst: Path,
    *,
    retain_backup: bool = False,
    plugin_type: PluginType | None = None,
    scope: str = "global",
    before_replace: Callable[[], None] | None = None,
    backup_destination: Path | None = None,
) -> Path | None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    _purge_stale_scratch(parent, dst.name)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        _preserve_native_group_metadata(plugin_type, dst, tmp, scope=scope)
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


def _preserve_native_group_metadata(
    plugin_type: PluginType | None, previous: Path, prepared: Path, *, scope: str,
) -> None:
    """Retain one editable native attribute in the existing package staging step.

    Configuration-backed groups and Device instance records are outside package
    replacement. No group inventory or membership data is created here.
    """
    if plugin_type == "skill":
        from flocks.skill.skill import Skill

        new_file = prepared / "SKILL.md"
        if new_file.is_file():
            content = new_file.read_bytes().decode("utf-8")
            preserved = Skill.preserve_install_group(
                previous / "SKILL.md", content, scope=scope, name=previous.name,
            )
            if preserved != content:
                new_file.write_bytes(preserved.encode("utf-8"))
        return

    if plugin_type == "workflow":
        from flocks.workflow.fs_store import (
            is_system_workflow_definition, normalize_workflow_group, patch_workflow_metadata,
        )

        if is_system_workflow_definition(previous):
            current = json.loads((previous / "meta.json").read_text(encoding="utf-8"))
            fixed_group = normalize_workflow_group(current.get("group"))
            for path in (prepared / "meta.json", prepared / "workflow.json"):
                if not path.is_file():
                    continue
                incoming = json.loads(path.read_text(encoding="utf-8"))
                if path.name == "workflow.json":
                    incoming = incoming.get("metadata", {})
                if "group" in incoming and normalize_workflow_group(incoming["group"]) != fixed_group:
                    raise ValueError("System workflow group is read-only")
            patch_workflow_metadata(prepared, {"group": fixed_group}, workflow_id=previous.name)
            return
        if previous.is_symlink():
            return
        old_meta = previous / "meta.json"
        if old_meta.is_file() and not old_meta.is_symlink():
            metadata = json.loads(old_meta.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and "group" in metadata:
                patch_workflow_metadata(prepared, {"group": metadata["group"]}, workflow_id=previous.name)
                return
        # Package interchange may carry the default in workflow JSON. Once
        # installed, meta.json remains the sole locally editable authority.
        new_meta = prepared / "meta.json"
        if new_meta.is_file():
            metadata = json.loads(new_meta.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and "group" in metadata:
                return
        definition = prepared / "workflow.json"
        if definition.is_file():
            metadata = json.loads(definition.read_text(encoding="utf-8")).get("metadata", {})
            if isinstance(metadata, dict) and "group" in metadata:
                patch_workflow_metadata(prepared, {"group": metadata["group"]}, workflow_id=previous.name)
        return

    if plugin_type not in {"agent", "tool"}:
        return
    import yaml

    if plugin_type == "agent":
        from flocks.agent.agent import normalize_agent_group
        from flocks.agent.agent_factory import is_system_agent_definition

        old_file, new_file = previous / "agent.yaml", prepared / "agent.yaml"
        if not old_file.is_file() or not new_file.is_file():
            return
        protected = is_system_agent_definition(old_file)
        if not protected and (previous.is_symlink() or old_file.is_symlink()):
            return
        old_data = yaml.safe_load(old_file.read_text(encoding="utf-8"))
        if not isinstance(old_data, dict) or "group" not in old_data:
            return
        new_data = yaml.safe_load(new_file.read_text(encoding="utf-8"))
        if not isinstance(new_data, dict):
            raise ValueError("Expected native agent metadata")
        try:
            value = normalize_agent_group(old_data["group"])
        except ValueError as exc:
            raise ValueError("Invalid group metadata in agent.yaml") from exc
        if protected and "group" in new_data and normalize_agent_group(new_data["group"]) != value:
            raise ValueError("System agent group is read-only")
        new_data["group"] = value
        new_file.write_text(yaml.safe_dump(new_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return

    from flocks.tool.registry import is_shipped_tool_path, normalize_tool_group

    old_tools = _native_yaml_tools(previous, previous, scope=scope)
    new_tools = _native_yaml_tools(prepared, previous, scope=scope)
    for name, (old_file, old_data) in old_tools.items():
        protected = is_shipped_tool_path(old_file)
        if name not in new_tools:
            if protected:
                raise ValueError(f"System tool '{name}' cannot be removed by installation")
            continue
        if "group" not in old_data or (previous.is_symlink() and not protected):
            continue
        new_file, new_data = new_tools[name]
        value = normalize_tool_group(old_data["group"])
        if protected and "group" in new_data and normalize_tool_group(new_data["group"]) != value:
            raise ValueError(f"System tool '{name}' group is read-only")
        new_data["group"] = value
        new_file.write_text(yaml.safe_dump(new_data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _native_yaml_tools(package: Path, destination: Path, *, scope: str) -> dict[str, tuple[Path, dict]]:
    """Read only discoverable YAML tool identities, without importing handlers.

    Native discovery scans tools/ at depth two, so an api/provider install has
    no remaining child depth while a flat package has one. Staging directory
    names do not change that budget or the top-level subsystem exclusions.
    """
    import yaml
    from flocks.plugin.loader import scan_directory

    try:
        relative = destination.resolve().relative_to(local.install_root("tool", scope).resolve())
    except ValueError:
        # Direct package-copy callers may specify another project's native
        # target rather than the active request's install root.
        native_root = next((
            parent for parent in destination.parents
            if parent.name == "tools" and parent.parent.name == "plugins" and parent.parent.parent.name == ".flocks"
        ), None)
        if native_root is not None:
            relative = destination.relative_to(native_root)
        else:
            relative = Path(destination.parent.name, destination.name) if destination.parent.name in _TOOL_TYPE_DIRS else Path(destination.name)
    if any(part.startswith("_") for part in relative.parts) or (relative.parts and relative.parts[0] in {"mcp", "generated"}):
        return {}
    depth = 2 - len(relative.parts)
    if depth < 0:
        return {}
    tools: dict[str, tuple[Path, dict]] = {}
    for filename in scan_directory(
        package, recursive=True, max_depth=depth,
        exclude_subdirs={"mcp", "generated"} if not relative.parts else set(),
    ):
        path = Path(filename)
        if path.suffix not in {".yaml", ".yml"} or path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue  # Invalid YAML is not loaded as a native tool either.
        if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not data["name"]:
            continue
        if not any(isinstance(data.get(key), dict) and data[key] for key in ("handler", "execution")):
            continue  # Config/provider/fixture YAML is not tool metadata.
        tools.setdefault(data["name"], (path, data))  # Native duplicate rule: first wins.
    return tools


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


def _remove_attached_access_contracts(
    plugin_type: PluginType, plugin_id: str, scope: str, *, recovery_root: Path | None = None,
) -> bool:
    if plugin_type != "webui":
        return False
    access_dst = _validate_access_uninstall_target(plugin_id, scope)
    if recovery_root is not None and (access_dst.exists() or access_dst.is_symlink()):
        _rollback_replacement(access_dst, None, recovery_root=recovery_root / "access")
    elif access_dst.is_dir():
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
    *, recovery_root: Path | None = None,
) -> None:
    recovery_root = recovery_root if recovery_root is not None else _new_recovery_root()
    errors: list[str] = []
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
            await _uninstall_plugin(
                plugin_type, plugin_id, recovery_root=recovery_root / plugin_type / plugin_id,
            )
        except Exception as exc:
            errors.append(f"{plugin_type}/{plugin_id}: {exc}")
    if errors:
        raise RuntimeError("Dependency rollback incomplete; " + "; ".join(errors))


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
        await _rollback_component_ref_installs(
            rollback_refs, component_key, recovery_root=(protection_plan or {}).get("_recoveryRoot"),
        )
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
        # Block the whole operation before replacing any suite child when an
        # earlier failure left an unresolved original in a temporary backup.
        for item in plan["items"]:
            for target in item["_roots"].values():
                _ensure_no_pending_backup(Path(target))
        if (confirmation_token is not None and confirmation_token != plan["token"]) or (
            plan["requiresConfirmation"] and (not confirm_changes or confirmation_token != plan["token"])
        ):
            raise UpdateConfirmationRequired(public_plan(plan))
        try:
            backup = await asyncio.to_thread(create_backup, plan) if plan["requiresConfirmation"] else None
        except Exception as exc:
            raise RuntimeError(f"Backup failed; update cancelled: {exc}") from exc
        recovery_root = backup / "failed" if backup is not None else _new_recovery_root()
        plan["_recoveryRoot"] = recovery_root
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
            saved_backup = backup if backup is not None else recovery_root.parent if recovery_root.exists() else None
            if saved_backup is not None:
                raise RuntimeError(f"Update failed; backup preserved at {saved_backup}: {exc}") from exc
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
    if plugin_type == "component" and plugin_id == "host-security-monitor":
        from flocks.monitoring.lifecycle import validate_manifest
        validate_manifest(manifest, src)
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
    recovery_root = (protection_plan or {}).get("_recoveryRoot") or _new_recovery_root()
    component_key = f"component:{plugin_id}"
    component_ref_installs: list[tuple[PluginType, str]] = []
    previous_record = local.get_record(plugin_type, plugin_id)
    monitor_registration_started = False
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
                src, dst, retain_backup=True, plugin_type=plugin_type, scope=scope,
                before_replace=lambda: verify_root("package", dst),
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
        if plugin_type == "component" and plugin_id == "host-security-monitor":
            from flocks.monitoring.lifecycle import install
            monitor_registration_started = True
            await install(manifest)
        if plugin_type == "component":
            await _emit_component_progress(progress, manifest, "complete", record=record, message="Installed")
        if access_replacement is not None and "access" not in retained_roots:
            _commit_replacement(access_replacement[1])
        if "package" not in retained_roots:
            _commit_replacement(package_backup)
        return record
    except Exception:
        if monitor_registration_started:
            from flocks.monitoring.lifecycle import compensate_install_failure
            await compensate_install_failure(previous_record is not None)
        if access_replacement is not None:
            _rollback_replacement(
                *access_replacement, recovery_root=recovery_root / plugin_type / plugin_id / "access",
            )
        if package_replaced:
            _rollback_replacement(
                dst, package_backup, recovery_root=recovery_root / plugin_type / plugin_id / "package",
            )
        if previous_record is None:
            local.remove_installed_record(plugin_type, plugin_id)
        else:
            local.save_installed_record(previous_record)
        clear_catalog_caches()
        if plugin_type == "component":
            await _rollback_component_ref_installs(
                component_ref_installs, component_key, recovery_root=recovery_root,
            )
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


async def _uninstall_plugin(
    plugin_type: PluginType, plugin_id: str, *, recovery_root: Path | None = None,
) -> bool:
    _validate_uninstall_plugin_id(plugin_id)
    if plugin_type == "component" and plugin_id == "host-security-monitor":
        from flocks.monitoring.lifecycle import uninstall
        await uninstall()
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
                _remove_attached_access_contracts, plugin_type, plugin_id, "global", recovery_root=recovery_root,
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
    if recovery_root is not None:
        _rollback_replacement(install_path, None, recovery_root=recovery_root / "package")
    elif install_path.is_dir():
        shutil.rmtree(install_path)
    else:
        install_path.unlink()
    _remove_attached_access_contracts(
        plugin_type, plugin_id, record.scope if record else "global", recovery_root=recovery_root,
    )
    local.remove_installed_record(plugin_type, plugin_id)
    clear_catalog_caches()
    _cleanup_orphan_api_services(orphan_keys)
    await _refresh_runtime(plugin_type, install_path)
    return True
