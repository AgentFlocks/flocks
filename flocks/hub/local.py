"""Local installed-plugin discovery for Hub."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from flocks.config.config import Config
from flocks.hub.models import InstalledPluginRecord, PluginType
from flocks.project.instance import Instance


def _user_plugins_root() -> Path:
    return Path.home() / ".flocks" / "plugins"


def _project_plugins_root() -> Path:
    project_dir = Instance.get_directory() or Path.cwd()
    return Path(project_dir) / ".flocks" / "plugins"


def install_root(plugin_type: PluginType, scope: str = "global") -> Path:
    root = _project_plugins_root() if scope == "project" else _user_plugins_root()
    if plugin_type == "skill":
        return root / "skills"
    if plugin_type == "agent":
        return root / "agents"
    if plugin_type == "workflow":
        return root / "workflows"
    return root / "tools"


def install_dir(plugin_type: PluginType, plugin_id: str, scope: str = "global") -> Path:
    return install_root(plugin_type, scope) / plugin_id


def _record_path() -> Path:
    return Config.get_data_path() / "hub" / "installed.json"


def load_installed_records() -> dict[str, InstalledPluginRecord]:
    import json

    path = _record_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    records = raw.get("plugins", raw)
    if not isinstance(records, dict):
        return {}
    result: dict[str, InstalledPluginRecord] = {}
    for key, value in records.items():
        if not isinstance(value, dict):
            continue
        try:
            result[key] = InstalledPluginRecord.model_validate(value)
        except Exception:
            continue
    return result


def save_installed_record(record: InstalledPluginRecord) -> None:
    import json

    path = _record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = load_installed_records()
    records[f"{record.type}:{record.id}"] = record
    payload = {"plugins": {key: value.model_dump(mode="json") for key, value in records.items()}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def remove_installed_record(plugin_type: PluginType, plugin_id: str) -> None:
    import json

    path = _record_path()
    records = load_installed_records()
    records.pop(f"{plugin_type}:{plugin_id}", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"plugins": {key: value.model_dump(mode="json") for key, value in records.items()}}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def has_install_payload(plugin_type: PluginType, path: Path) -> bool:
    if not path.exists():
        return False
    if plugin_type == "skill":
        return (path / "SKILL.md").is_file()
    if plugin_type == "agent":
        return (path / "agent.yaml").is_file()
    if plugin_type == "workflow":
        return (path / "workflow.json").is_file() or (path / "workflow.md").is_file()
    if plugin_type == "tool":
        if path.is_file():
            return path.suffix in {".yaml", ".yml", ".py"}
        return any(candidate.is_file() for candidate in path.rglob("*.yaml")) or any(
            candidate.is_file() and candidate.name != "__init__.py"
            for candidate in path.rglob("*.py")
        )
    return path.exists()


def get_record(plugin_type: PluginType, plugin_id: str) -> Optional[InstalledPluginRecord]:
    return load_installed_records().get(f"{plugin_type}:{plugin_id}")


def make_record(
    *,
    plugin_type: PluginType,
    plugin_id: str,
    version: str,
    source: str,
    install_path: Path,
    enabled: bool = True,
    scope: str = "global",
) -> InstalledPluginRecord:
    return InstalledPluginRecord(
        id=plugin_id,
        type=plugin_type,
        version=version,
        source=source,
        installedAt=int(time.time() * 1000),
        enabled=enabled,
        scope="project" if scope == "project" else "global",
        installPath=str(install_path),
    )


def infer_local_install(plugin_type: PluginType, plugin_id: str) -> Optional[Path]:
    for scope in ("global", "project"):
        path = install_dir(plugin_type, plugin_id, scope)
        if has_install_payload(plugin_type, path):
            return path
    if plugin_type == "tool":
        for base in (install_root("tool", "global"), install_root("tool", "project")):
            if not base.is_dir():
                continue
            for nested in (base / "api" / plugin_id, base / "mcp" / plugin_id, base / "generated" / plugin_id):
                if has_install_payload(plugin_type, nested):
                    return nested
            for candidate in base.rglob(f"{plugin_id}.yaml"):
                if has_install_payload(plugin_type, candidate.parent):
                    return candidate.parent
    return None
