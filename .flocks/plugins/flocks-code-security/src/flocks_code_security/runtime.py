"""Lazy process-local composition root for plugin services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from flocks_code_security.paths import (
    data_dir,
    ensure_private_directory,
    outputs_root,
    runtime_dir,
    snapshots_dir,
)
from flocks_code_security.snapshot import TargetSnapshotService
from flocks_code_security.source import AuditSourceRepository
from flocks_code_security.store import ScanStore


@dataclass(frozen=True)
class PluginRuntime:
    store: ScanStore
    snapshots: TargetSnapshotService
    source: AuditSourceRepository


_runtime: PluginRuntime | None = None
_lock = RLock()


def build_runtime(root: Path) -> PluginRuntime:
    store = ScanStore(root / "code-security.db")
    store.initialize()
    snapshot_service = TargetSnapshotService(
        root / "snapshots",
        store,
        protected_roots=(root, runtime_dir(), outputs_root()),
    )
    return PluginRuntime(
        store=store,
        snapshots=snapshot_service,
        source=AuditSourceRepository(store),
    )


def get_runtime() -> PluginRuntime:
    global _runtime
    with _lock:
        if _runtime is None:
            root = ensure_private_directory(data_dir())
            ensure_private_directory(snapshots_dir())
            ensure_private_directory(runtime_dir())
            _runtime = build_runtime(root)
        return _runtime
