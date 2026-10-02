"""Install local extension files in the layout consumed by PluginLoader."""
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional

import typer

plugin_app = typer.Typer(help="List and install local Flocks plugins", no_args_is_help=True)


class PluginKind(str, Enum):
    agents = "agents"
    tools = "tools"
    hooks = "hooks"
    tasks = "tasks"


def plugin_root(root: Optional[Path]) -> Path:
    from flocks.plugin import DEFAULT_PLUGIN_ROOT
    return (root or DEFAULT_PLUGIN_ROOT).expanduser().resolve()


def list_plugins(root: Path) -> list[dict]:
    from flocks.plugin import scan_directory
    result = []
    for kind in PluginKind:
        for source in scan_directory(root / kind.value, recursive=kind in {PluginKind.tools, PluginKind.hooks}, max_depth=2):
            path = Path(source)
            result.append({"kind": kind.value, "name": path.name, "path": str(path)})
    return result


@plugin_app.command("list")
def plugin_list(root: Optional[Path] = typer.Option(None, "--root", help="Plugin root (default: user plugin directory)")):
    typer.echo(json.dumps(list_plugins(plugin_root(root)), ensure_ascii=False))


def install_local(source: Path, root: Path, kind: PluginKind) -> list[Path]:
    """Stage files, refuse overwrites/symlinks, and roll back partial installs."""
    from flocks.plugin import scan_directory
    if source.is_symlink():
        raise ValueError("Symlink plugin sources are not supported")
    if source.is_file():
        if source.suffix not in {".py", ".yaml", ".yml"} or source.name.startswith("_"):
            raise ValueError("Plugin files must be discoverable .py/.yaml/.yml files")
        files = [source]
        base = source.parent
    elif source.is_dir():
        files = []
        for path in source.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlink plugin sources are not supported")
            if path.is_file() and "__pycache__" not in path.relative_to(source).parts and path.suffix != ".pyc":
                files.append(path)
        entries = scan_directory(source, recursive=kind in {PluginKind.tools, PluginKind.hooks}, max_depth=2)
        if not entries:
            raise ValueError("No discoverable plugin files (.py/.yaml/.yml)")
        files.sort()
        base = source
    else:
        raise ValueError(f"Plugin source not found: {source}")
    if not files:
        raise ValueError("No discoverable plugin files (.py/.yaml/.yml)")
    targets = [root / kind.value / p.relative_to(base) for p in files]
    for target in targets:
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Plugin already exists: {target}")
        for parent in target.parents:
            if parent.is_symlink():
                raise ValueError(f"Symlink plugin destination: {parent}")
    root.mkdir(parents=True, exist_ok=True)
    installed = []
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as staging:
        staged = []
        for index, path in enumerate(files):
            target = Path(staging) / str(index)
            shutil.copy2(path, target)
            staged.append(target)
        try:
            for path, target in zip(staged, targets):
                target.parent.mkdir(parents=True, exist_ok=True)
                # Same-filesystem hard link publishes a complete file atomically
                # and refuses overwrites, including simultaneous installations.
                os.link(path, target)
                installed.append(target)
        except BaseException:
            for target in installed:
                target.unlink(missing_ok=True)
            raise
    return installed


@plugin_app.command("install")
def plugin_install(
    source: Path = typer.Argument(..., help="Local plugin file or extension directory"),
    kind: PluginKind = typer.Option(..., "--kind", help="Destination extension point"),
    root: Optional[Path] = typer.Option(None, "--root", help="Explicit plugin root; useful for isolated installations"),
):
    """Copy local plugins without executing them or changing configuration."""
    try:
        paths = install_local(source.expanduser(), plugin_root(root), kind)
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps({"installed": [str(p) for p in paths]}, ensure_ascii=False))
