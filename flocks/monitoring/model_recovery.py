"""Durable, monitor-only backoff for transient model dependencies.

Health belongs to a project, source device and configured model, rather than an
incident. A gateway outage must not consume every incident's repair allowance.
No provider options or retry settings outside monitoring are changed here.
"""
from datetime import datetime, timedelta, timezone

from .store import connection, rows
from . import diagnostics as diag

TRANSIENT_FAILURES = frozenset({'timeout', 'connection', 'unavailable', 'rate_limit'})
DELAYS = (600, 1200, 2400, 3600)
REQUEST_TIMEOUT = 90
PROBE_LEASE_SECONDS = REQUEST_TIMEOUT + 30


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


async def has_failure(owner, project, device):
    return bool(await rows('SELECT 1 FROM monitor_model_recovery WHERE owner=? AND project=? '
        'AND device=? AND failures>0 LIMIT 1', (owner, project, device)))


async def blocked(scope, at):
    """Read only: preflight must never reserve the actual recovery probe."""
    found = await rows('SELECT * FROM monitor_model_recovery WHERE owner=? AND project=? '
        'AND device=? AND provider=? AND model=?', scope)
    if not found or not found[0]['failures']:
        return None
    row = found[0]
    until = max((v for v in (stamp(row['next_retry_at']), stamp(row['probe_until'])) if v), default=at)
    return {**row, 'next_retry_at': until.isoformat()} if until > at else None


async def acquire(scope, at):
    """At most one recovery probe may run after a shared cooldown expires."""
    async with connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cursor = await db.execute('SELECT * FROM monitor_model_recovery WHERE owner=? AND project=? '
            'AND device=? AND provider=? AND model=?', scope)
        found = await cursor.fetchone()
        if not found or not found['failures']:
            return None
        row = dict(found)
        until = max((v for v in (stamp(row['next_retry_at']), stamp(row['probe_until'])) if v), default=at)
        if until > at:
            return {**row, 'next_retry_at': until.isoformat()}
        await db.execute('UPDATE monitor_model_recovery SET probe_until=? WHERE owner=? AND project=? '
            'AND device=? AND provider=? AND model=?',
            ((at + timedelta(seconds=PROBE_LEASE_SECONDS)).isoformat(), *scope))
        diag.event('investigation.model_gate', gate_state='probe', model_provider=scope[3],
                   model_id=scope[4], device=diag.opaque(scope[2]), failure_count=row['failures'])
        return None


async def failed(scope, failure, at):
    """Exponential delays cap at an hour; transient outages never close a case."""
    async with connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cursor = await db.execute('SELECT failures FROM monitor_model_recovery WHERE owner=? AND project=? '
            'AND device=? AND provider=? AND model=?', scope)
        prior = await cursor.fetchone()
        failures = (prior['failures'] if prior else 0) + 1
        retry = (at + timedelta(seconds=DELAYS[min(failures - 1, len(DELAYS) - 1)])).isoformat()
        await db.execute('INSERT INTO monitor_model_recovery '
            '(owner,project,device,provider,model,failures,model_failure,next_retry_at,updated_at,probe_until) '
            'VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(owner,project,device,provider,model) DO UPDATE SET '
            'failures=excluded.failures,model_failure=excluded.model_failure,next_retry_at=excluded.next_retry_at,'
            'updated_at=excluded.updated_at,probe_until=NULL', (*scope, failures, failure, retry, at.isoformat()))
        diag.event('investigation.model_gate', gate_state='cooldown', model_provider=scope[3],
                   model_id=scope[4], device=diag.opaque(scope[2]), failure_count=failures,
                   model_failure=failure, retry_delay_seconds=DELAYS[min(failures - 1, len(DELAYS) - 1)])
        return {'model_failure': failure, 'next_retry_at': retry, 'failures': failures}


async def succeeded(scope, at):
    async with connection() as db:
        cursor = await db.execute('DELETE FROM monitor_model_recovery WHERE owner=? AND project=? '
            'AND device=? AND provider=? AND model=?', scope)
        if cursor.rowcount:
            diag.event('investigation.model_gate', gate_state='recovered', model_provider=scope[3],
                       model_id=scope[4], device=diag.opaque(scope[2]))


async def release(scope):
    """Cancellation must release the probe without erasing previous failures."""
    async with connection() as db:
        await db.execute('UPDATE monitor_model_recovery SET probe_until=NULL WHERE owner=? AND project=? '
            'AND device=? AND provider=? AND model=?', scope)
