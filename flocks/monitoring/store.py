"""Monitoring facts in tasks.db. Short, dedicated transactions avoid shared-connection races."""
from contextlib import asynccontextmanager
import json
import aiosqlite
from flocks.task.store import TaskStore

DDL = '''
CREATE TABLE IF NOT EXISTS monitor_installations (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, policy TEXT NOT NULL,
 installed INTEGER NOT NULL DEFAULT 1, ready INTEGER NOT NULL DEFAULT 0,
 reason TEXT, scheduler_id TEXT, activation_pending INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner, scope));
CREATE TABLE IF NOT EXISTS monitor_daily_sessions (
 owner TEXT NOT NULL, project TEXT NOT NULL, scope TEXT NOT NULL, business_date TEXT NOT NULL,
 session_id TEXT NOT NULL UNIQUE, timezone TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'creating',
 PRIMARY KEY(owner, project, scope, business_date));
CREATE TABLE IF NOT EXISTS monitor_slots (
 scheduler_id TEXT NOT NULL, scheduled_for TEXT NOT NULL, execution_id TEXT UNIQUE,
 status TEXT NOT NULL, PRIMARY KEY(scheduler_id, scheduled_for));
CREATE TABLE IF NOT EXISTS monitor_attempts (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, execution_id TEXT NOT NULL,
 owner TEXT NOT NULL, project TEXT NOT NULL, scope TEXT NOT NULL, business_date TEXT NOT NULL,
 session_id TEXT NOT NULL, message_id TEXT NOT NULL, scheduled_for TEXT, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, error TEXT, result TEXT NOT NULL DEFAULT '{}',
 navigation_confirmed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS monitor_attempts_day ON monitor_attempts(owner, scope, business_date);
CREATE TABLE IF NOT EXISTS monitor_steps (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL, message_id TEXT NOT NULL, part_id TEXT NOT NULL,
 tool TEXT NOT NULL, input TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, output TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS monitor_observations (
 attempt_id TEXT NOT NULL, event_key TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(attempt_id, event_key));
CREATE TABLE IF NOT EXISTS monitor_cursors (
 owner TEXT NOT NULL, scope TEXT NOT NULL, device TEXT NOT NULL, through_time INTEGER NOT NULL,
 PRIMARY KEY(owner, scope, device));
CREATE TABLE IF NOT EXISTS monitor_dispositions (
 id TEXT NOT NULL, owner TEXT NOT NULL, scope TEXT NOT NULL, event_key TEXT NOT NULL,
 comment TEXT NOT NULL, status TEXT NOT NULL, observed_status INTEGER, error TEXT,
 session_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 mode TEXT NOT NULL DEFAULT 'manual', target_status INTEGER NOT NULL DEFAULT 40,
 project TEXT, decision TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(owner, scope, id));
CREATE INDEX IF NOT EXISTS monitor_dispositions_event ON monitor_dispositions(owner,scope,event_key,created_at);
CREATE TABLE IF NOT EXISTS monitor_auto_settings (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
 revision TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(owner,scope));
CREATE TABLE IF NOT EXISTS monitor_auto_queue (
 owner TEXT NOT NULL, scope TEXT NOT NULL, project TEXT NOT NULL, event_key TEXT NOT NULL,
 checked_at TEXT NOT NULL DEFAULT '', reason TEXT, PRIMARY KEY(owner,scope,project,event_key));
CREATE TABLE IF NOT EXISTS monitor_reports (
 owner TEXT NOT NULL, scope TEXT NOT NULL, business_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', version INTEGER NOT NULL DEFAULT 0,
 content TEXT, error TEXT, PRIMARY KEY(owner, scope, business_date));
'''


@asynccontextmanager
async def connection():
    await TaskStore.init()
    async with aiosqlite.connect(TaskStore.get_db_path(), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(DDL)
        await db.execute('BEGIN IMMEDIATE')  # Serialize additive migrations across concurrent readers.
        # Additive migrations also support databases created by earlier builds.
        for table, column in [('monitor_installations', 'activation_pending'), ('monitor_attempts', 'navigation_confirmed')]:
            columns = await db.execute(f'PRAGMA table_info({table})')
            if column not in {row['name'] for row in await columns.fetchall()}:
                await db.execute(f'ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
        columns = await db.execute('PRAGMA table_info(monitor_dispositions)')
        existing = {row['name'] for row in await columns.fetchall()}
        for column, declaration in [('mode', "TEXT NOT NULL DEFAULT 'manual'"),
                                    ('target_status', 'INTEGER NOT NULL DEFAULT 40'),
                                    ('project', 'TEXT'), ('decision', "TEXT NOT NULL DEFAULT '{}'")]:
            if column not in existing:
                await db.execute(f'ALTER TABLE monitor_dispositions ADD COLUMN {column} {declaration}')
        await db.commit()
        try:
            yield db
            await db.commit()
        except BaseException:
            await db.rollback()
            raise


async def rows(sql, args=()):
    async with connection() as db:
        async with db.execute(sql, args) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def write(sql, args=()):
    async with connection() as db:
        await db.execute(sql, args)


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)
