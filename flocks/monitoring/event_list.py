"""Read-only event follow-up, with device-scoped 24-hour presentation groups.

Grouping is a presentation aid. It never changes incident identities, mail
idempotency, disposition records, or the daily dashboard/report projection.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
import re
from zoneinfo import ZoneInfo

from .event_details import redact_text, _SECRET_NAME, build_story, readable_story
from .assessment import assess_event, same_evidence_version, STATUSES, FACT_KEYS
from .followup import projection as followup_projection, PROJECTION_COLUMNS
from contextlib import contextmanager
import asyncio
import sqlite3
import time

from flocks.task.store import TaskStore

MAX_EVENTS = 1000
MAX_DATE_DAYS = 366
MAX_LIST_JSON = 48_000
MAX_DETAIL_JSON = 256_000
MAX_DETAIL_ROWS = 200
MAX_CONFIRMED_PROOFS = 3
CLOSURES = {40: 'closed', 60: 'ignored', 70: 'contained'}
HANDLING_STATES = ('open', 'closed', 'ignored', 'contained', 'ended')
SOURCE_FILTERS = ('source_closed_risk', 'source_closed_benign', 'source_closed_unknown')
SORT_FIELDS = ('first_seen_at', 'last_processed_at')


def _json(value, default):
    try:
        return json.loads(value) if isinstance(value, str) else default
    except (ValueError, TypeError, RecursionError):
        return default


def _text(value, limit=500):
    return redact_text(value)[:limit] if isinstance(value, str) else ''


def _time(value, tz):
    """Parse a source time; unknown timezones use the configured business zone."""
    try:
        if type(value) in (int, float):
            if value > 100_000_000_000:
                value /= 1000
            return datetime.fromtimestamp(value, timezone.utc) if value > 0 else None
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        return (parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _iso(value):
    return value.isoformat() if value else None


def _host(value):
    """Exact asset identity, with textual normalization only (no fuzzy match)."""
    if not isinstance(value, str):
        return ''
    text = value.strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text.casefold().rstrip('.') if text and not any(c.isspace() for c in text) else ''


def _ioc(kind, value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if kind == 'ip':
        try:
            return ('ip', str(ipaddress.ip_address(text)))
        except ValueError:
            return None
    if kind == 'domain':
        text = text.rstrip('.').lower()
        try:
            text = text.encode('idna').decode('ascii')
        except UnicodeError:
            return None
        labels = text.split('.')
        if (len(labels) < 2 or len(text) > 253 or any(not x or len(x) > 63
                or x.startswith('-') or x.endswith('-') or any(not (c.isalnum() or c == '-') for c in x)
                for x in labels)):
            return None
        try:
            ipaddress.ip_address(text)
            return None
        except ValueError:
            return ('domain', text)
    return None


def _event_times(event, tz):
    basics = event.get('basics') if isinstance(event.get('basics'), dict) else {}
    first = _time(event.get('startTime') or basics.get('start_time') or basics.get('alert_time'), tz)
    last = _time(event.get('endTime') or basics.get('end_time'), tz) or first
    first = first or last
    if first and last and last < first:
        return None, None
    return first, last


def _extract_iocs(event, investigation, tz):
    """Only incident-bound successful XDR malicious IP/DNS entity facts.

    Host addresses, model prose, packet strings, merely matching names, and
    cross-device candidates are never promoted to IOC identities.
    """
    canonical = _json(investigation.get('event'), {})
    canonical = canonical if isinstance(canonical, dict) else {}
    if (canonical.get('id') != event.get('id') or canonical.get('device') != event.get('device')
            or _host(canonical.get('host')) != _host(event.get('host'))):
        return {}, []
    compact = 'ioc_rows' in investigation
    proofs = _json(investigation.get('ioc_rows') if compact else investigation.get('evidence'), [])
    if not isinstance(proofs, list):
        return {}, []
    found, warnings = {}, []
    if len(proofs) > 50:
        warnings.append('列表仅采用此事件前 50 条明确 IOC；未读取部分不参与关联。')
    from .investigation import fingerprint
    if not same_evidence_version(canonical, event):
        return {}, ['最新事件版本与已保存取证版本不同，未展示旧版本 IOC。']
    for proof in proofs[:50] if compact else proofs[:24]:
        if (not isinstance(proof, dict) or proof.get('success') not in (True, 1)
                or proof.get('event') != event.get('id') or proof.get('device') != event.get('device')
                or proof.get('kind') not in ('ip', 'dns')):
            continue
        # The stored revision fingerprint binds proof to the investigation's
        # canonical incident version. Older records lacking that binding are
        # excluded rather than guessed to belong to the current version.
        revision = proof.get('event_revision')
        if revision != fingerprint(canonical):
            continue
        query = proof.get('query')
        if isinstance(query, dict) and (query.get('action') != 'get_entities'
                or query.get('uuid') != event.get('id') or query.get('entity_type') != proof['kind']):
            continue
        facts = proof.get('facts')
        if compact:
            facts = {'data': {'item': [{'threatLevel': 3, 'ip': proof.get('value'), 'domain': proof.get('value')}]}}
        data = facts.get('data') if isinstance(facts, dict) else None
        if not isinstance(data, dict):
            continue
        items = data.get('item', data.get('list'))
        if not isinstance(items, list):
            continue
        for item in items[:200]:
            if not isinstance(item, dict) or type(item.get('threatLevel')) is not int or item['threatLevel'] != 3:
                continue
            kind = 'ip' if proof['kind'] == 'ip' else 'domain'
            value = item.get('ip') if kind == 'ip' else item.get('domain') or item.get('domainName') or item.get('dns')
            normalized = _ioc(kind, value)
            if normalized is None or normalized[0] == 'ip' and normalized[1] == _host(event.get('host')):
                continue
            found.setdefault(normalized, []).append({'evidence_id': _text(proof.get('id'), 100),
                'source': _text(proof.get('source_name') or proof.get('tool'), 200),
                'partial': bool(proof.get('partial'))})
        if proof.get('partial'):
            warnings.append('IOC 取自部分返回的明确恶意实体；关联只说明已取得事实，不代表全量覆盖。')
    return found, list(dict.fromkeys(warnings))


def _handling(event, disposition, observed_at, tz):
    if event.get('source_terminal_unmatched'):
        return 'open'
    actual = event.get('dealStatus')
    label = CLOSURES.get(actual, 'open') if type(actual) is int else 'open'
    if disposition.get('status') == 'verified':
        verified = _time(disposition.get('updated_at'), tz)
        observed = _time(observed_at, tz)
        if verified and (observed is None or verified >= observed):
            # Only a verified target matching the read-back fact is terminal.
            target = disposition.get('target_status')
            from .disposition import matches_status
            if matches_status(target, disposition.get('observed_status')):
                label = CLOSURES.get(target, 'open')
    return label


def _summarize(row, investigation, disposition, notice, reply_count, names, tz, followup=None):
    event = _json(row['data'], {})
    if not isinstance(event, dict):
        event = {}
    key = row['event_key']
    event['key'] = key
    device = _text(event.get('device'), 200)
    event_id = _text(event.get('id'), 300)
    expected = f'{device}:incident:{event_id}'
    # Corrupt/misattributed observations may be visible for support, but may
    # not supply evidence for a group or borrow another device's status.
    valid_identity = bool(device and event_id and key == expected)
    if not valid_identity:
        investigation, disposition, notice, reply_count = {}, {}, {}, 0
    result = _json(investigation.get('result'), {})
    result = result if isinstance(result, dict) else {}
    risk = event.get('risk') if event.get('risk') in ('risk', 'unknown', 'ignored', 'benign') else 'unknown'
    canonical = _json(investigation.get('event'), {})
    canonical = canonical if isinstance(canonical, dict) else {}
    from .investigation import fingerprint
    current_investigation = same_evidence_version(canonical, event)
    if current_investigation and investigation.get('state') == 'ready' and result.get('verdict') in ('risk', 'unknown', 'benign'):
        risk = result['verdict']
    investigation_state = investigation.get('state') or 'pending'
    investigation_verdict = result.get('verdict')
    investigation_reason = _text(result.get('reason'), 1500)
    if investigation and not current_investigation:
        investigation_state, investigation_verdict = 'stale', 'unknown'
        investigation_reason = '原事件已更新，旧调查结论仅供历史查看；当前事件仍需重新核对。'
    first, last = _event_times(event, tz)
    iocs, warnings = _extract_iocs(event, investigation, tz) if valid_identity else ({}, ['事件标识不完整，未作自动合并。'])
    handling = _handling(event, disposition, row['started_at'], tz)
    assessment = assess_event(event, investigation, handling=handling)
    from .assessment import apply_followup
    assessment = apply_followup(assessment, event, followup)
    monitoring_ended = bool(assessment.get('ended') and assessment.get('lifecycle') == 'monitoring_ended'
                            and handling not in {'closed', 'ignored'})
    if monitoring_ended:
        handling = 'ended'
    risk = assessment['verdict']
    notice_state = notice.get('state')
    basics = event.get('basics') if isinstance(event.get('basics'), dict) else {}
    from .disposition import matches_status
    observed = _time(row['started_at'], tz)
    verified = _time(disposition.get('updated_at'), tz)
    disposition_verified = (disposition.get('status') == 'verified' and verified
        and matches_status(disposition.get('target_status'), disposition.get('observed_status'))
        and (observed is None or verified >= observed or matches_status(disposition.get('target_status'), event.get('dealStatus'))))
    # Source closure describes a source visibility/terminal fact, never a claim
    # that a person repaired the threat. Verified writes by this suite keep
    # their own disposition outcome and must not become external closures.
    lifecycle = assessment.get('lifecycle')
    suite_verified = bool(disposition_verified or lifecycle == 'verified')
    native_terminal = type(event.get('dealStatus')) is int and event['dealStatus'] in {40, 60}
    source_closed = bool(monitoring_ended and not suite_verified or lifecycle == 'external_closed' or
                         native_terminal and not event.get('source_terminal_unmatched') and not suite_verified)
    historical_verdict = assessment.get('historical_verdict', assessment['verdict'])
    historical_verdict = historical_verdict if historical_verdict in {'risk', 'benign'} else 'unknown'
    if source_closed:
        assessment = {**assessment, 'status': 'ended', 'label': '源端关闭',
                      'notify_eligible': False, 'source_closed': True,
                      'source_closed_verdict': historical_verdict,
                      'source_closure_basis': 'not_returned' if monitoring_ended else 'source_status'}
    else:
        assessment = {**assessment, 'source_closed': False}
    processed = [(row.get('last_seen_at') or row['started_at'], 'observation'),
                 (investigation.get('updated_at'), 'investigation'),
                 (disposition.get('updated_at'), 'disposition'),
                 ((followup or {}).get('updated_at'), 'followup')]
    processed = [(stamp, source, _time(stamp, tz)) for stamp, source in processed]
    processed = [item for item in processed if item[2] is not None]
    latest_processed = max(processed, key=lambda item: item[2]) if processed else (None, None, None)
    summary = {'key': key, 'id': event_id or key, 'name': _text(event.get('name'), 300) or '名称未提供',
        'device': device, 'device_name': _text(names.get(device) or event.get('device_name') or device, 200),
        'host': _text(event.get('host') or basics.get('host'), 200),
        'risk': risk, 'assessment': assessment, 'identity_valid': valid_identity, 'handling': handling,
        'handling_source': 'monitoring' if monitoring_ended else 'monitor_verified' if disposition_verified else 'xdr',
        'in_period': bool(row.get('in_period', True)), 'carried_over': not bool(row.get('in_period', True)),
        'is_backlog': not bool(row.get('in_period', True)), 'deal_status': event.get('dealStatus'),
        'observed_at': row['started_at'], 'first_seen_at': row['first_seen_at'], 'last_seen_at': row['last_seen_at'],
        'last_processed_at': _iso(latest_processed[2]), 'last_processed_source': latest_processed[1],
        'source_closed': source_closed, 'source_closed_verdict': historical_verdict if source_closed else None,
        'event_at': _iso(first), 'event_end_at': _iso(last), 'occurrence_at': _iso(last),
        'occurrence_time_unknown': last is None,
        'sessionID': row['session_id'], 'messageID': row['message_id'],
        'investigation': {'state': investigation_state,
            'verdict': investigation_verdict, 'reason': investigation_reason,
            'updated_at': investigation.get('updated_at'), 'is_current': current_investigation},
        'mail': {'id': notice.get('id'), 'state': notice_state,
            'sent': bool(notice.get('sent_at') or notice_state == 'sent'),
            'replied': reply_count > 0, 'reply_count': reply_count,
            'recipient': _text(notice.get('recipient'), 300), 'sent_at': notice.get('sent_at'),
            'failed': notice_state == 'failed' or notice_state == 'queued' and bool(notice.get('error'))},
        'disposition': {key: disposition.get(key) for key in ('status', 'target_status', 'observed_status', 'updated_at')},
        'iocs': [{'type': kind, 'value': value, 'sources': sources} for (kind, value), sources in sorted(iocs.items())],
        'warnings': warnings}
    return summary, iocs, first, last


def _event_type(name):
    """Only normalize presentation spacing/case; never merge unlike attacks."""
    return re.sub(r'\s+', '', name).casefold() if isinstance(name, str) else ''


def _monitor_time_order(member, field='first_seen_at'):
    value = _time(member.get(field), timezone.utc)
    return value is None, value or datetime.min.replace(tzinfo=timezone.utc), member.get('key', member.get('id', ''))


def _sort_items(items, field, order):
    # Missing audit times remain last in either direction. Sorting precedes
    # pagination and is repeated for children, so pages never sort locally.
    known = [item for item in items if _time(item.get(field), timezone.utc) is not None]
    unknown = [item for item in items if _time(item.get(field), timezone.utc) is None]
    return sorted(known, key=lambda item: _monitor_time_order(item, field), reverse=order == 'desc') + sorted(unknown, key=lambda item: item.get('key', item.get('id', '')))


def group_events(members, window_hours=24, enabled=True):
    """Connect nearby source intervals for the same XDR, host IP and event.

    The window limits each gap, not the group's total duration. Outcomes may
    differ; identities, evidence, notifications and permissions stay separate.
    """
    groups = []
    latest = {}
    for member, iocs, first, last in sorted(members, key=lambda item: (item[2] or datetime.min.replace(tzinfo=timezone.utc), item[0]['key'])):
        event_type = _event_type(member['name'])
        try:
            host_ip = str(ipaddress.ip_address(_host(member.get('host'))))
        except ValueError:
            host_ip = ''
        bucket = member['device'], host_ip, event_type
        chosen = None
        valid = bool(member.get('identity_valid', True) and member['device'] and host_ip and event_type
                     and member['name'] != '名称未提供' and first and last and first <= last)
        if enabled and valid:
            previous = latest.get(bucket)
            # Sorted starts and the running latest end retain overlapping or
            # long-lived intervals as bridges, independent of input order.
            if previous and (first - previous['_last']).total_seconds() <= window_hours * 3600:
                chosen = previous
        if chosen is None:
            chosen = {'_first': first, '_last': last,
                      '_common': set(iocs), 'members': []}
            groups.append(chosen)
        else:
            chosen['_first'] = min(chosen['_first'], first)
            chosen['_last'] = max(chosen['_last'], last)
            chosen['_common'] &= iocs.keys()
        chosen['members'].append(member)
        if enabled and valid:
            latest[bucket] = chosen
    output = []
    for group in groups:
        members = _sort_items(group['members'], 'first_seen_at', 'desc')
        primary = members[0]
        counts = {state: sum(m['handling'] == state for m in members) for state in HANDLING_STATES}
        assessment_counts = {state: sum(m['assessment']['status'] == state for m in members) for state in STATUSES}
        keys = sorted(m['key'] for m in members)
        hosts = sorted({_host(m['host']) or m['host'] for m in members if m['host']})
        output.append({'id': 'group:' + sha256('\0'.join(keys).encode()).hexdigest()[:24] if len(members) > 1 else primary['key'],
            'event_keys': keys, 'grouped': len(members) > 1, 'member_count': len(members),
            'name': primary['name'], 'device': primary['device'], 'device_name': primary['device_name'],
            'host': hosts[0] if len(hosts) == 1 else '', 'hosts': hosts, 'host_count': len(hosts),
            'first_event_at': _iso(group['_first']), 'last_event_at': _iso(group['_last']),
            'first_seen_at': primary['first_seen_at'], 'last_seen_at': max(m['last_seen_at'] for m in members),
            'last_processed_at': max((m.get('last_processed_at') for m in members if m.get('last_processed_at')), key=lambda stamp: _time(stamp, timezone.utc), default=None),
            'last_processed_source': _sort_items(members, 'last_processed_at', 'desc')[0].get('last_processed_source'),
            'source_closed': primary['source_closed'], 'source_closed_verdict': primary['source_closed_verdict'],
            'risk': primary['risk'], 'handling': primary['handling'], 'closure_counts': counts,
            'primary_event_key': primary['key'], 'assessment': primary['assessment'],
            'assessment_status': primary['assessment']['status'], 'assessment_counts': assessment_counts,
            'assessment_label': primary['assessment']['label'],
            'in_period': any(m['in_period'] for m in members), 'carried_over': any(m['carried_over'] for m in members),
            'is_backlog': any(m['carried_over'] for m in members),
            'mail': {field: sum(bool(m['mail'][field]) for m in members) for field in ('sent', 'replied', 'failed')},
            'ioc_matches': [{'type': kind, 'value': value} for kind, value in sorted(group['_common'])] if len(members) > 1 else [],
            'merge_reason': (f'同一 XDR、同一主机 IP、相同事件，相邻发生时间间隔不超过 {window_hours} 小时，连续合并；整组跨度可超过该间隔。各子事件独立研判和处置。'
                             if len(members) > 1 else '未找到同一 XDR、同一主机 IP、相同事件且时间相邻的其他事件；缺少有效主机 IP 或发生时间的事件保持独立。'
                             if enabled else '已关闭关联聚合，当前按原事件独立展示。'),
            'members': members})
    return _sort_items(output, 'first_seen_at', 'desc')


@contextmanager
def _read_connection():
    """Never initialize/migrate or acquire a writer lock for a polling read."""
    path = TaskStore.get_db_path()
    if not path.is_file():
        yield None
        return
    started = time.monotonic()
    db = sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=.25)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() - started > 8), 1000)
        yield db
    finally:
        db.close()


def _event_projection(column):
    # Project only correlation/list facts in SQLite. Packet bodies, stories and
    # model context are not loaded/decoded for every monthly list refresh.
    keys = ('key', 'id', 'device', 'device_name', 'host', 'name', 'risk', 'source_terminal_unmatched', 'dealStatus',
            'startTime', 'endTime', 'incidentSeverity', 'riskLevel', 'incidentThreatClass',
            'incidentThreatType', 'alertIds', 'development_sample', 'basics')
    safe = f"CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END"
    def extract(key):
        if key == 'development_sample':
            return f"json(CASE json_type({safe},'$.{key}') WHEN 'true' THEN 'true' WHEN 'false' THEN 'false' ELSE 'null' END)"
        return f"json_extract({safe},'$.{key}')"
    return 'json_object(' + ','.join(f"'{key}',{extract(key)}" for key in keys) + ')'


def _register_event_functions(db):
    def version(value):
        from .followup import event_version
        event = _json(value, {})
        return event_version(event) if isinstance(event, dict) else None
    db.create_function('monitor_event_version', 1, version, deterministic=True)


def _ioc_projection():
    """Read at most 51 tiny typed IOC records, never full packet payloads."""
    safe = "CASE WHEN json_valid(i.evidence) THEN i.evidence ELSE '[]' END"
    proof = "CASE WHEN p.type='object' THEN p.value ELSE '{}' END"
    entity = "CASE WHEN e.type='object' THEN e.value ELSE '{}' END"
    return f"""(SELECT json_group_array(json_object(
        'id',substr(json_extract(proof,'$.id'),1,100),'kind',json_extract(proof,'$.kind'),
        'event',substr(json_extract(proof,'$.event'),1,300),'device',substr(json_extract(proof,'$.device'),1,200),
        'event_revision',substr(json_extract(proof,'$.event_revision'),1,100),
        'source_name',substr(json_extract(proof,'$.source_name'),1,200),'tool',substr(json_extract(proof,'$.tool'),1,200),
        'partial',json_extract(proof,'$.partial'),'success',json_extract(proof,'$.success'),
        'query',json(json_object('action',json_extract(proof,'$.query.action'),
            'uuid',json_extract(proof,'$.query.uuid'),'entity_type',json_extract(proof,'$.query.entity_type'))),
        'value',substr(CASE json_extract(proof,'$.kind') WHEN 'ip' THEN json_extract(entity,'$.ip')
            ELSE COALESCE(json_extract(entity,'$.domain'),json_extract(entity,'$.domainName'),json_extract(entity,'$.dns')) END,1,300)))
        FROM (SELECT {proof} AS proof,{entity} AS entity FROM json_each({safe}) p
        JOIN json_each(CASE WHEN json_type({proof},'$.facts.data.item')='array' THEN json_extract({proof},'$.facts.data.item') WHEN json_type({proof},'$.facts.data.list')='array' THEN json_extract({proof},'$.facts.data.list') ELSE '[]' END) e
        WHERE p.type='object' AND e.type='object' AND CAST(p.key AS INTEGER)<24
        AND json_extract({proof},'$.kind') IN ('ip','dns') AND json_extract({proof},'$.success')=1
        AND json_type({entity},'$.threatLevel')='integer' AND json_extract({entity},'$.threatLevel')=3
        AND CAST(e.key AS INTEGER)<200 LIMIT 51))"""


def _assessment_projection():
    """Bounded proof identities and field presence, without loading packet text."""
    keys = ','.join("'" + key + "'" for key in sorted(FACT_KEYS))
    safe = "CASE WHEN json_valid(i.evidence) THEN i.evidence ELSE '[]' END"
    return f"""(SELECT json_group_array(json_object(
        'id',substr(json_extract(p.value,'$.id'),1,100),'event',substr(json_extract(p.value,'$.event'),1,300),
        'device',substr(json_extract(p.value,'$.device'),1,200),'tool',substr(json_extract(p.value,'$.tool'),1,100),
        'kind',substr(json_extract(p.value,'$.kind'),1,50),'event_revision',substr(json_extract(p.value,'$.event_revision'),1,100),
        'success',json_extract(p.value,'$.success'),'partial',json_extract(p.value,'$.partial'),
        'fact_fields',json((SELECT json_group_array(key) FROM (
            SELECT DISTINCT t.key FROM json_tree(CASE WHEN json_type(p.value,'$.facts') IN ('object','array') THEN json_extract(p.value,'$.facts') ELSE '{{}}' END) t
            WHERE t.key IN ({keys}) AND t.type IN ('text','integer','real','array')
            AND t.value IS NOT NULL AND t.value NOT IN ('','[]') LIMIT 80)))))
        FROM (SELECT value FROM json_each({safe}) WHERE type='object' LIMIT 24) p)"""


def _result_projection(column):
    safe = f"CASE WHEN json_valid({column}) THEN {column} ELSE '{{}}' END"
    fields = ('verdict', 'confidence', 'suspected_risk', 'risk_confirmed', 'blocking_gaps', 'evidence_ids', 'gaps')
    return 'json_object(' + ','.join(f"'{key}'," + (f"json(CASE json_type({safe},'$.risk_confirmed') WHEN 'true' THEN 'true' ELSE 'false' END)" if key == 'risk_confirmed' else f"json_extract({safe},'$.{key}')") for key in fields) + f",'reason',substr(json_extract({safe},'$.reason'),1,1500))"


def _select(db, sql, args=()):
    cursor = db.execute(sql, args)
    return [dict(row) for row in cursor.fetchall()]


def _installation(db, owner, scope):
    found = _select(db, 'SELECT project,policy FROM monitor_installations WHERE owner=? AND scope=?', (owner, scope)) if db and db.execute("SELECT 1 FROM sqlite_master WHERE name='monitor_installations'").fetchone() else []
    if not found:
        return None, {}, ZoneInfo('Asia/Shanghai')
    policy = _json(found[0]['policy'], {})
    policy = policy if isinstance(policy, dict) else {}
    try:
        tz = ZoneInfo(policy.get('timezone', 'Asia/Shanghai'))
    except (ValueError, TypeError, KeyError):
        tz = ZoneInfo('Asia/Shanghai')
    return found[0]['project'], policy, tz


def _dates(start_date, end_date):
    try:
        first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (ValueError, TypeError):
        raise ValueError('开始和结束日期必须为 YYYY-MM-DD') from None
    if first > last or (last-first).days >= MAX_DATE_DAYS:
        raise ValueError(f'日期范围须为顺序正确且不超过 {MAX_DATE_DAYS} 天')
    return first.isoformat(), last.isoformat()


def _list_events(owner, scope, *, start_date, end_date, window_hours=24, device=None,
                      risk=None, handling=None, assessment=None, search=None, offset=0, limit=10, group=True, include_backlog=True,
                      sort_by='first_seen_at', sort_order='desc', shape=None):
    """Date membership uses first detection; source time is only the merge key."""
    start_date, end_date = _dates(start_date, end_date)
    if type(window_hours) is not int or not 1 <= window_hours <= 24:
        raise ValueError('关联窗口必须为 1 至 24 小时')
    if type(offset) is not int or not 0 <= offset <= MAX_EVENTS or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('分页参数无效')
    if risk not in (None, '', 'risk', 'unknown', 'ignored', 'benign') or handling not in (None, '', *HANDLING_STATES):
        raise ValueError('事件筛选状态无效')
    if assessment not in (None, '', *STATUSES, *SOURCE_FILTERS):
        raise ValueError('研判结果筛选状态无效')
    if sort_by not in SORT_FIELDS or sort_order not in {'asc', 'desc'} or shape not in {None, '', 'grouped', 'ungrouped'}:
        raise ValueError('事件排序或分组筛选参数无效')
    if search is not None and (not isinstance(search, str) or len(search) > 200):
        raise ValueError('搜索内容不能超过 200 字符')
    warnings, truncated = [], False
    with _read_connection() as db:
        if db:
            db.execute('BEGIN')
        project, policy, tz = _installation(db, owner, scope)
        if db:
            _register_event_functions(db)
        period_start = datetime.fromisoformat(start_date).replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
        period_end = (datetime.fromisoformat(end_date) + timedelta(days=1)).replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
        order_column = 'current.first_seen_jd' if sort_by == 'first_seen_at' else 'last_processed_jd'
        order_direction = sort_order.upper()
        candidates = _select(db,
            'WITH visible AS (SELECT o.event_key,o.data,a.sequence,a.started_at '
            'FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
            'WHERE a.owner=? AND a.scope=? AND a.project=?), '
            "newest AS (SELECT event_key,MAX(sequence) AS sequence,MIN(julianday(started_at)) AS first_seen_jd,MAX(julianday(started_at)) AS last_seen_jd FROM visible GROUP BY event_key), "
            'current AS (SELECT v.*,n.first_seen_jd,n.last_seen_jd '
            'FROM newest n JOIN visible v ON v.event_key=n.event_key AND v.sequence=n.sequence) '
            'SELECT current.event_key,current.sequence AS selected_sequence, '
            'NULLIF(MAX(COALESCE(current.last_seen_jd,0),COALESCE(julianday(i.updated_at),0),'
            'COALESCE(julianday(d.updated_at),0),COALESCE(julianday(f.updated_at),0)),0) AS last_processed_jd, '
            '(current.first_seen_jd>=julianday(?) AND current.first_seen_jd<julianday(?)) AS in_period FROM current '
            'LEFT JOIN monitor_dispositions d ON d.rowid=(SELECT x.rowid FROM monitor_dispositions x '
            'WHERE x.owner=? AND x.scope=? AND x.project=? AND x.event_key=current.event_key ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1) '
            'LEFT JOIN monitor_event_followup f ON f.owner=? AND f.scope=? AND f.project=? AND f.event_key=current.event_key '
            'LEFT JOIN monitor_investigations i ON i.owner=? AND i.project=? AND i.event_key=current.event_key '
            'WHERE (current.first_seen_jd>=julianday(?) AND current.first_seen_jd<julianday(?)) OR '
            '(? AND (current.first_seen_jd<julianday(?) OR current.first_seen_jd IS NULL) AND '
            "NOT (COALESCE(f.state,'')='monitoring_ended' AND COALESCE(f.event_version,'')=monitor_event_version(" + _event_projection('current.data') + ')) '
            "AND COALESCE(CASE WHEN d.status='verified' AND julianday(d.updated_at)>=julianday(current.started_at) "
            'AND d.observed_status=d.target_status THEN CASE WHEN d.target_status IN (40,60) THEN 1 ELSE 0 END END, '
            "CASE WHEN json_extract(CASE WHEN json_valid(current.data) THEN current.data ELSE '{}' END,'$.source_terminal_unmatched')=1 THEN 0 WHEN json_extract(CASE WHEN json_valid(current.data) THEN current.data ELSE '{}' END,'$.dealStatus') IN (40,60) THEN 1 ELSE 0 END)=0) "
            f'ORDER BY {order_column} IS NULL,{order_column} {order_direction},current.event_key {order_direction} LIMIT ?',
            (owner, scope, project, period_start, period_end, owner, scope, project, owner, scope, project, owner, project,
             period_start, period_end, int(include_backlog), period_start, MAX_EVENTS+1)) if project else []
        if len(candidates) > MAX_EVENTS:
            candidates = candidates[:MAX_EVENTS]
            truncated = True
            order_label = ('首次检出' if sort_by == 'first_seen_at' else '最近处理') + ('由近到远' if sort_order == 'desc' else '由远到近')
            warnings.append(f'选定范围超过 {MAX_EVENTS} 条独立事件，本次按{order_label}加载 {MAX_EVENTS} 条（未知时间置后）；筛选与统计仅覆盖已加载事件，聚合组可能未加载完整，请缩小日期范围。')
        keys = [c['event_key'] for c in candidates]
        observations, investigations, dispositions, notices, replies, followups = [], [], [], [], [], []
        # Batching keeps parameter counts below SQLite's older 999-variable
        # default. All joins include the exact current installation project.
        for index in range(0, len(keys), 200):
            batch = keys[index:index+200]
            marks = ','.join('?' for _ in batch)
            args = (owner, scope, project, *batch)
            observations += _select(db,
                'WITH visible AS (SELECT o.event_key,o.data,a.sequence,a.started_at,a.session_id,a.message_id '
                'FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
                f'WHERE a.owner=? AND a.scope=? AND a.project=? AND o.event_key IN ({marks})), '
                "latest AS (SELECT event_key,MAX(sequence) AS sequence,strftime('%Y-%m-%dT%H:%M:%fZ',MIN(julianday(started_at))) AS first_seen_at,strftime('%Y-%m-%dT%H:%M:%fZ',MAX(julianday(started_at))) AS last_seen_at FROM visible GROUP BY event_key) "
                f'SELECT v.event_key,{_event_projection("v.data")} AS data,NOT json_valid(v.data) AS data_truncated,v.started_at,v.session_id,v.message_id,l.first_seen_at,l.last_seen_at '
                'FROM latest l JOIN visible v ON v.event_key=l.event_key AND v.sequence=l.sequence',
                args)
            investigations += _select(db,
                f'SELECT i.event_key,i.state,i.confirmed_risk,{_event_projection("i.event")} AS event,{_ioc_projection()} AS ioc_rows, '
                f'{_assessment_projection()} AS assessment_proofs,{_result_projection("i.result")} AS result,i.updated_at, '
                'NOT json_valid(i.event) OR NOT json_valid(i.evidence) OR NOT json_valid(i.result) AS data_truncated '
                f'FROM monitor_investigations i WHERE i.owner=? AND i.project=? AND i.event_key IN ({marks})',
                (owner, project, *batch))
            followups += _select(db, f'SELECT {PROJECTION_COLUMNS},updated_at FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND event_key IN ({marks})', args)
            dispositions += _select(db,
                'SELECT d.* FROM monitor_dispositions d WHERE d.owner=? AND d.scope=? AND d.project=? '
                f'AND d.event_key IN ({marks}) AND d.rowid=(SELECT x.rowid FROM monitor_dispositions x '
                'WHERE x.owner=d.owner AND x.scope=d.scope AND x.project=d.project AND x.event_key=d.event_key ORDER BY x.created_at DESC,x.rowid DESC LIMIT 1)', args)
            notices += _select(db,
                'SELECT id,event_key,state,recipient,sent_at,updated_at,error FROM monitor_mail_notices '
                f'WHERE owner=? AND scope=? AND project=? AND event_key IN ({marks})', args)
        ids = [n['id'] for n in notices]
        for index in range(0, len(ids), 200):
            batch = ids[index:index+200]
            marks = ','.join('?' for _ in batch)
            replies += _select(db,
                'SELECT n.id,COUNT(DISTINCT r.id) AS count FROM monitor_mail_notices n '
                'JOIN monitor_mail_replies r ON r.owner=n.owner AND r.project=n.project '
                'AND ((EXISTS(SELECT 1 FROM monitor_mail_items i WHERE i.owner=n.owner AND i.project=n.project AND i.notice_id=n.id AND i.reply_id=r.id)) '
                "OR (json_valid(r.payload) AND json_array_length(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.notice_ids')=1 AND EXISTS(SELECT 1 FROM json_each(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.notice_ids') j WHERE j.value=n.id))) "
                f'WHERE n.owner=? AND n.scope=? AND n.project=? AND n.id IN ({marks}) AND r.state NOT IN (\'unrelated\',\'forwarding\') GROUP BY n.id',
                (owner, scope, project, *batch))
    from .followup import projection
    followups = {r['event_key']: {**projection(r), 'updated_at': r['updated_at']} for r in followups}
    investigations = {r['event_key']: r for r in investigations}
    dispositions = {r['event_key']: r for r in dispositions}
    notices = {r['event_key']: r for r in notices}
    replies = {r['id']: r['count'] for r in replies}
    names = policy.get('device_names') if isinstance(policy.get('device_names'), dict) else {}
    all_members = []
    period = {c['event_key']: bool(c['in_period']) for c in candidates}
    for row in observations:
        row['in_period'] = period[row['event_key']]
        case = investigations.get(row['event_key'], {})
        if row['data_truncated'] or case.get('data_truncated'):
            truncated = True
            warnings.append('部分事件或证据未能完整读取；列表保留事件概要，请打开详情或缩小查询范围。')
        notice = notices.get(row['event_key'], {})
        member = _summarize(row, case, dispositions.get(row['event_key'], {}), notice,
                            replies.get(notice.get('id'), 0), names, tz, followups.get(row['event_key']))
        if member[0]['in_period'] or include_backlog and member[0]['handling'] not in {'closed', 'ignored', 'ended'}:
            all_members.append(member)
        if any('前 50 条' in note for note in member[0]['warnings']):
            truncated = True
            warnings.append('部分事件的关联指标只加载了概要；同类事件聚合不依赖这些指标，完整证据请查看详情。')
    devices = sorted({(m[0]['device'], m[0]['device_name']) for m in all_members if m[0]['device']})
    needle = search.casefold().strip() if search else ''
    base_members = [m for m in all_members if (not device or m[0]['device'] == device)
        and (not needle or needle in ' '.join(str(m[0].get(k, '')) for k in ('id', 'name', 'host', 'device_name')).casefold()
             or any(needle in ioc['value'].casefold() for ioc in m[0]['iocs']))]

    def matches(member):
        value = member[0]
        if risk and value['risk'] != risk or handling and value['handling'] != handling:
            return False
        if assessment == 'ended':
            return value['source_closed']
        if assessment in SOURCE_FILTERS:
            return value['source_closed'] and value['source_closed_verdict'] == assessment.removeprefix('source_closed_')
        return not assessment or value['assessment']['status'] == assessment and not value['source_closed']

    base_items = group_events(base_members, window_hours, enabled=group)
    members = [member for member in base_members if matches(member)]
    items = group_events(members, window_hours, enabled=group)
    if shape:
        items = [item for item in items if item['grouped'] == (shape == 'grouped')]
        selected_keys = {member['key'] for item in items for member in item['members']}
        members = [member for member in members if member[0]['key'] in selected_keys]
    items = _sort_items(items, sort_by, sort_order)
    for item in items:
        item['members'] = _sort_items(item['members'], sort_by, sort_order)
    metrics = _metrics(members, items)
    summary_metrics = _metrics(base_members, base_items)
    return {'start_date': start_date, 'end_date': end_date, 'timezone': str(tz), 'window_hours': window_hours,
        'items': items[offset:offset+limit], 'total': len(items), 'has_more': len(items) > offset+limit,
        'offset': offset, 'limit': limit, 'metrics': metrics, 'summary_metrics': summary_metrics,
        '_risk_restore': (project, [key for key, case in investigations.items() if case.get('confirmed_risk') is None]),
        'sort_by': sort_by, 'sort_order': sort_order, 'shape': shape,
        'devices': [{'id': identifier, 'name': name} for identifier, name in devices],
        'truncated': truncated, 'warnings': list(dict.fromkeys(warnings)),
        'include_backlog': bool(include_backlog),
        'date_basis': '日期范围按监测首次检出时间筛选；首次检出与最近处理均支持排序，原始发生时间只作详情与聚合依据。此前未完成事件可选纳入，聚合主行采用最新检出子事件的研判。'}



def _metrics(members, items):
    values = [member[0] for member in members]
    assessment_counts = {state: sum(value['source_closed'] if state == 'ended' else
        value['assessment']['status'] == state and not value['source_closed'] for value in values) for state in STATUSES}
    return {'events': len(values), 'rows': len(items),
        'period_events': sum(value['in_period'] for value in values), 'carried_over': sum(value['carried_over'] for value in values),
        'events_in_range': sum(value['in_period'] for value in values), 'backlog_events': sum(value['carried_over'] for value in values),
        'groups': sum(item['grouped'] for item in items), 'ungrouped': sum(not item['grouped'] for item in items),
        **{state: sum(value['handling'] == state for value in values) for state in HANDLING_STATES},
        'unknown_time_events': sum(value['occurrence_time_unknown'] for value in values),
        'assessment_counts': assessment_counts,
        'source_closed_counts': {verdict: sum(value['source_closed'] and value['source_closed_verdict'] == verdict for value in values)
                                 for verdict in ('risk', 'benign', 'unknown')},
        'mail_sent': sum(value['mail']['sent'] for value in values), 'mail_replied': sum(value['mail']['replied'] for value in values)}


def _confirmed_history(owner, project, event, investigation, assessment):
    """Load cited original packets for display, never as current authority.

    The durable snapshot is field-presence only. Read the exact saved revision
    and cited proof objects individually so a large evidence array cannot turn
    valid JSON into a broken prefix or borrow another incident's packets.
    """
    if not assessment.get('confirmed_risk_retained'):
        return None, False, []
    from .conclusions import validated_snapshot
    from .evidence_presenter import bound_evidence
    snapshot = _json(investigation.get('confirmed_risk'), {})
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    result = validated_snapshot(snapshot, event)
    canonical = snapshot.get('event')
    revision = snapshot.get('revision')
    if not result or not isinstance(canonical, dict) or type(revision) is not int:
        return None, True, ['此前确认风险的原始依据未能定位到对应调查版本；已确认的风险事实仍保留。']
    references = [ref for ref in result.get('evidence_ids', []) if isinstance(ref, str) and ref]
    references = list(dict.fromkeys(references))
    warnings = []
    if len(references) > MAX_CONFIRMED_PROOFS:
        warnings.append(f'此前确证依据本页只恢复前 {MAX_CONFIRMED_PROOFS} 条引用，其余原始依据保留在调查版本中。')
    references = references[:MAX_CONFIRMED_PROOFS]
    if not references:
        return None, True, ['此前确证结论未保留可定位的原始依据引用；不能据此认为未曾取得证据。']
    marks = ','.join('?' for _ in references)
    with _read_connection() as db:
        records = _select(db,
            "SELECT json_extract(p.value,'$.id') AS id,COUNT(*) AS matches,"
            'MAX(length(p.value)) AS proof_size, '
            'CASE WHEN COUNT(*)=1 AND MAX(length(p.value))<=? THEN p.value ELSE NULL END AS proof '
            'FROM monitor_investigation_versions v '
            "JOIN json_each(CASE WHEN json_valid(v.evidence) THEN v.evidence ELSE '[]' END) p "
            'WHERE v.owner=? AND v.project=? AND v.event_key=? AND v.revision=? '
            "AND json_extract(CASE WHEN json_valid(v.event) THEN v.event ELSE '{}' END,'$.device')=? "
            "AND json_extract(CASE WHEN json_valid(v.event) THEN v.event ELSE '{}' END,'$.id')=? "
            "AND p.type='object' AND CAST(p.key AS INTEGER)<24 "
            f"AND json_extract(p.value,'$.id') IN ({marks}) "
            "AND json_extract(p.value,'$.success')=1 GROUP BY json_extract(p.value,'$.id')",
            (MAX_DETAIL_JSON, owner, project, event['key'], revision, canonical['device'], canonical['id'], *references))
    proofs = []
    for record in records:
        if record['matches'] != 1:
            warnings.append('此前确证依据的原始引用存在重复，未选用有歧义的记录。')
        elif record['proof'] is None:
            warnings.append('部分此前确证依据超过详情读取上限，原始记录仍保留在调查版本中。')
        else:
            proof = _json(record['proof'], {})
            if isinstance(proof, dict):
                proofs.append(proof)
    bound = bound_evidence(canonical, proofs, canonical=canonical)
    if len(bound) < len(references):
        warnings.append('部分此前确证依据暂未加载，已确认的风险事实仍保留；当前补查依据与历史依据分别展示。')
    story = build_story(canonical, bound, result, canonical=canonical)
    return story, bool(warnings), list(dict.fromkeys(warnings))

def _detail(owner, scope, event_key):
    """Bounded original-incident detail; a visual group grants no extra access."""
    if not isinstance(event_key, str) or not event_key or len(event_key) > 1000:
        raise ValueError('事件标识无效')
    warnings, truncated = [], False
    with _read_connection() as db:
        if db:
            db.execute('BEGIN')
        project, policy, tz = _installation(db, owner, scope)
        if not project:
            raise LookupError('没有可访问的监测事件')
        latest = _select(db,
            'SELECT substr(o.data,1,?) AS data,length(o.data)>? AS data_truncated '
            'FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
            'WHERE a.owner=? AND a.scope=? AND a.project=? AND o.event_key=? ORDER BY a.sequence DESC LIMIT 1',
            (MAX_DETAIL_JSON, MAX_DETAIL_JSON, owner, scope, project, event_key))
        if not latest:
            raise LookupError('没有可访问的监测事件')
        observations = _select(db,
            'SELECT a.business_date,a.started_at AS observed_at,a.session_id AS sessionID,a.message_id AS messageID '
            'FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
            'WHERE a.owner=? AND a.scope=? AND a.project=? AND o.event_key=? ORDER BY a.sequence DESC LIMIT ?',
            (owner, scope, project, event_key, MAX_DETAIL_ROWS+1))
        seen = _select(db,
            "SELECT strftime('%Y-%m-%dT%H:%M:%fZ',MIN(julianday(a.started_at))) AS first_seen_at,strftime('%Y-%m-%dT%H:%M:%fZ',MAX(julianday(a.started_at))) AS last_seen_at "
            'FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
            'WHERE a.owner=? AND a.scope=? AND a.project=? AND o.event_key=?',
            (owner, scope, project, event_key))[0]
        followups = _select(db, f'SELECT {PROJECTION_COLUMNS},updated_at FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND event_key=?', (owner, scope, project, event_key))
        cases = _select(db,
            'SELECT state,confirmed_risk,substr(event,1,?) AS event,substr(evidence,1,?) AS evidence,substr(result,1,?) AS result,updated_at, '
            'length(event)>? OR length(evidence)>? OR length(result)>? AS data_truncated '
            'FROM monitor_investigations WHERE owner=? AND project=? AND event_key=?',
            (MAX_DETAIL_JSON, MAX_DETAIL_JSON, MAX_DETAIL_JSON, MAX_DETAIL_JSON, MAX_DETAIL_JSON, MAX_DETAIL_JSON, owner, project, event_key))
        dispositions = _select(db,
            'SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND project=? AND event_key=? ORDER BY created_at DESC,rowid DESC LIMIT ?',
            (owner, scope, project, event_key, MAX_DETAIL_ROWS+1))
        notices = _select(db,
            'SELECT id,state,recipient,subject,substr(body,1,?) AS body,length(body)>? AS data_truncated,error,sent_at,created_at,updated_at '
            'FROM monitor_mail_notices WHERE owner=? AND scope=? AND project=? AND event_key=?',
            (MAX_DETAIL_JSON, MAX_DETAIL_JSON, owner, scope, project, event_key))
        notice = notices[0] if notices else None
        replies = _select(db,
            'SELECT r.id,r.sender,r.state,substr(r.payload,1,?) AS payload,length(r.payload)>? AS data_truncated, '
            'substr(r.result,1,?) AS result,r.error,r.received_at,r.updated_at '
            'FROM monitor_mail_replies r WHERE r.owner=? AND r.project=? AND r.state NOT IN (\'unrelated\',\'forwarding\') AND '
            '(EXISTS(SELECT 1 FROM monitor_mail_items i WHERE i.owner=r.owner AND i.project=r.project AND i.notice_id=? AND i.reply_id=r.id) '
            "OR (json_valid(r.payload) AND json_array_length(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.notice_ids')=1 AND EXISTS(SELECT 1 FROM json_each(CASE WHEN json_valid(r.payload) THEN r.payload ELSE '{}' END,'$.notice_ids') j WHERE j.value=?))) "
            'ORDER BY r.sequence DESC LIMIT ?',
            (MAX_DETAIL_JSON, MAX_DETAIL_JSON, MAX_DETAIL_JSON, owner, project, notice['id'], notice['id'], MAX_DETAIL_ROWS+1)) if notice else []
    for collection in (observations, dispositions, replies):
        if len(collection) > MAX_DETAIL_ROWS:
            truncated = True
            warnings.append(f'详情仅展开最近 {MAX_DETAIL_ROWS} 条相关记录，较早历史未在此页加载。')
            del collection[MAX_DETAIL_ROWS:]
    if any(r.get('data_truncated') for r in latest+cases+replies+notices):
        truncated = True
        warnings.append('部分详情超出单字段读取上限，不能将未显示部分理解为没有证据。')
    event = _json(latest[0]['data'], {})
    event = event if isinstance(event, dict) else {}
    event['key'] = event_key
    case = cases[0] if cases else {}
    investigation = {**case, 'event': _json(case.get('event'), {}), 'evidence': _json(case.get('evidence'), []), 'result': _json(case.get('result'), {})}
    if notice:
        notice['replies'] = [{**r, 'payload': _json(r['payload'], {}), 'result': _json(r['result'], {})} for r in replies]
    # Data was already sanitized on ingestion, and a final redact guards older
    # rows that predate private capture/packet sanitization. Keep plain strings.
    row = observations[0]
    summary, _, _, _ = _summarize({'event_key': event_key, 'data': json.dumps(event),
        'started_at': row['observed_at'], 'session_id': row['sessionID'], 'message_id': row['messageID'],
        'first_seen_at': seen['first_seen_at'], 'last_seen_at': seen['last_seen_at']},
        case, dispositions[0] if dispositions else {}, notice or {}, len(replies),
        policy.get('device_names') if isinstance(policy.get('device_names'), dict) else {}, tz, {**followup_projection(followups[0]), 'updated_at': followups[0]['updated_at']} if followups else None)
    event.update(summary)
    investigation['is_current'] = summary['investigation']['is_current']
    if summary['investigation']['state'] == 'stale':
        investigation['stored_state'] = investigation.get('state')
        investigation['historical_result'] = investigation['result']
        investigation['state'] = 'stale'
        investigation['result'] = {'verdict': 'unknown', 'reason': summary['investigation']['reason'],
            'gaps': ['当前事件版本与历史取证版本不同，旧证据未用来自动关联或判定安全。']}
    from .evidence_presenter import bound_evidence
    canonical = investigation['event'] if isinstance(investigation['event'], dict) else {}
    current_proofs = bound_evidence(event, investigation['evidence'], canonical=canonical)
    display_proofs = bound_evidence(event, investigation['evidence'], canonical=canonical, include_failed=True)
    from .source_event import source_event_evidence
    story = build_story(event, display_proofs,
                        investigation['result'] if isinstance(investigation['result'], dict) else {}, canonical=canonical)
    source_card, source_timeline = source_event_evidence(event, display_proofs, has_details=bool(story['evidence_cards']))
    overview = readable_story({**story, 'verdict': summary['assessment']['verdict'],
        'conclusion': summary['assessment']['reason'], 'gaps': summary['assessment']['gaps']})
    from .case_overview import operator_overview
    overview.update(operator_overview(event, investigation, summary['assessment'], story, current_proofs))
    confirmed_story, history_truncated, history_warnings = _confirmed_history(owner, project, event, investigation, summary['assessment'])
    truncated = truncated or history_truncated
    warnings.extend(history_warnings)
    output = {'event': event, 'overview': overview, 'story': story, 'investigation': investigation, 'dispositions': dispositions, 'notice': notice,
        'source_event_evidence': source_card, 'source_event_timeline': source_timeline,
        'confirmed_story': confirmed_story,
        'confirmed_evidence_scope': ('previous_activity' if summary['assessment'].get('risk_scope') == 'previous_activity' else 'current_activity') if confirmed_story is not None else None,
        'confirmed_at': summary['assessment'].get('confirmed_at') if summary['assessment'].get('confirmed_risk_retained') else None,
        'observations': [{k: v for k, v in row.items() if k not in {'data', 'data_truncated'}} for row in observations],
        'timezone': str(tz), 'truncated': truncated, 'warnings': list(dict.fromkeys(warnings)),
        '_risk_restore': (project, [event_key] if case and case.get('confirmed_risk') is None else [])}
    return _redacted(output)


async def list_events(owner, scope, *, start_date, end_date, window_hours=24, device=None,
                      risk=None, handling=None, assessment=None, search=None, offset=0, limit=10, group=True, include_backlog=True,
                      sort_by='first_seen_at', sort_order='desc', shape=None):
    arguments = dict(start_date=start_date, end_date=end_date,
        window_hours=window_hours, device=device, risk=risk, handling=handling, assessment=assessment, search=search,
        offset=offset, limit=limit, group=group, include_backlog=include_backlog,
        sort_by=sort_by, sort_order=sort_order, shape=shape)
    result = await asyncio.to_thread(_list_events, owner, scope, **arguments)
    project, missing = result.pop('_risk_restore')
    if missing:
        from .conclusions import restore_confirmed_risks
        await restore_confirmed_risks(owner, project, missing)
        result = await asyncio.to_thread(_list_events, owner, scope, **arguments)
        result.pop('_risk_restore')
    return result


async def detail(owner, scope, event_key):
    result = await asyncio.to_thread(_detail, owner, scope, event_key)
    project, missing = result.pop('_risk_restore')
    if missing:
        from .conclusions import restore_confirmed_risks
        await restore_confirmed_risks(owner, project, missing)
        result = await asyncio.to_thread(_detail, owner, scope, event_key)
        result.pop('_risk_restore')
    return result


def _redacted(value, depth=0):
    if depth > 64:
        return '[内容层级超限]'
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redacted(item, depth+1) for item in value]
    if isinstance(value, dict):
        return {key: '[已脱敏]' if re.fullmatch(_SECRET_NAME, key, re.I)
                else _redacted(item, depth+1) for key, item in value.items()}
    return value
