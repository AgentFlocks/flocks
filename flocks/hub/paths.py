"""Path helpers for the bundled Flocks Hub."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from flocks.project.instance import Instance


# Hub roots contributed by extensions (an extension can ship its scene suites
# this way). Each is shaped like the bundled root: ``index.json`` plus ``plugins/``.
# Their index entries join the catalog and replace bundled entries with the
# same type and id, so a placeholder listed by OSS can be backed by the real
# package once the extension is installed.
_EXTENSION_HUB_ROOTS: dict[str, Path] = {}


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("FLOCKS_HUB_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())

    project_dir = Instance.get_directory()
    if project_dir:
        roots.append(Path(project_dir) / ".flocks" / "flockshub")

    cwd = Path.cwd().resolve()
    roots.extend(candidate / ".flocks" / "flockshub" for candidate in [cwd, *cwd.parents])

    package_root = Path(__file__).resolve().parents[2]
    roots.append(package_root / ".flocks" / "flockshub")
    roots.append(package_root.parent / ".flocks" / "flockshub")
    return roots


def get_bundled_hub_root() -> Path:
    for root in _candidate_roots():
        if root.is_dir():
            return root
    return Path.cwd().resolve() / ".flocks" / "flockshub"


def register_hub_root(name: str, root: Path) -> None:
    """Register (or move) the extension hub root called *name*."""
    if not name:
        raise ValueError("hub root name cannot be empty")
    _EXTENSION_HUB_ROOTS[name] = Path(root).expanduser().resolve()


def unregister_hub_root(name: str) -> bool:
    return _EXTENSION_HUB_ROOTS.pop(name, None) is not None


def extension_hub_roots() -> list[Path]:
    """Registered extension roots that exist, in registration order."""
    return [root for root in _EXTENSION_HUB_ROOTS.values() if root.is_dir()]


def hub_roots() -> list[Path]:
    """Every root plugin files may come from: extensions first, then bundled."""
    return [*extension_hub_roots(), get_bundled_hub_root().resolve()]


def owning_hub_root(path: Path) -> Optional[Path]:
    """The hub root that contains *path*, or None when it lies outside all of them."""
    resolved = path.resolve()
    for root in hub_roots():
        if resolved == root or root in resolved.parents:
            return root
    return None


def safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be relative and cannot contain '..'")
    return path


def bundled_plugin_subdir(subdir: str) -> list[Path]:
    """Bundled flockshub plugin subdirectory roots for *subdir*.

    Returns a list (0 or 1 item) so callers can splat it into existing
    ``[user_root, project_root, *bundled_roots]`` collections without
    extra ``None`` checks. The bundled root is resolved lazily via
    :func:`get_bundled_hub_root` so callers picking up changes to
    ``FLOCKS_HUB_ROOT`` between runs do not see stale paths.
    """
    if not subdir:
        return []
    root = get_bundled_hub_root() / "plugins" / subdir
    return [root] if root.is_dir() else []


def bundled_tool_plugin_roots() -> list[Path]:
    """Tool-plugin search roots bundled inside flockshub.

    Returns directories shaped exactly like ``~/.flocks/plugins/tools/``
    (i.e. with ``api/``, ``python/`` etc. as immediate children). Used
    by the FlocksHub catalog to surface bundled tool plugins as
    ``available`` (pre-install) entries so users can discover and
    install them through the standard Hub flow.
    """
    return bundled_plugin_subdir("tools")
