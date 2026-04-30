"""File tree and preview helpers for bundled Hub plugins."""

from __future__ import annotations

import hashlib
from pathlib import Path

from flocks.hub.models import HubFileContent, HubFileNode, PluginType
from flocks.hub.paths import get_bundled_hub_root, safe_relative_path


_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".sh",
    ".toml",
    ".ini",
    ".cfg",
}
_MAX_PREVIEW_BYTES = 512_000


def plugin_root(plugin_type: PluginType, plugin_id: str) -> Path:
    from flocks.hub.catalog import manifest_path, system_plugin_root

    system_root = system_plugin_root(plugin_type, plugin_id)
    if system_root is not None:
        return system_root.resolve()

    root = manifest_path(plugin_type, plugin_id).parent.resolve()
    bundled = get_bundled_hub_root().resolve()
    if bundled not in root.parents and root != bundled:
        raise ValueError("plugin path escapes hub root")
    return root


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _previewable(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS and path.stat().st_size <= _MAX_PREVIEW_BYTES


def _node(path: Path, base: Path) -> HubFileNode:
    rel = "" if path == base else path.relative_to(base).as_posix()
    if path.is_dir():
        children = [
            _node(child, base)
            for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            if child.name != "__pycache__"
        ]
        return HubFileNode(name=path.name, path=rel, type="directory", children=children)
    return HubFileNode(
        name=path.name,
        path=rel,
        type="file",
        size=path.stat().st_size,
        checksum=_checksum(path),
        previewable=_previewable(path),
    )


def file_tree(plugin_type: PluginType, plugin_id: str) -> HubFileNode:
    root = plugin_root(plugin_type, plugin_id)
    if not root.is_dir():
        raise FileNotFoundError(f"Hub plugin not found: {plugin_type}/{plugin_id}")
    return _node(root, root)


def _language(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return {
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".py": "python",
        ".sh": "shell",
        ".toml": "toml",
    }.get(suffix)


def read_file_content(plugin_type: PluginType, plugin_id: str, rel_path: str) -> HubFileContent:
    base = plugin_root(plugin_type, plugin_id)
    safe_rel = safe_relative_path(rel_path)
    path = (base / safe_rel).resolve()
    if base not in path.parents and path != base:
        raise ValueError("file path escapes plugin root")
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    if not _previewable(path):
        raise ValueError("file is not previewable")
    return HubFileContent(
        path=safe_rel.as_posix(),
        content=path.read_text(encoding="utf-8", errors="replace"),
        size=path.stat().st_size,
        checksum=_checksum(path),
        language=_language(path),
    )
