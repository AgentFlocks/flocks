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
    if plugin_type == "device":
        # Device plugins live as a subdirectory of tools/ so the runtime
        # tool loader (which expects ``<plugins>/tools/<group>/<id>/``)
        # picks them up alongside api/ and python/ groups.
        return root / "tools" / "device"
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
    if plugin_type in {"tool", "device"}:
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
            for nested in (
                base / "api" / plugin_id,
                base / "device" / plugin_id,
                base / "mcp" / plugin_id,
                base / "generated" / plugin_id,
            ):
                if has_install_payload(plugin_type, nested):
                    return nested
            for candidate in base.rglob(f"{plugin_id}.yaml"):
                if has_install_payload(plugin_type, candidate.parent):
                    return candidate.parent
    if plugin_type == "device":
        # Device installs live under ``<tools>/device/<id>/``. We already
        # checked the canonical path above via ``install_dir``; the loop
        # here catches legacy installs that may have been written into
        # the bare ``<tools>/<id>/`` location before ``device`` became
        # a first-class plugin type.
        for base in (install_root("tool", "global"), install_root("tool", "project")):
            legacy = base / plugin_id
            if has_install_payload("tool", legacy):
                return legacy
    return None


def infer_local_installs() -> dict[tuple[PluginType, str], Path]:
    """Scan installed plugin roots once and return plugin id -> install path."""
    result: dict[tuple[PluginType, str], Path] = {}

    for plugin_type in ("skill", "agent", "workflow"):
        for scope in ("global", "project"):
            base = install_root(plugin_type, scope)
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if child.is_dir() and has_install_payload(plugin_type, child):
                    result.setdefault((plugin_type, child.name), child)

    for scope in ("global", "project"):
        base = install_root("tool", scope)
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and has_install_payload("tool", child):
                result.setdefault(("tool", child.name), child)
        # Tools live under ``<tools>/<group>/<id>/`` where ``group`` is
        # one of api/device/mcp/generated. ``device`` is a first-class
        # plugin type on the Hub layer (driven by ``integration_type:
        # device`` in ``_provider.yaml``), so we surface those entries
        # keyed as ``("device", id)`` instead of ``("tool", id)`` to keep
        # the catalog state in sync with the runtime install path.
        for group in ("api", "device", "mcp", "generated"):
            group_dir = base / group
            if not group_dir.is_dir():
                continue
            entry_type: PluginType = "device" if group == "device" else "tool"
            for child in group_dir.iterdir():
                if child.is_dir() and has_install_payload("tool", child):
                    result.setdefault((entry_type, child.name), child)
        for candidate in base.rglob("*"):
            if not candidate.is_file() or candidate.name == "__init__.py":
                continue
            if candidate.suffix not in {".yaml", ".yml", ".py"}:
                continue
            if has_install_payload("tool", candidate.parent):
                # Preserve the directory-derived classification populated
                # above; never downgrade a previously-classified device
                # entry back to tool just because we found another yaml
                # in the same package.
                stem_key = candidate.stem
                if (("device", stem_key) in result) or (("tool", stem_key) in result):
                    continue
                result.setdefault(("tool", stem_key), candidate.parent)

    return result
