"""Local installed-plugin discovery for Hub."""

from __future__ import annotations

from flocks.hub.diagnostics import record_handled_error, timed

from contextlib import contextmanager
from contextvars import ContextVar

import time
from pathlib import Path
from typing import Optional

from flocks.config.config import Config
from flocks.hub.models import InstalledPluginRecord, PluginType
from flocks.project.instance import Instance


_payload_cache = ContextVar("hub_payload_snapshot", default=None)


@contextmanager
def discovery_scope():
    token = _payload_cache.set({})
    try:
        yield
    finally:
        _payload_cache.reset(token)


def _remember_tree(tree):
    cache = _payload_cache.get()
    if cache is not None:
        for kind in ("tool", "device"):
            cache.update(((kind, path), found) for path, found in tree.payload.items())
            cache.update(((kind, path), True) for path in tree.files)
    return tree


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
    if plugin_type == "webui":
        return root / "contracts" / "webui"
    if plugin_type == "component":
        return root / "components"
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


@timed("local.load_installed_records", detail=False)
def load_installed_records() -> dict[str, InstalledPluginRecord]:
    import json

    path = _record_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        record_handled_error("local.load_installed_records", exc)
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


@timed("local.has_install_payload", detail=True)
def has_install_payload(plugin_type: PluginType, path: Path) -> bool:
    cache = _payload_cache.get()
    key = (plugin_type, path)
    if cache is not None and key in cache:
        return cache[key]
    found = _probe_install_payload(plugin_type, path)
    if cache is not None:
        cache[key] = found
    return found


def _probe_install_payload(plugin_type: PluginType, path: Path) -> bool:
    if not path.exists():
        return False
    if plugin_type == "skill":
        return (path / "SKILL.md").is_file()
    if plugin_type == "agent":
        return (path / "agent.yaml").is_file()
    if plugin_type == "workflow":
        return (path / "workflow.json").is_file() or (path / "workflow.md").is_file()
    if plugin_type == "webui":
        if path.is_file():
            return False
        return (path / "workspace.json").is_file() or any(
            candidate.name == "manifest.json" and candidate.is_file()
            for candidate in path.rglob("manifest.json")
        )
    if plugin_type == "component":
        return path.is_dir() and (path / "component.json").is_file()
    if plugin_type in {"tool", "device"}:
        if path.is_file():
            return path.suffix in {".yaml", ".yml", ".py"}
        return any(candidate.is_file() for candidate in path.rglob("*.yaml")) or any(
            candidate.is_file() and candidate.name != "__init__.py"
            for candidate in path.rglob("*.py")
        )
    return path.exists()


@timed("local.installed_payload_version", detail=True)
def installed_payload_version(plugin_type: PluginType, path: Path) -> Optional[str]:
    """Read a version embedded in an installed payload when one is available."""
    if plugin_type != "webui" or not path.is_dir():
        return None

    import json

    workspace_path = path / "workspace.json"
    if not workspace_path.is_file():
        return None
    try:
        raw = json.loads(workspace_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    version = raw.get("version") if isinstance(raw, dict) else None
    if not isinstance(version, str) or not version.strip():
        return None
    return version.strip()


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
    installed_by: Optional[str] = None,
) -> InstalledPluginRecord:
    return InstalledPluginRecord(
        id=plugin_id,
        type=plugin_type,
        version=version,
        source=source,
        installedBy=installed_by,
        installedAt=int(time.time() * 1000),
        enabled=enabled,
        scope="project" if scope == "project" else "global",
        installPath=str(install_path),
    )


@timed("local.infer_local_install", detail=True)
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
                base / "python" / plugin_id,
            ):
                if has_install_payload(plugin_type, nested):
                    return nested
            for suffix in (".yaml", ".yml", ".py"):
                for candidate in base.rglob(f"{plugin_id}{suffix}"):
                    if has_install_payload(plugin_type, candidate):
                        return candidate
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


@timed("local.infer_local_installs", detail=False)
def infer_local_installs() -> dict[tuple[PluginType, str], Path]:
    """Scan installed plugin roots once and return plugin id -> install path."""
    result: dict[tuple[PluginType, str], Path] = {}

    for plugin_type in ("skill", "agent", "workflow", "webui", "component"):
        for scope in ("global", "project"):
            base = install_root(plugin_type, scope)
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if child.is_dir() and has_install_payload(plugin_type, child):
                    result.setdefault((plugin_type, child.name), child)

    from flocks.hub import tool_tree
    for scope in ("global", "project"):
        base = install_root("tool", scope)
        tree = _remember_tree(tool_tree.get(base))

        def present(path):
            # rglob does not follow nested directory symlinks. Explicit
            # canonical plugin roots, including linked roots, remain supported.
            if path in tree.links:
                return _remember_tree(tool_tree.get(path)).payload.get(path, False)
            return tree.payload.get(path, False)

        for child, is_dir, _ in tree.children.get(base, []):
            if is_dir and present(child):
                result.setdefault(("tool", child.name), child)
        for group in ("api", "device", "mcp", "generated", "python"):
            group_dir = base / group
            group_tree = _remember_tree(tool_tree.get(group_dir)) if group_dir in tree.links else tree
            entry_type = "device" if group == "device" else "tool"
            for child, is_dir, is_file in group_tree.children.get(group_dir, []):
                has_payload = (_remember_tree(tool_tree.get(child)).payload.get(child, False)
                               if child in group_tree.links else group_tree.payload.get(child, False))
                if is_dir and has_payload:
                    result.setdefault((entry_type, child.name), child)
                elif is_file and child.suffix in {".yaml", ".yml", ".py"}:
                    result.setdefault(("tool", child.stem), child)
        for candidate in tree.files:
            if candidate.name == "__init__.py" or not tree.payload.get(candidate.parent, False):
                continue
            stem = candidate.stem
            if ("device", stem) not in result:
                result.setdefault(("tool", stem), candidate.parent)

    return result


@timed("local.tool_discovery_signature", detail=False)
def tool_discovery_signature():
    """Watch nested install directories, including canonical linked roots."""
    from flocks.hub import tool_tree
    signatures = []
    for scope in ("global", "project"):
        base = install_root("tool", scope)
        tree = tool_tree.get(base)
        signatures.append(tree.signature)
        for child, is_dir, _ in tree.children.get(base, []):
            if not is_dir:
                continue
            subtree = tool_tree.get(child) if child in tree.links else tree
            if child in tree.links:
                signatures.append(subtree.signature)
            if child.name in {"api", "device", "mcp", "generated", "python"}:
                for nested, nested_dir, _ in subtree.children.get(child, []):
                    if nested_dir and nested in subtree.links:
                        signatures.append(tool_tree.get(nested).signature)
    return tuple(signatures)
