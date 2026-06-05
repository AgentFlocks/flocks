"""Loader for user-defined trigger plugin specs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

from flocks.workflow.fs_store import find_workspace_root

try:  # pragma: no cover - optional dependency fallback
    import yaml
except Exception:  # pragma: no cover - fallback branch
    yaml = None

PLUGIN_FILENAMES = ("trigger.json", "trigger.yaml", "trigger.yml", "manifest.json")


def trigger_plugin_roots() -> List[Path]:
    workspace = find_workspace_root()
    return [
        Path.home() / ".flocks" / "plugins" / "triggers",
        workspace / ".flocks" / "plugins" / "triggers",
    ]


def _read_plugin_manifest(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        if yaml is None:
            return None
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_trigger_plugins() -> List[Dict[str, Any]]:
    plugins: Dict[str, Dict[str, Any]] = {}
    for root in trigger_plugin_roots():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = next((entry / filename for filename in PLUGIN_FILENAMES if (entry / filename).is_file()), None)
            if manifest_path is None:
                continue
            manifest = _read_plugin_manifest(manifest_path)
            if not isinstance(manifest, dict):
                continue
            plugin_id = str(manifest.get("id") or entry.name).strip() or entry.name
            plugins[plugin_id] = {
                "id": plugin_id,
                "name": manifest.get("name") or plugin_id,
                "description": manifest.get("description"),
                "root": str(entry),
                "manifestPath": str(manifest_path),
                "handlerPath": str(entry / "handler.py"),
                "manifest": manifest,
            }
    return list(plugins.values())


def load_trigger_plugin_module(plugin_spec: Dict[str, Any]) -> Optional[ModuleType]:
    handler_path = Path(str(plugin_spec.get("handlerPath") or "")).expanduser()
    if not handler_path.is_file():
        return None
    module_name = f"flocks_trigger_plugin_{plugin_spec.get('id', handler_path.stem)}"
    spec = importlib.util.spec_from_file_location(module_name, handler_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
