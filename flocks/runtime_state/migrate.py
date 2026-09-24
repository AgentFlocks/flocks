"""
Move existing state from local SQLite files into a shared database.

Written to be re-runnable. A migration that must succeed on the first attempt
is a migration that strands an operator when the network drops halfway; this
one can be run again and will finish what it started, because every write is
keyed on the document's own key rather than appended.

It also does not delete the source. The SQLite files stay exactly as they were
so a deployment can compare, or go back, without needing a restore.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from flocks.utils.log import Log

log = Log.create(service="runtime_state.migrate")

DEFAULT_BATCH = 500


@dataclass
class MigrationReport:
    """What a migration run did, per source file."""

    scanned: int = 0
    migrated: int = 0
    skipped: int = 0
    failed: int = 0
    by_file: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.errors

    def describe(self) -> str:
        parts = [
            f"扫描 {self.scanned}",
            f"迁移 {self.migrated}",
            f"跳过 {self.skipped}",
            f"失败 {self.failed}",
        ]
        return " / ".join(parts)


def discover_sqlite_files(data_dir: Path) -> List[Path]:
    """Every SQLite file that could hold state, newest layout or older."""
    if not data_dir.is_dir():
        return []
    found = sorted(
        path
        for path in data_dir.rglob("*.db")
        # WAL and shm siblings are not databases to open on their own.
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    )
    return found


async def _iter_storage_rows(db_path: Path):
    """Read legacy documents explicitly, in bounded read-only batches."""
    import sqlite3
    def open_cursor():
        db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        db.execute("PRAGMA query_only=ON")
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='storage'").fetchone()
        return db, db.execute("SELECT key,value,type FROM storage") if exists else None
    db, cursor = await asyncio.to_thread(open_cursor)
    try:
        if cursor is not None:
            while rows := await asyncio.to_thread(cursor.fetchmany, DEFAULT_BATCH):
                for key, value, kind in rows:
                    yield key, value, kind or "json"
    finally:
        await asyncio.to_thread(db.close)


def _decode(value: str) -> Any:
    """Documents are stored as JSON text; anything else travels as a string.

    A row that will not parse is migrated as a string rather than dropped:
    losing it would be worse than storing it in a slightly different shape,
    and the target column accepts either.
    """
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


async def migrate_state(
    data_dir: Path,
    target,
    *,
    key_prefixes: Optional[Sequence[str]] = None,
    batch_size: int = DEFAULT_BATCH,
    dry_run: bool = False,
) -> MigrationReport:
    """Copy documents from local SQLite files into *target*.

    ``key_prefixes`` limits what moves, so a deployment can migrate
    conversations first and leave the rest until it has confidence.
    """
    report = MigrationReport()
    files = discover_sqlite_files(Path(data_dir))
    if not files:
        log.warn("runtime_state.migrate.no_sources", {"data_dir": str(data_dir)})
        return report

    for db_path in files:
        batch: List[Tuple[str, Any, str]] = []
        moved = 0
        try:
            async for key, value, value_type in _iter_storage_rows(db_path):
                report.scanned += 1
                if key_prefixes and not any(key.startswith(p) for p in key_prefixes):
                    report.skipped += 1
                    continue
                batch.append((key, _decode(value), value_type))
                if len(batch) >= batch_size:
                    moved += await _flush(target, batch, report, dry_run)
                    batch = []
            if batch:
                moved += await _flush(target, batch, report, dry_run)
        except Exception as exc:
            report.errors.append(f"{db_path.name}: {exc}")
            report.failed += 1
            continue

        if moved:
            report.by_file[db_path.name] = moved

    log.info(
        "runtime_state.migrate.complete",
        {
            "scanned": report.scanned,
            "migrated": report.migrated,
            "skipped": report.skipped,
            "failed": report.failed,
            "dry_run": dry_run,
        },
    )
    return report


async def _flush(target, batch: List[Tuple[str, Any, str]], report: MigrationReport, dry_run: bool) -> int:
    if dry_run:
        report.migrated += len(batch)
        return len(batch)
    try:
        inserted = await target.import_legacy_many(batch)
    except Exception as exc:
        report.failed += len(batch)
        report.errors.append(type(exc).__name__)
        return 0
    report.migrated += inserted
    report.skipped += len(batch) - inserted
    return inserted


async def verify_migration(
    data_dir: Path,
    target,
    *,
    key_prefixes: Optional[Sequence[str]] = None,
    sample_size: int = 200,
) -> Tuple[bool, List[str]]:
    """Check the copy against the source.

    Counts alone would pass while every value was wrong, so this compares the
    documents themselves on a sample. An operator deciding whether to cut over
    needs evidence the data arrived, not that the right number of rows did.
    """
    expected = {}
    for db_path in discover_sqlite_files(Path(data_dir)):
        async for key, value, _kind in _iter_storage_rows(db_path):
            if key_prefixes and not any(key.startswith(prefix) for prefix in key_prefixes):
                continue
            expected[key] = _decode(value)
            if len(expected) >= sample_size:
                break
        if len(expected) >= sample_size:
            break
    actual = await target.get_many(list(expected))
    problems = [f"Missing or different document: {key}" for key, value in expected.items()
                if key not in actual or actual[key] != value]
    return not problems, problems


async def migrate_task_executions(source_db: Path, target) -> int:
    """The complete offline importer handles tasks without replaying work."""
    raise RuntimeError("Use python -m flocks.db.migrate; partial running-task migration is no longer supported")


__all__ = [
    "MigrationReport",
    "discover_sqlite_files",
    "migrate_state",
    "migrate_task_executions",
    "verify_migration",
]
