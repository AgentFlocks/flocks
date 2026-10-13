"""Bounded, owner-scoped read-only task state, independent of TaskStore._conn."""
import json
from contextlib import closing, contextmanager
import sqlite3
import time
from datetime import datetime, timezone

from flocks.task.diagnostics import error_fields
from flocks.task.store import TaskStore
from .diagnostics import opaque


@contextmanager
def read_only_connection():
    started = time.monotonic()
    path = TaskStore.get_db_path()
    with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() - started > .5), 1000)
        yield db


def read_rows(sql, args):
    """Only used inside the diagnostic download's task-local read scope."""
    if not sql.lstrip().upper().startswith('SELECT '):
        raise ValueError('diagnostic_query_must_be_select')
    with read_only_connection() as db:
        return [dict(row) for row in db.execute(sql, args).fetchall()]


def task_state(owner, scope):
    state = {'snapshot_available': False, 'mode': 'read_only',
             'captured_at': datetime.now(timezone.utc).isoformat()}
    try:
        path = TaskStore.get_db_path()
        state['database'] = opaque(path)
        if not path.is_file():
            return {**state, 'reason': 'database_missing'}
        # mode=ro cannot create or migrate a DB. Never reuse a poisoned writer,
        # checkpoint WAL, BEGIN IMMEDIATE, or initialize TaskStore on export.
        with read_only_connection() as db:
            journal = db.execute('PRAGMA journal_mode').fetchone()[0]
            state['journal_mode'] = journal if journal in {'wal', 'delete', 'truncate', 'persist', 'memory', 'off'} else 'unknown'
            columns = {item[1] for item in db.execute('PRAGMA table_info(monitor_installations)')}
            extras = ',project,policy' if {'project','policy'} <= columns else ''
            row = db.execute('SELECT scheduler_id,installed,ready' + extras + ' FROM monitor_installations '
                             'WHERE owner=? AND scope=?', (owner, scope)).fetchone()
            if row is None:
                return {**state, 'snapshot_available': True, 'installed': False}
            scheduler = db.execute('SELECT status,trigger FROM task_schedulers WHERE id=?',
                                   (row['scheduler_id'],)).fetchone()
            trigger = json.loads(scheduler['trigger']) if scheduler else {}
            next_run = trigger.get('next_run') or trigger.get('nextRun')
            if next_run is not None:
                next_run = datetime.fromisoformat(next_run).isoformat()
            counts = db.execute('SELECT status,COUNT(*) AS n FROM task_executions WHERE scheduler_id=? '
                                'GROUP BY status', (row['scheduler_id'],)).fetchall()
            executions = db.execute('SELECT id,status,created_at,started_at,completed_at FROM task_executions '
                                    'WHERE scheduler_id=? ORDER BY created_at DESC LIMIT 5', (row['scheduler_id'],)).fetchall()
            followup_counts = {}
            if extras and db.execute("SELECT 1 FROM sqlite_master WHERE name='monitor_event_followup'").fetchone():
                followup_counts = {item['state']: item['n'] for item in db.execute('SELECT state,COUNT(*) AS n FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? GROUP BY state', (owner, scope, row['project'])).fetchall()}
            policy = json.loads(row['policy']) if extras else {}
            observation_rounds = policy.get('observation_rounds', 6)
            overdue = None
            if next_run:
                parsed = datetime.fromisoformat(next_run)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                overdue = max(0, (datetime.now(timezone.utc) - parsed).total_seconds())
            return {**state, 'snapshot_available': True, 'installed': bool(row['installed']), 'ready': bool(row['ready']),
                    'scheduler_present': scheduler is not None,
                    'scheduler_status': scheduler['status'] if scheduler and scheduler['status'] in {'active', 'disabled', 'paused', 'completed'} else 'unknown',
                    'followup_counts': followup_counts, 'observation_rounds': observation_rounds if type(observation_rounds) is int else None,
                    'next_run': next_run, 'overdue_seconds': overdue,
                    'execution_counts': {item['status']: item['n'] for item in counts if item['status'] in {'pending', 'queued', 'running', 'completed', 'failed', 'cancelled'}},
                    'recent_executions': [{'execution': opaque(item['id']), 'status': item['status'] if item['status'] in {'pending', 'queued', 'running', 'completed', 'failed', 'cancelled'} else 'unknown',
                                           **{key: datetime.fromisoformat(item[key]).isoformat() if item[key] else None for key in ('created_at', 'started_at', 'completed_at')}} for item in executions]}
    except Exception as exc:
        return {**state, 'reason': 'read_failed', **error_fields(exc)}
