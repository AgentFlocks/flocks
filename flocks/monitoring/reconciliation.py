"""Reconcile complete source membership and explicit exact-ID status facts."""
import asyncio
import json

from .adapter import normalize, ContractError
from .assessment import apply_followup, assess_event
from .automatic import read_event
from .store import rows, write, encode, connection
from .summaries import Summary


async def finish_missing(policy, current_keys, completed_devices, round_id):
    """A complete source snapshot can end monitoring, never prove remediation.

    Incremental absence and failed/partial pagination cannot enter this path.
    Keep all past facts; this only ends local follow-up for the absent event.
    """
    from . import followup
    devices = set(completed_devices) & set(policy.devices)
    if not devices:
        return set()
    marks = ','.join('?' for _ in devices)
    candidates = await rows(
        'WITH latest AS (SELECT o.event_key,MAX(a.sequence) AS sequence '
        'FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
        'WHERE a.owner=? AND a.scope=? AND a.project=? GROUP BY o.event_key) '
        'SELECT o.event_key,o.data FROM latest l JOIN monitor_attempts a ON a.sequence=l.sequence '
        'JOIN monitor_observations o ON o.attempt_id=a.id AND o.event_key=l.event_key '
        'LEFT JOIN monitor_event_followup f ON f.owner=a.owner AND f.scope=a.scope '
        'AND f.project=a.project AND f.event_key=o.event_key '
        f"WHERE json_valid(o.data) AND json_type(o.data)='object' AND json_extract(o.data,'$.device') IN ({marks}) "
        "AND COALESCE(json_extract(o.data,'$.dealStatus'),-1) NOT IN (40,60) "
        "AND COALESCE(f.state,'') NOT IN ('verified','external_closed','monitoring_ended')",
        (policy.owner, policy.scope, policy.project, *sorted(devices)))
    ended = set()
    for item in candidates:
        if item['event_key'] in current_keys:
            continue
        event = json.loads(item['data'])
        if (not event.get('id') or event.get('device') not in devices
                or item['event_key'] != f"{event['device']}:incident:{event['id']}"):
            continue
        event['key'] = item['event_key']
        state = await followup.end_missing(policy, event, round_id)
        if state and state['state'] == 'monitoring_ended':
            ended.add(item['event_key'])
    return ended


async def recheck_existing(policy, adapter, recorder, round_id, current_keys, *, limit=12, timeout_seconds=60):
    from . import followup, investigation
    if not policy.devices:
        return [], set(), {'checked': 0, 'external_closed': 0, 'errors': []}
    marks = ','.join('?' for _ in policy.devices)
    excluded = sorted(current_keys)
    exclude_sql = ' AND o.event_key NOT IN (' + ','.join('?' for _ in excluded) + ')' if excluded else ''
    # Oldest check first. Failed exact queries are rotated, so an unavailable
    # incident cannot permanently starve other historical incidents.
    candidates = await rows(
        'WITH latest AS (SELECT o.event_key,MAX(a.sequence) AS sequence FROM monitor_observations o '
        'JOIN monitor_attempts a ON a.id=o.attempt_id WHERE a.owner=? AND a.scope=? AND a.project=? GROUP BY o.event_key) '
        'SELECT o.event_key,o.data FROM latest l JOIN monitor_attempts a ON a.sequence=l.sequence '
        'JOIN monitor_observations o ON o.attempt_id=a.id AND o.event_key=l.event_key '
        'LEFT JOIN monitor_event_followup f ON f.owner=a.owner AND f.scope=a.scope AND f.project=a.project AND f.event_key=o.event_key '
        f"WHERE json_valid(o.data) AND json_type(o.data)='object' AND json_extract(o.data,'$.device') IN ({marks}) "
        "AND (COALESCE(json_extract(o.data,'$.dealStatus'),-1) NOT IN (40,60) OR json_extract(o.data,'$.source_terminal_unmatched')=1) "
        "AND COALESCE(f.state,'') NOT IN ('verified','external_closed','monitoring_ended') "
        + exclude_sql + ' ORDER BY COALESCE(f.updated_at,\'\'),a.sequence LIMIT ?',
        (policy.owner, policy.scope, policy.project, *policy.devices, *excluded, min(100, limit)))
    result, checked_keys = [], set()
    summary = {'checked': 0, 'external_closed': 0, 'errors': []}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    for candidate in candidates:
        if candidate['event_key'] in current_keys or summary['checked'] >= limit:
            continue
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        event = json.loads(candidate['data'])
        if not isinstance(event, dict) or event.get('device') not in policy.devices or candidate['event_key'] != f"{event.get('device')}:incident:{event.get('id')}" or not event.get('id'):
            summary['errors'].append('历史事件身份不完整，未用于状态写入或闭环。')
            continue
        summary['checked'] += 1
        try:
            async def query(_):
                raw = await read_event(adapter, event)
                state = raw.get('dealStatus')
                if type(state) is not int or state not in {0,10,30,40,50,60,70}:
                    raise ContractError('历史事件当前状态未知，保留待核对')
                return raw, {'event': event['id'], 'device': event['device'], 'deal_status': state}, Summary(
                    f"按原事件编号核对「{event['name']}」（{event['id']}）：当前 XDR 状态为 {state}。只有明确终态且活动版本一致，才确认事件结束；筛选列表未返回不作为处置依据。")
            raw = await asyncio.wait_for(recorder.call('核对历史事件状态', {'device': event['device'], 'event': event['id']}, query), min(15, remaining))
            closed = await followup.reconcile_external(policy, event, raw, round_id, query_by_id=True)
            fresh = normalize(event['device'], raw)
            fresh['device_name'] = policy.device_names.get(event['device'], event['device'])
            fresh['investigationWindow'] = event.get('investigationWindow')
            fresh['risk'] = 'unknown'
            if closed and closed.get('closed'):
                fresh.update(assessment=apply_followup(
                    assess_event(fresh, (await rows('SELECT * FROM monitor_investigations WHERE owner=? AND project=? AND event_key=?', (policy.owner, policy.project, fresh['key'])) or [{}])[0]), fresh, closed))
                summary['external_closed'] += int(bool(closed.get('external')))
            elif raw.get('dealStatus') not in {40, 60}:
                async with connection() as db:
                    await investigation.enqueue(db, policy, fresh, investigation.now().isoformat())
                checked_keys.add(fresh['key'])
            else:
                # Terminal state on a newer activity version is recorded as a
                # source fact, not claimed as closure of the old investigation.
                fresh['source_terminal_unmatched'] = True
            await write('INSERT OR REPLACE INTO monitor_observations VALUES(?,?,?)', (round_id, fresh['key'], encode(fresh)))
            result.append(fresh)
        except asyncio.CancelledError:
            raise
        except Exception:
            await followup.observe(policy, event, round_id, query_complete=False)
            await write('UPDATE monitor_event_followup SET updated_at=? WHERE owner=? AND scope=? AND project=? AND event_key=?', (investigation.now().isoformat(), policy.owner, policy.scope, policy.project, event['key']))
            summary['errors'].append(f"历史事件 {event.get('id')} 未能完成编号回查，保留原状态；不能据此认定已被处置。")
    return result, checked_keys, summary
