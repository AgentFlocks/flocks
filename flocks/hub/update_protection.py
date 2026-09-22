"""Detect SOC customizations and retain complete backups before replacement."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock, Timeout

from flocks.config.config import Config
from flocks.hub import local
from flocks.hub.catalog import load_manifest
from flocks.hub.files import plugin_root
from flocks.hub.security import SKIP_NAMES


class UpdateConfirmationRequired(Exception):
    def __init__(self, plan: dict):
        super().__init__("Local changes require confirmation before replacement")
        self.plan = plan


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def tree_hashes(root: Path, *, webui: bool = False, skip_manifest: bool = False) -> dict[str, str]:
    """Hash user-controlled files, including added files and symlink targets."""
    if root.is_symlink():
        raise ValueError(f"Cannot update a symlinked plugin directory: {root}")
    if not root.exists():
        return {}
    if not root.is_dir():
        raise ValueError(f"Plugin path is not a directory: {root}")
    result = {}

    def fail_scan(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(root, followlinks=False, onerror=fail_scan):
        names[:] = sorted(
            n
            for n in names
            if n not in SKIP_NAMES and n != ".pytest_cache" and not (webui and n in {"dist", "node_modules"})
        )
        for name in sorted(files + [n for n in names if (Path(directory) / n).is_symlink()]):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if name in SKIP_NAMES or name.endswith((".pyc", ".pyo")) or (skip_manifest and rel == "manifest.json"):
                continue
            if path.is_symlink():
                result[rel] = "symlink:" + os.readlink(path)
            else:
                with path.open("rb") as stream:
                    result[rel] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def payload_hashes(plugin_type: str, root: Path, access: Path | None = None, *, source: bool = False) -> dict[str, str]:
    result = {
        "package/" + key: value
        for key, value in tree_hashes(
            root,
            webui=plugin_type == "webui",
            skip_manifest=source,
        ).items()
    }
    if access is not None:
        result.update({"access/" + key: value for key, value in tree_hashes(access, skip_manifest=source).items()})
    return result


def _soc_keys() -> set[tuple[str, str]]:
    keys = {
        ("component", "soc-workspace"),
        ("webui", "soc_ui"),
        ("tool", "soc_workspace_query"),
        ("workflow", "stream_alert_denoise"),
        ("workflow", "stream_alert_triage"),
    }
    try:
        manifest = load_manifest("component", "soc-workspace")
    except FileNotFoundError:
        # A missing suite manifest must not disable protection for its installed children.
        return keys
    return keys | {(ref.type, ref.id) for ref in manifest.components}


def build_plan(plugin_type: str, plugin_id: str, scope: str = "global") -> dict:
    from flocks.hub.installer import _component_ref_is_outdated, _resolve_install_destination

    if scope not in {"global", "project"}:
        raise ValueError("Invalid install scope")
    manifest = load_manifest(plugin_type, plugin_id)
    refs = [(plugin_type, plugin_id)]
    if plugin_type == "component":
        for ref in manifest.components:
            if ref.type == "component":
                raise ValueError("Nested Hub components are not supported")
            installed = local.infer_local_install(ref.type, ref.id)
            if installed is None or _component_ref_is_outdated(ref, installed, local.get_record(ref.type, ref.id)):
                refs.append((ref.type, ref.id))
    protected = _soc_keys()
    optional_keys = {(ref.type, ref.id) for ref in manifest.components if ref.optional}
    items = []
    for kind, identifier in dict.fromkeys(refs):
        try:
            release = load_manifest(kind, identifier)
            source = plugin_root(kind, identifier, prefer_bundled=True)
        except FileNotFoundError:
            if (kind, identifier) in optional_keys:
                # The installer already skips unavailable optional dependencies.
                continue
            raise
        target = _resolve_install_destination(kind, identifier, source, scope)
        access = (
            local.install_root("webui", scope).parent / "access" / identifier
            if kind == "webui" and (source / "access").is_dir()
            else None
        )
        current = payload_hashes(kind, target, access)
        official = payload_hashes(kind, source, source / "access" if access is not None else None, source=True)
        record = local.get_record(kind, identifier)
        matching_record = (
            record if record and record.installPath and Path(record.installPath).resolve() == target.resolve() else None
        )
        baseline = matching_record.fileHashes if matching_record else None
        version = matching_record.version if matching_record else local.installed_payload_version(kind, target)
        # An identical released version can provide the missing legacy baseline.
        # Never compare an old customized install with a DIFFERENT release.
        if baseline is None and version == release.version:
            baseline = official
        exists = target.exists() or (access is not None and access.exists())
        unknown = exists and baseline is None
        changes = []
        if baseline is not None:
            for path in sorted(current.keys() | baseline.keys()):
                if current.get(path) != baseline.get(path):
                    changes.append(
                        {
                            "path": path,
                            "kind": "added"
                            if path not in baseline
                            else "deleted"
                            if path not in current
                            else "modified",
                        }
                    )
        requires = (kind, identifier) in protected and exists and (unknown or bool(changes))
        items.append(
            {
                "type": kind,
                "id": identifier,
                "name": release.nameCn or release.name,
                "installedVersion": version,
                "version": release.version,
                "baselineKnown": not unknown,
                "changes": changes,
                "requiresConfirmation": requires,
                "_roots": {"package": str(target), **({"access": str(access)} if access is not None else {})},
                "_current": current,
                "_official": official,
                "_record": record.model_dump(mode="json") if record else None,
            }
        )
    token = _digest({"type": plugin_type, "id": plugin_id, "scope": scope, "items": items})
    return {
        "type": plugin_type,
        "id": plugin_id,
        "scope": scope,
        "token": token,
        "requiresConfirmation": any(item["requiresConfirmation"] for item in items),
        "items": items,
    }


def public_plan(plan: dict) -> dict:
    return {**plan, "items": [{k: v for k, v in item.items() if not k.startswith("_")} for item in plan["items"]]}


@asynccontextmanager
async def mutation_lock():
    directory = Config.get_data_path() / "hub"
    directory.mkdir(parents=True, exist_ok=True)
    lock = FileLock(directory / "install.lock", thread_local=False)
    deadline = time.monotonic() + 60
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise RuntimeError("Another plugin operation is still running; retry shortly")
            await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def create_backup(plan: dict) -> Path:
    root = Config.get_data_path() / "hub" / "backups"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    staging = root / ("." + name)
    destination = root / name
    staging.mkdir(mode=0o700)
    try:
        for index, item in enumerate(plan["items"]):
            for label, original in item["_roots"].items():
                path = Path(original)
                if path.is_symlink():
                    raise ValueError(f"Cannot back up symlinked plugin directory: {path}")
                if path.exists():
                    shutil.copytree(path, staging / str(index) / label, symlinks=True)
            copied = payload_hashes(
                item["type"],
                staging / str(index) / "package",
                staging / str(index) / "access" if "access" in item["_roots"] else None,
            )
            if copied != item["_current"]:
                raise RuntimeError("Files changed while creating backup; retry the update")
        (staging / "backup.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "plan": plan,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        staging.rename(destination)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
