"""Installer for bundled Hub plugins."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from flocks.hub import local
from flocks.hub.catalog import load_manifest
from flocks.hub.files import plugin_root
from flocks.hub.models import InstalledPluginRecord, PluginType
from flocks.hub.security import validate_package


def _copy_package(src: Path, dst: Path) -> None:
    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.", dir=str(parent)))
    try:
        for item in src.iterdir():
            if item.name == "manifest.json":
                continue
            target = tmp / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
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
    elif plugin_type == "tool":
        from flocks.tool.registry import ToolRegistry

        ToolRegistry.init()
        ToolRegistry.refresh_plugin_tools()
    elif plugin_type == "workflow":
        try:
            from flocks.workflow.center import scan_skill_workflows

            await scan_skill_workflows()
        except Exception:
            pass


async def install_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    *,
    scope: str = "global",
) -> InstalledPluginRecord:
    manifest = load_manifest(plugin_type, plugin_id)
    src = plugin_root(plugin_type, plugin_id)
    validate_package(src, manifest)
    dst = local.install_dir(plugin_type, plugin_id, scope)
    _copy_package(src, dst)
    record = local.make_record(
        plugin_type=plugin_type,
        plugin_id=plugin_id,
        version=manifest.version,
        source=f"bundled:{manifest.source.path or ''}",
        install_path=dst,
        enabled=True,
        scope=scope,
    )
    local.save_installed_record(record)
    await _refresh_runtime(plugin_type)
    return record


async def update_plugin(plugin_type: PluginType, plugin_id: str, *, scope: str = "global") -> InstalledPluginRecord:
    return await install_plugin(plugin_type, plugin_id, scope=scope)


async def uninstall_plugin(plugin_type: PluginType, plugin_id: str) -> bool:
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
    shutil.rmtree(install_path)
    local.remove_installed_record(plugin_type, plugin_id)
    await _refresh_runtime(plugin_type)
    return True
