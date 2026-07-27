"""Device integration schema migration tests."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from flocks.storage.storage import Storage
from flocks.tool.device import models as device_models


def _reset_storage_state() -> None:
    Storage._initialized = False
    Storage._init_pid = None
    Storage._db_path = None


async def _shutdown_storage() -> None:
    await Storage.shutdown()
    _reset_storage_state()


async def _device_columns(db_path: Path) -> set[str]:
    async with Storage.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(device_integrations)")
        return {str(row[1]) for row in await cursor.fetchall()}


async def _device_indexes(db_path: Path) -> set[str]:
    async with Storage.connect(db_path) as db:
        cursor = await db.execute("PRAGMA index_list(device_integrations)")
        return {str(row[1]) for row in await cursor.fetchall()}


def _capture_storage_warnings(monkeypatch) -> list[tuple[Any, Any]]:
    warnings: list[tuple[Any, Any]] = []
    monkeypatch.setattr(Storage._log, "warn", lambda message=None, extra=None: warnings.append((message, extra)))
    return warnings


def _extension_ddl_warnings(warnings: list[tuple[Any, Any]]) -> list[tuple[Any, Any]]:
    return [entry for entry in warnings if entry[0] == "storage.extension_ddl.failed"]


@pytest.mark.asyncio
async def test_device_schema_fresh_init_does_not_warn_duplicate_group_id(monkeypatch, tmp_path: Path) -> None:
    warnings = _capture_storage_warnings(monkeypatch)
    db_path = tmp_path / "fresh.db"

    _reset_storage_state()
    try:
        await Storage.init(db_path)

        assert device_models.DEFAULT_GROUP_ID == "default-room"
        assert "group_id" in await _device_columns(db_path)
        assert "idx_device_group" in await _device_indexes(db_path)
        assert _extension_ddl_warnings(warnings) == []
    finally:
        await _shutdown_storage()


@pytest.mark.asyncio
async def test_device_schema_old_integrations_table_gets_group_id(monkeypatch, tmp_path: Path) -> None:
    warnings = _capture_storage_warnings(monkeypatch)
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
        CREATE TABLE device_integrations (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            service_id  TEXT NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            verify_ssl  INTEGER NOT NULL DEFAULT 0,
            fields      TEXT NOT NULL DEFAULT '{}',
            status      TEXT NOT NULL DEFAULT 'unknown',
            message     TEXT,
            latency_ms  INTEGER,
            checked_at  INTEGER,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );
        """)

    _reset_storage_state()
    try:
        await Storage.init(db_path)

        assert "group_id" in await _device_columns(db_path)
        assert "idx_device_group" in await _device_indexes(db_path)
        assert _extension_ddl_warnings(warnings) == []
    finally:
        await _shutdown_storage()
