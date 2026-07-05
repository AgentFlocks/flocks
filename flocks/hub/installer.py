"""Installer for bundled Hub plugins."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from flocks.hub import local
from flocks.hub.catalog import load_manifest
from flocks.hub.files import plugin_root
from flocks.hub.models import HubPluginManifest, InstalledPluginRecord, PluginType
from flocks.hub.security import SKIP_NAMES, validate_package


_TOOL_TYPE_DIRS = {"api", "device", "python", "mcp", "generated"}


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


def _copy_package(src: Path, dst: Path) -> None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        backup = None
        if dst.exists():
            backup = parent / f".{dst.name}.bak"
            if backup.exists():
                shutil.rmtree(backup)
            dst.replace(backup)
        tmp.replace(dst)
        if backup and backup.exists():
            shutil.rmtree(backup)
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


def _replace_dir(src: Path, dst: Path) -> None:
    parent = dst.parent
    backup = None
    if dst.exists():
        backup = parent / f".{dst.name}.bak"
        if backup.exists():
            shutil.rmtree(backup)
        dst.replace(backup)
    src.replace(dst)
    if backup and backup.exists():
        shutil.rmtree(backup)


def _contracts_access_dir(plugin_id: str, scope: str) -> Path:
    return local.install_root("webui", scope).parent / "access" / plugin_id


def _copy_attached_access_contracts(plugin_type: PluginType, plugin_id: str, src: Path, scope: str) -> Path | None:
    if plugin_type != "webui":
        return None
    access_src = src / "access"
    if not access_src.is_dir():
        return None
    access_dst = _contracts_access_dir(plugin_id, scope)
    _copy_package(access_src, access_dst)
    return access_dst


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


def _copy_webui_package_with_build(plugin_id: str, src: Path, dst: Path) -> None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        _copy_package_contents(src, tmp)
        _build_webui_pages(plugin_id, tmp)
        _replace_dir(tmp, dst)
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise


async def _refresh_runtime(plugin_type: PluginType) -> None:
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

        ToolRegistry.init()
        ToolRegistry.refresh_plugin_tools()
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


async def _install_component_refs(manifest: HubPluginManifest, *, scope: str) -> None:
    seen: set[tuple[PluginType, str]] = set()
    component_key = f"component:{manifest.id}"
    for ref in manifest.components:
        key = (ref.type, ref.id)
        if key in seen:
            continue
        seen.add(key)
        if ref.type == "component":
            raise ValueError("Nested Hub components are not supported")
        if local.infer_local_install(ref.type, ref.id) is not None:
            continue
        try:
            await install_plugin(ref.type, ref.id, scope=scope, installed_by=component_key)
        except Exception:
            if ref.optional:
                continue
            raise


async def _uninstall_component_refs(manifest: HubPluginManifest) -> None:
    component_key = f"component:{manifest.id}"
    seen: set[tuple[PluginType, str]] = set()
    for ref in reversed(manifest.components):
        key = (ref.type, ref.id)
        if key in seen or ref.type == "component":
            continue
        seen.add(key)
        record = local.get_record(ref.type, ref.id)
        if record is None or record.installedBy != component_key:
            continue
        try:
            await uninstall_plugin(ref.type, ref.id)
        except FileNotFoundError:
            local.remove_installed_record(ref.type, ref.id)
        except Exception:
            if ref.optional:
                continue
            raise


async def install_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    *,
    scope: str = "global",
    installed_by: str | None = None,
) -> InstalledPluginRecord:
    manifest = load_manifest(plugin_type, plugin_id)
    src = plugin_root(plugin_type, plugin_id)
    validate_package(src, manifest)
    if plugin_type == "component":
        await _install_component_refs(manifest, scope=scope)
    dst = _resolve_install_destination(plugin_type, plugin_id, src, scope)
    if plugin_type == "webui":
        _copy_webui_package_with_build(plugin_id, src, dst)
    else:
        _copy_package(src, dst)
    _copy_attached_access_contracts(plugin_type, plugin_id, src, scope)
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
    await _refresh_runtime(plugin_type)
    return record


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
        local.remove_installed_record(plugin_type, plugin_id)
        return False
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
    _cleanup_orphan_api_services(orphan_keys)
    await _refresh_runtime(plugin_type)
    return True
