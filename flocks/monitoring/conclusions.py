"""Durable, evidence-validated risk facts, independent of execution checkpoints.

The snapshot contains only the established conclusion and field-presence proof
projections. Original packets stay in investigation/version storage. It is not
a substitute for fresh evidence at a notification or source-write gate.
"""
import json

from .store import connection, encode


def decoded(value, default):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return default
    return value if isinstance(value, type(default)) else default


def validated_snapshot(snapshot, event):
    """Recheck identity and the original business gate; mail is never proof."""
    from .assessment import _assess_current
    canonical = decoded(snapshot.get('event'), {}) if isinstance(snapshot, dict) else {}
    if (not canonical.get('device') or not canonical.get('id')
            or canonical.get('device') != event.get('device') or canonical.get('id') != event.get('id')
            or canonical.get('key') != event.get('key')):
        return None
    candidate = {**snapshot, 'state': 'ready'}
    assessed = _assess_current(canonical, candidate)
    if assessed['verdict'] != 'risk':
        return None
    return decoded(snapshot.get('result'), {})


def capture_case(case, previous=None):
    """Persist only validated conclusions, never unknown or source labels."""
    from .assessment import _assess_current, _proof_fields
    case = dict(case)
    event = decoded(case.get('event'), {})
    snapshot = decoded(previous if previous is not None else case.get('confirmed_risk'), {})
    assessment = _assess_current(event, case)
    if assessment['verdict'] == 'risk':
        result = decoded(case.get('result'), {})
        keys = ('verdict', 'confidence', 'risk_confirmed', 'reason', 'operator_summary',
                'operator_explanation', 'blocking_gaps', 'evidence_ids', 'gaps', 'evidence_schema')
        evidence = decoded(case.get('evidence'), [])
        proofs = [{key: proof[key] for key in ('id', 'device', 'event', 'event_revision', 'success', 'kind') if key in proof}
                  | {'fact_fields': sorted(_proof_fields(proof))}
                  for proof in evidence if isinstance(proof, dict) and proof.get('id') in assessment['evidence_ids']]
        activity = {key: event[key] for key in ('key', 'id', 'device', 'host', 'name', 'endTime',
                    'dealStatus', 'incidentSeverity', 'riskLevel', 'incidentThreatClass', 'incidentThreatType',
                    'alertIds', 'development_sample', 'basics', 'startTime') if key in event}
        return {'event': activity, 'result': {key: result[key] for key in keys if key in result},
                'evidence': proofs, 'state': 'ready', 'revision': case.get('revision'),
                'confirmed_at': case.get('updated_at') or case.get('archived_at')}
    if assessment['verdict'] == 'benign' and validated_snapshot(snapshot, event):
        snapshot = {**snapshot, 'superseded_by': {'event_revision': assessment['event_revision'],
                    'reason': assessment['reason'], 'at': case.get('updated_at') or case.get('archived_at')}}
    return snapshot


async def restore_case_snapshot(db, owner, project, case):
    """Read legacy history on the caller's transaction before replacing a row."""
    row = dict(case)
    if row.get('confirmed_risk') is not None:
        return capture_case(row)
    snapshot = capture_case(row)
    if snapshot:
        return snapshot
    cursor = await db.execute('SELECT * FROM monitor_investigation_versions WHERE owner=? AND project=? '
                              "AND event_key=? AND state='ready' AND json_valid(result) "
                              "AND CASE WHEN json_valid(result) THEN json_extract(result,'$.verdict') END IN ('risk','benign') "
                              'ORDER BY revision DESC LIMIT 64', (owner, project, row['event_key']))
    history = [dict(item) for item in await cursor.fetchall()]
    # Fold oldest to newest so explicit newer benign evidence suppresses
    # restoration, while unknown failures do not.
    for version in [*reversed(history), row]:
        snapshot = capture_case(version, snapshot)
    return snapshot


async def restore_confirmed_risks(owner, project, event_keys):
    """Lazily backfill legacy rows once, in bounded device/event-scoped batches.

    NULL means unchecked; '{}' means inspected with no confirmed risk. Updates
    are compare-and-set so concurrent successful investigations always win.
    Newer supported benign judgments prevent older risk facts being revived.
    """
    keys = list(dict.fromkeys(key for key in event_keys if isinstance(key, str) and key))
    output = {}
    for offset in range(0, len(keys), 100):
        batch = keys[offset:offset + 100]
        if not batch:
            continue
        async with connection() as db:
            cursor = await db.execute('SELECT * FROM monitor_investigations WHERE owner=? AND project=? '
                                      f'AND event_key IN ({",".join("?" for _ in batch)})', (owner, project, *batch))
            for record in await cursor.fetchall():
                row = dict(record)
                if row['confirmed_risk'] is not None:
                    output[row['event_key']] = decoded(row['confirmed_risk'], {})
                    continue
                snapshot = await restore_case_snapshot(db, owner, project, row)
                await db.execute('UPDATE monitor_investigations SET confirmed_risk=? WHERE owner=? AND project=? '
                                 'AND event_key=? AND confirmed_risk IS NULL',
                                 (encode(snapshot), owner, project, row['event_key']))
                output[row['event_key']] = snapshot
    return output
