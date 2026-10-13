"""Durable next-round incident decisions, independent of mail availability.

Observation is a local business state. Only a persisted, version-bound intent
from a prior round can authorize one XDR status write. An unknown write result
is recovered exclusively by reading the original incident again.
"""
import asyncio
import hashlib
import json
from uuid import uuid4

from flocks.session.interaction_policy import automatic_mark_scope
from . import disposition as d
from .adapter import ContractError, XdrAdapter, normalize
from .automatic import read_event
from .assessment import assess_event
from .models import MonitoringPolicy
from .store import connection, encode, rows, write

PROJECTION_COLUMNS = 'event_key,state,watch_count,last_round,queued_round,target_status,disposition_id,event_version,policy_revision,result'
_TERMINAL = {'verified', 'external_closed'}
_UNCERTAIN = {'writing', 'pending'}


def policy_revision(policy):
    return hashlib.sha256(json.dumps(policy.model_dump(mode='json'), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def event_version(event):
    """The source disposition alone changes no incident activity/evidence."""
    from .investigation import fingerprint
    current = {**event, 'dealStatus': None}
    if isinstance(current.get('basics'), dict):
        current['basics'] = {key: value for key, value in current['basics'].items()
                             if key not in {'deal_status', 'dealStatus'}}
    return fingerprint(json.loads(json.dumps(current, sort_keys=True, ensure_ascii=False, default=str)))


def _identity(policy, event):
    device, key, identifier = event.get('device'), event.get('key'), event.get('id')
    if device not in policy.devices or not identifier or key != f'{device}:incident:{identifier}':
        raise ContractError('事件身份或设备绑定不一致，不能安排下一轮状态标记')
    return (policy.owner, policy.scope, policy.project, device, key)


def _medium_or_lower(event):
    # The native incidentSeverity enum is ascending; legacy riskLevel descends.
    level = event.get('incidentSeverity')
    if event.get('severitySource') == 'incidentSeverity':
        return type(level) is int and level in {1, 2}
    level = event.get('riskLevel')
    return (event.get('severitySource') == 'riskLevel' and type(level) is int
            and level in {2, 3, 4})


def _suspected(event, persisted=None):
    case = persisted or event.get('investigation') or {}
    result = case.get('result', case)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = {}
    return (result.get('verdict') == 'risk' or result.get('risk_confirmed') is True
            or result.get('suspected_risk') is True or event.get('suspected_risk') is True)


def _complete_observation(event, query_complete, persisted=None):
    attached = event.get('investigation') or {}
    case = persisted or attached
    result = case.get('result', case)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            result = {}
    evidence = case.get('evidence', [])
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            evidence = []
    canonical = case.get('event', event)
    if isinstance(canonical, str):
        try:
            canonical = json.loads(canonical)
        except (TypeError, ValueError):
            return False
    from .investigation import fresh_evidence
    # An exact successful source recheck can count with a still-valid cached
    # conclusion. Expired/failed/partial evidence and interrupted investigation
    # cannot turn timer ticks into successful observations.
    return bool(query_complete and case.get('state') == 'ready'
                and attached.get('state', 'ready') == 'ready'
                and event_version(canonical) == event_version(event)
                and bool(evidence) and fresh_evidence(evidence, canonical) == evidence
                and not case.get('error_kind') and not result.get('error_kind')
                and not any(not proof.get('success') or proof.get('partial') for proof in evidence
                            if isinstance(proof, dict)))


def projection(item):
    """A small API projection; never treats observation as an XDR closure."""
    if not item:
        return None
    result = json.loads(item.get('result') or '{}')
    return {'state': item['state'], 'watch_count': item['watch_count'],
            'last_round': item.get('last_round'), 'queued_round': item.get('queued_round'),
            'target_status': item.get('target_status'), 'disposition_id': item.get('disposition_id'),
            'event_version': item['event_version'], 'policy_revision': item['policy_revision'],
            'observed_status': result.get('observed_status'), 'reason': result.get('reason'),
            'external': item['state'] == 'external_closed',
            'closed': item['state'] in _TERMINAL, 'result': result}


async def snapshot(policy, event_key=None, *, include_event=False):
    sql = 'SELECT ' + PROJECTION_COLUMNS + (',event' if include_event else '') + ' FROM monitor_event_followup WHERE owner=? AND scope=? AND project=?'
    args = (policy.owner, policy.scope, policy.project)
    if event_key is not None:
        sql += ' AND event_key=?'
        args += (event_key,)
    result = {}
    for item in await rows(sql, args):
        projected = projection(item)
        if include_event:
            projected['event'] = json.loads(item['event'])
        result[item['event_key']] = projected
    return result


async def observe(policy, event, round_id, *, query_complete=True):
    """Record a conclusion now; a different future round consumes its intent.

    Only complete, successful investigation rounds count toward uncertainty
    expiry. High/unknown severity and suspected risk never expire this way.
    """
    if not isinstance(round_id, str) or not round_id:
        raise ValueError('观察轮次必须具有稳定编号')
    identity = _identity(policy, event)
    revision, version, stamp = policy_revision(policy), event_version(event), d.stamp()
    cases = await rows('SELECT * FROM monitor_investigations WHERE owner=? AND project=? AND event_key=?',
                       (policy.owner, policy.project, event['key']))
    case = cases[0] if cases else None
    assessment = assess_event(event, case)
    complete = _complete_observation(event, query_complete, case)
    async with connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        cursor = await db.execute('SELECT * FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=?', identity)
        existing = await cursor.fetchone()
        old = dict(existing) if existing else None
        same = bool(old and old['policy_revision'] == revision and old['event_version'] == version)
        if same and old['state'] in _TERMINAL and event.get('dealStatus') not in {40,60} and query_complete:
            same = False  # Source explicitly reopened; preserve audit, release old closure.
        if same and old['state'] in _TERMINAL | _UNCERTAIN | {'queued'}:
            return projection(old)
        count = old['watch_count'] if same else 0
        last_round = old['last_round'] if same else None
        previous_result = json.loads(old['result']) if same else {}
        observed_rounds = previous_result.get('observed_rounds', [])
        observed_rounds = [v for v in observed_rounds if isinstance(v, str)] if isinstance(observed_rounds, list) else []
        if last_round and last_round not in observed_rounds:
            observed_rounds.append(last_round)
        if complete and round_id not in observed_rounds and count < 144:
            count += 1
            observed_rounds.append(round_id)
            last_round = round_id
        state, target, reason = 'watching', None, '证据不足，继续观察；尚未修改 XDR 状态。'
        if assessment['verdict'] == 'risk':
            state, reason = 'risk_open', '已证实风险，等待责任人处置反馈。'
        elif assessment['verdict'] == 'benign' and assessment['risk_excluded'] and complete:
            state, target, reason = 'queued', 60, '已取得高置信无风险结论，下一轮标记忽略并回查。'
        elif (complete and policy.observation_rounds > 0 and count >= policy.observation_rounds
              and _medium_or_lower(event) and not _suspected(event, case)):
            state, target, reason = 'queued', 60, '中低等级事件持续观察仍证据不足，下一轮接受风险标记忽略；不等于确认无风险。'
        await db.execute('INSERT INTO monitor_event_followup(owner,scope,project,device,event_key,policy_revision,event_version,event,decision,state,watch_count,last_round,queued_round,target_status,result,created_at,updated_at) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,scope,project,device,event_key) DO UPDATE SET '
                         'policy_revision=excluded.policy_revision,event_version=excluded.event_version,event=excluded.event,'
                         'decision=excluded.decision,state=excluded.state,watch_count=excluded.watch_count,last_round=excluded.last_round,'
                         'queued_round=excluded.queued_round,target_status=excluded.target_status,disposition_id=NULL,result=excluded.result,updated_at=excluded.updated_at',
                         (*identity, revision, version, encode(event), encode(assessment), state, count, last_round,
                          round_id if target else None, target, encode({'reason': reason, 'observed_rounds': observed_rounds[:144], 'decision_kind': assessment['verdict'] if assessment['verdict'] in {'benign', 'risk'} else 'uncertain'}), stamp, stamp))
        cursor = await db.execute('SELECT * FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=?', identity)
        return projection(dict(await cursor.fetchone()))


async def authorized(policy):
    """Current installation consent authorizes this policy, never the old toggle."""
    from .lifecycle import _owned_installation
    from flocks.hub import local
    from flocks.project.project import Project
    entry, scheduler, current = await _owned_installation(policy.owner)
    record = local.get_record('component', policy.scope)
    if (not record or not record.enabled or not entry['ready'] or current != policy
            or scheduler.status.value != 'active'):
        raise ContractError('监测已暂停、停用或配置变化，本次不执行状态标记')
    if Project.registry_state(policy.project, owner_id=policy.owner) != 'active':
        raise ContractError('监测项目不可用，本次不执行状态标记')


class FollowupAdapter(XdrAdapter):
    allowed_actions = frozenset({'list', 'update_status'})

    async def call(self, device, params, message_id):
        if params.get('action') != 'update_status':
            return await super().call(device, params, message_id)
        await authorized(self.policy)
        identifiers = params.get('uuids')
        if device not in self.policy.devices or not isinstance(identifiers, list) or len(identifiers) != 1:
            raise PermissionError('下一轮标记每次只允许操作一个受控事件')
        # This adapter is internal and only used after durable intent selection.
        if params.get('deal_status') not in {40, 60}:
            raise PermissionError('下一轮只允许已授权的处置完成或忽略状态')
        with automatic_mark_scope(self.policy.tool_for(device), device, identifiers[0], params['deal_status'], self.session_id):
            return await super().call(device, params, message_id)


async def _finish(item, state, *, observed=None, reason=None):
    result = {**json.loads(item['result']), 'observed_status': observed}
    if reason:
        result['reason'] = reason
    await write('UPDATE monitor_event_followup SET state=?,result=?,updated_at=? WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=? AND policy_revision=? AND event_version=?',
                (state, encode(result), d.stamp(), item['owner'], item['scope'], item['project'], item['device'], item['event_key'], item['policy_revision'], item['event_version']))
    item = {**item, 'state': state, 'result': encode(result)}
    return projection(item)


async def _audit_finish(item, state, observed=None, error=None):
    if item.get('disposition_id'):
        # The disposition audit is authoritative for uncertain network writes.
        await write("UPDATE monitor_dispositions SET status=?,observed_status=?,error=?,updated_at=? WHERE owner=? AND scope=? AND project=? AND id=? AND (status!='verified' OR ?='verified')",
                    (state, observed, error, d.stamp(), item['owner'], item['scope'], item['project'], item['disposition_id'], state))


async def _verified_local_disposition(policy, event, status):
    """A previously verified owner/incident-bound write is not external work."""
    audits = await rows("SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND project=? AND event_key=? AND status='verified' AND target_status=? AND observed_status=? AND mode IN ('mail','mail_manual','followup') ORDER BY updated_at DESC LIMIT 20",
                        (policy.owner, policy.scope, policy.project, event['key'], status, status))
    for audit in audits:
        detail = json.loads(audit['decision'])
        if detail.get('event_version') == event_version(event):
            return audit
        notice_id = detail.get('notice')
        if not notice_id:
            continue
        notices = await rows('SELECT event FROM monitor_mail_notices WHERE id=? AND owner=? AND scope=? AND project=? AND event_key=?',
                            (notice_id, policy.owner, policy.scope, policy.project, event['key']))
        if not notices:
            continue
        original = json.loads(notices[0]['event'])
        expected_end = detail.get('observed_end_time', original.get('notified_end_time', original.get('endTime')))
        expected_host = detail.get('observed_host', original.get('notified_host', original.get('host')))
        if (original.get('device') == event['device'] and original.get('id') == event['id']
                and expected_end is not None and expected_end == event.get('endTime')
                and (expected_host or '') == (event.get('host') or '')):
            return audit
    return None


async def _bind_verified_audit(item, audit):
    if not audit:
        return item
    await write('UPDATE monitor_event_followup SET disposition_id=?,target_status=? WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=? AND event_version=? AND policy_revision=?',
                (audit['id'], audit['target_status'], item['owner'], item['scope'], item['project'], item['device'], item['event_key'], item['event_version'], item['policy_revision']))
    return {**item, 'disposition_id': audit['id'], 'target_status': audit['target_status']}


def _case_token(case):
    return tuple(case.get(key) for key in ('state', 'event', 'evidence', 'result', 'revision', 'updated_at'))


async def _current_intent(item, policy, event):
    cases = await rows('SELECT * FROM monitor_investigations WHERE owner=? AND project=? AND event_key=?',
                       (policy.owner, policy.project, event['key']))
    if not cases:
        return False, None
    case = cases[0]
    assessment = assess_event(event, case)
    current_event = {**event, 'investigation': {'state': case['state']}}
    complete = _complete_observation(current_event, True, case)
    kind = json.loads(item['result']).get('decision_kind')
    allowed = complete and (assessment['verdict'] == 'benign' and assessment['risk_excluded'] if kind == 'benign'
        else (kind == 'uncertain' and assessment['verdict'] == 'unknown' and _medium_or_lower(event)
              and not _suspected(event, case) and policy.observation_rounds > 0
              and item['watch_count'] >= policy.observation_rounds))
    return bool(allowed), case


async def _process(item, policy, session_id, adapter):
    event = json.loads(item['event'])
    if item['policy_revision'] != policy_revision(policy) or event.get('device') not in policy.devices:
        return await _finish(item, 'invalidated', reason='监测配置或设备绑定变化，旧标记意图已失效。')
    await authorized(policy)
    raw = await read_event(adapter, event)  # Exact UUID, no status/whitelist/time filters.
    current = raw.get('dealStatus')
    if type(current) is not int or current not in d.LIST_RESPONSE_STATUSES:
        raise ContractError('XDR 返回未知处置状态，不能改写或确认结束')
    fresh = normalize(event['device'], raw)
    if event_version(fresh) != item['event_version']:
        await _audit_finish(item, 'pending', current, '原事件已有新活动，旧意图不再授权写入')
        return await _finish(item, 'invalidated', observed=current, reason='事件主机、时间或其他活动信息变化，旧意图失效，需重新调查。')
    if current in {40, 60}:
        audit = await _verified_local_disposition(policy, event, current)
        item = await _bind_verified_audit(item, audit)
        own_target = bool(audit) or (item['state'] in _UNCERTAIN and current == item['target_status'])
        await _audit_finish(item, 'verified' if own_target else 'mismatch', current)
        return await _finish(item, 'verified' if own_target else 'external_closed', observed=current,
                             reason='XDR 回查确认目标状态。' if own_target else '按原事件编号回查确认 XDR 已结束，记录外部处置结果。')
    if item['state'] in _UNCERTAIN:
        await _audit_finish(item, 'pending', current, '写入结果仍待回查，禁止重复写入')
        return await _finish(item, 'pending', observed=current, reason='此前写入结果待确认，本轮只回查，不重复标记。')
    if current not in {0, 10} or raw.get('whiteStatus') not in {'未加白', '部分加白'}:
        return await _finish(item, 'invalidated', observed=current, reason='XDR 处置状态或加白范围变化，未执行旧标记意图。')
    notices = await rows("SELECT 1 FROM monitor_mail_notices WHERE owner=? AND scope=? AND project=? AND event_key=? AND state NOT IN ('skipped','cancelled') LIMIT 1", (policy.owner, policy.scope, policy.project, event['key']))
    if notices:
        return await _finish(item, 'watching', reason='已有责任人通知，等待反馈；观察到期不能绕过邮件跟进自动忽略。')
    # Re-read just before intent claim. Hook boundaries and current policy are
    # checked again inside FollowupAdapter on the only mutating API call.
    check = await read_event(adapter, event)
    if any(check.get(key) != raw.get(key) for key in ('dealStatus', 'endTime', 'whiteStatus', 'hostIp')) or event_version(normalize(event['device'], check)) != item['event_version']:
        return await _finish(item, 'invalidated', reason='写入前原事件发生变化，本轮未修改状态。')
    await authorized(policy)
    valid, latest_case = await _current_intent(item, policy, event)
    if not valid:
        return await _finish(item, 'watching', reason='最新研判或证据有效性变化，已撤销旧忽略意图，继续观察调查。')
    disposition_id, stamp = str(uuid4()), d.stamp()
    async with connection() as db:
        await db.execute('BEGIN IMMEDIATE')
        unresolved = await db.execute("SELECT 1 FROM monitor_dispositions WHERE owner=? AND scope=? AND project=? AND event_key=? AND status IN ('queued','writing','pending','mismatch') AND id!=? LIMIT 1",
            (policy.owner, policy.scope, policy.project, event['key'], item.get('disposition_id') or ''))
        if await unresolved.fetchone():
            result = {**json.loads(item['result']), 'reason': '已有人工或邮件处理反馈待写回、待回查，保留事件并等待，不执行旧忽略意图。'}
            await db.execute("UPDATE monitor_event_followup SET state='watching',result=?,updated_at=? WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=? AND state='queued' AND policy_revision=? AND event_version=?",
                (encode(result), d.stamp(), item['owner'], item['scope'], item['project'], item['device'], item['event_key'], item['policy_revision'], item['event_version']))
            return projection({**item, 'state': 'watching', 'result': encode(result)})
        latest = await db.execute('SELECT * FROM monitor_investigations WHERE owner=? AND project=? AND event_key=?', (policy.owner, policy.project, event['key']))
        current_case = await latest.fetchone()
        if not current_case or _case_token(dict(current_case)) != _case_token(latest_case):
            await db.execute("UPDATE monitor_event_followup SET state='watching',updated_at=? WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=? AND state='queued' AND policy_revision=? AND event_version=?",
                (d.stamp(), item['owner'], item['scope'], item['project'], item['device'], item['event_key'], item['policy_revision'], item['event_version']))
            return {**projection(item), 'state': 'watching'}
        cur = await db.execute("UPDATE monitor_event_followup SET state='writing',disposition_id=?,updated_at=? WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=? AND state='queued' AND policy_revision=? AND event_version=?", (disposition_id, stamp, item['owner'], item['scope'], item['project'], item['device'], item['event_key'], item['policy_revision'], item['event_version']))
        if cur.rowcount != 1:
            return None
        await db.execute('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,session_id,created_at,updated_at,mode,target_status,project,decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (disposition_id, policy.owner, policy.scope, event['key'], json.loads(item['result'])['reason'], 'writing', session_id, stamp, stamp, 'followup', item['target_status'], policy.project,
                          encode({'event_version': item['event_version'], 'policy_revision': item['policy_revision'], 'queued_round': item['queued_round'], 'decision': json.loads(item['decision'])})))
    item['disposition_id'] = disposition_id
    try:
        await d.operation(adapter, event, {'action': 'update_status', 'uuids': [event['id']],
            'deal_status': item['target_status'], 'deal_comment': json.loads(item['result'])['reason']}, '执行上轮已确定的 XDR 状态标记')
        # Reading the whole event after write verifies activity as well as status.
        checked = await read_event(adapter, event)
        observed = checked.get('dealStatus')
        same = event_version(normalize(event['device'], checked)) == item['event_version']
        verified = same and type(observed) is int and observed == item['target_status']
        await _audit_finish(item, 'verified' if verified else 'pending', observed)
        return await _finish(item, 'verified' if verified else 'pending', observed=observed,
                             reason='已标记并按原事件编号回查确认。' if verified else '标记后回查未确认原版本目标状态，后续只回查。')
    except BaseException as exc:
        await _audit_finish(item, 'pending', error='状态写入结果待回查，禁止重复写入')
        result = await _finish(item, 'pending', reason='写入或回查中断，保留执行意图，下一轮只回查。')
        if isinstance(exc, asyncio.CancelledError) or not isinstance(exc, Exception):
            raise
        return result


async def process_due(policy, session_id, round_id, adapter_factory=None, recorder=None, *, timeout_seconds=120, limit=50):
    """Consume prior-round intents; replay after interruption only reads status."""
    adapter = (adapter_factory or FollowupAdapter)(policy, session_id)
    candidates = await rows("SELECT f.* FROM monitor_event_followup f WHERE f.owner=? AND f.scope=? AND f.project=? AND f.state IN ('queued','writing','pending') AND f.queued_round!=? AND NOT EXISTS (SELECT 1 FROM monitor_attempts q,monitor_attempts c WHERE q.id=f.queued_round AND c.id=? AND q.sequence>=c.sequence) ORDER BY f.updated_at LIMIT ?", (policy.owner, policy.scope, policy.project, round_id, round_id, max(1, min(int(limit), 100))))
    summary = {'processed': 0, 'verified': 0, 'external_closed': 0, 'pending': 0, 'invalidated': 0, 'watching': 0, 'errors': []}
    token = d.operation_recorder.set(recorder) if recorder is not None else None
    deadline = asyncio.get_running_loop().time() + max(0, min(float(timeout_seconds), 120))
    try:
        for item in candidates:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                async with d._locks.setdefault((policy.owner, item['event_key']), asyncio.Lock()):
                    # Waiting consumers refresh the durable claim after the first
                    # worker has completed; no stale row can repeat its write.
                    current = await rows('SELECT * FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=?',
                        (policy.owner, policy.scope, policy.project, item['device'], item['event_key']))
                    if not current or current[0]['state'] not in {'queued', 'writing', 'pending'} or current[0]['queued_round'] == round_id:
                        continue
                    result = await asyncio.wait_for(_process(current[0], policy, session_id, adapter), timeout=min(90, remaining))
                if result:
                    summary['processed'] += 1
                    summary[result['state']] += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                summary['errors'].append('下一轮状态核验暂未完成，已保存原意图；没有把查询失败视为事件结束。')
                summary['pending'] += 1
    finally:
        if token is not None:
            d.operation_recorder.reset(token)
    return summary


async def reconcile_external(policy, event, raw, round_id, *, query_by_id=False):
    """Archive a source terminal state only after unique, unfiltered ID lookup.

    The caller supplies the exact record from ``automatic.read_event``; a
    monitoring list that happens not to contain this incident is insufficient.
    Unknown or missing states and newer incident activity remain open.
    """
    identity = _identity(policy, event)
    if (not query_by_id or not isinstance(raw, dict) or raw.get('uuId') != event['id']
            or type(raw.get('dealStatus')) is not int or raw['dealStatus'] not in {40, 60}):
        return None
    if event_version(normalize(event['device'], raw)) != event_version(event):
        return None
    found = await rows('SELECT * FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=?', identity)
    if not found:
        await observe(policy, event, round_id, query_complete=False)
        found = await rows('SELECT * FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND device=? AND event_key=?', identity)
    item = found[0]
    if item['policy_revision'] != policy_revision(policy) or item['event_version'] != event_version(event):
        return None
    audit = await _verified_local_disposition(policy, event, raw['dealStatus'])
    item = await _bind_verified_audit(item, audit)
    own_target = bool(audit) or item['state'] in _UNCERTAIN and item['target_status'] == raw['dealStatus']
    if item['state'] == 'verified':
        return projection(item)
    await _audit_finish(item, 'verified' if own_target else 'mismatch', raw['dealStatus'])
    return await _finish(item, 'verified' if own_target else 'external_closed', observed=raw['dealStatus'],
        reason='按原事件编号回查确认此前标记结果。' if own_target else '按原事件编号回查确认已由外部处置结束。')
