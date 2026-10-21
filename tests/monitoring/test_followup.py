"""Next-round intent durability and observation safety; no external devices."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flocks.monitoring import followup as f
from flocks.monitoring.adapter import normalize, ContractError
from flocks.monitoring.investigation import fingerprint, EVIDENCE_SCHEMA
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import encode, rows, write

REAL_AUTHORIZED = f.authorized


class Recorder:
    async def call(self, title, params, operation):
        return (await operation('message'))[0]


@pytest.fixture
async def case(monkeypatch):
    policy = MonitoringPolicy(owner='owner', project='project', directory='/tmp/monitor', devices=['one', 'two'])
    raw = {'uuId': 'event', 'name': 'fixture', 'incidentSeverity': 2,
           'hostIp': '10.1.1.1', 'dealStatus': 0, 'whiteStatus': '未加白', 'startTime': 10, 'endTime': 100}
    event = normalize('one', raw)
    event['investigationWindow'] = {'start': 0, 'end': 100}
    state = SimpleNamespace(policy=policy, event=event, raw=raw, calls=[], lose_write=False, reads=0, fail_read=False)
    async def allow(_):
        return None
    monkeypatch.setattr(f, 'authorized', allow)
    class Adapter:
        def __init__(self, policy, session_id):
            self.policy, self.session_id = policy, session_id
        async def call(self, device, params, message_id):
            state.calls.append((device, copy.deepcopy(params)))
            if params['action'] == 'update_status':
                state.raw['dealStatus'] = params['deal_status']
                if state.lose_write:
                    raise TimeoutError('raw secret must not be persisted')
                return {'code': 'Success'}
            if state.fail_read:
                raise ContractError('fixture read failed')
            state.reads += 1
            return {'data': {'item': [copy.deepcopy(state.raw)], 'total': 1}}
    state.adapter = Adapter
    return state


async def decision(case, verdict='unknown', *, confidence='high', failed=False, partial=False, stale=False, suspected=False):
    event = case.event
    stamp = datetime.now(timezone.utc) - (timedelta(hours=1) if stale else timedelta())
    proof = {'id': 'evidence-1', 'schema': EVIDENCE_SCHEMA, 'event': event['id'],
        'event_revision': fingerprint(event), 'device': event['device'], 'retrieved_at': stamp.isoformat(),
        'window': event['investigationWindow'], 'success': not failed, 'partial': partial,
        'facts': {'srcIp': event['host'], 'dstIp': '1.1.1.1', 'dstPort': 443}}
    result = {'verdict': verdict, 'confidence': confidence, 'risk_confirmed': verdict == 'risk',
        'suspected_risk': suspected, 'blocking_gaps': [], 'evidence_ids': ['evidence-1'],
        'reason': '已核对具体通信记录。', 'evidence_schema': EVIDENCE_SCHEMA}
    event['investigation'] = {'state': 'ready', **result, 'event_revision': fingerprint(event), 'attempted_this_round': True}
    await write('INSERT INTO monitor_investigations(owner,project,event_key,event,evidence,result,state,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(owner,project,event_key) DO UPDATE SET event=excluded.event,evidence=excluded.evidence,result=excluded.result,state=excluded.state',
                (case.policy.owner, case.policy.project, event['key'], encode(event), encode([proof]), encode(result), 'ready', stamp.isoformat()))
    return event


def mutations(case):
    return [params for _, params in case.calls if params['action'] == 'update_status']


async def consume(case, round_id):
    return await f.process_due(case.policy, 'session', round_id, adapter_factory=case.adapter, recorder=Recorder())


async def test_benign_only_writes_on_a_different_round_and_audits_original_version(case):
    await decision(case, 'benign')
    item = await f.observe(case.policy, case.event, 'first')
    assert item['state'] == 'queued' and item['target_status'] == 60
    assert (await consume(case, 'first'))['processed'] == 0
    assert not mutations(case)
    result = await consume(case, 'next')
    assert result['verified'] == 1 and len(mutations(case)) == 1
    assert mutations(case)[0]['uuids'] == ['event']
    audit = (await rows('SELECT * FROM monitor_dispositions'))[0]
    assert audit['mode'] == 'followup' and audit['status'] == 'verified'
    assert audit['target_status'] == 60 and audit['project'] == case.policy.project
    await consume(case, 'later')
    assert len(mutations(case)) == 1


async def test_retained_confirmed_risk_invalidates_old_uncertainty_ignore_intent(case):
    from flocks.monitoring.conclusions import capture_case
    await decision(case, 'unknown')
    for index in range(6):
        await f.observe(case.policy, case.event, f'round-{index}')
    assert (await f.snapshot(case.policy))[case.event['key']]['state'] == 'queued'
    await decision(case, 'risk')
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    snapshot = capture_case(saved)
    await write('UPDATE monitor_investigations SET state=?,result=?,evidence=?,confirmed_risk=?',
                ('deferred', encode({'verdict': 'unknown', 'reason': '补查模型暂不可用'}), '[]', encode(snapshot)))
    case.event['investigation'] = {'state': 'deferred'}
    await consume(case, 'next')
    assert not mutations(case)
    item = await f.observe(case.policy, case.event, 'after-recheck')
    assert item['state'] == 'risk_open' and item['target_status'] is None


async def test_watch_default_six_distinct_complete_rounds_then_following_round(case):
    assert case.policy.observation_rounds == 6
    await decision(case)
    for i in range(6):
        item = await f.observe(case.policy, case.event, f'round-{i}')
        repeat = await f.observe(case.policy, case.event, f'round-{i}')
        assert item['watch_count'] == repeat['watch_count'] == i + 1
        assert item['state'] == ('queued' if i == 5 else 'watching')
    assert (await consume(case, 'round-5'))['processed'] == 0
    assert (await consume(case, 'round-6'))['verified'] == 1
    assert mutations(case)[0]['deal_status'] == 60
    item = (await f.snapshot(case.policy))[case.event['key']]
    assert item['closed'] and item['result']['decision_kind'] == 'uncertain'


@pytest.mark.parametrize('cause', ['query', 'deferred', 'failed_proof', 'partial_proof', 'stale_proof', 'model', 'version'])
async def test_incomplete_observation_does_not_count(case, cause):
    await decision(case, failed=cause == 'failed_proof', partial=cause == 'partial_proof', stale=cause == 'stale_proof')
    if cause == 'deferred':
        case.event['investigation']['state'] = 'deferred'
    if cause == 'model':
        await write("UPDATE monitor_investigations SET state='system_wait',error_kind='model_format'")
    if cause == 'version':
        case.event['endTime'] += 10
    item = await f.observe(case.policy, case.event, 'round', query_complete=cause != 'query')
    assert item['watch_count'] == 0 and item['state'] == 'watching'


async def test_valid_cached_conclusion_counts_after_successful_source_recheck(case):
    await decision(case)
    case.event['investigation']['attempted_this_round'] = False
    item = await f.observe(case.policy, case.event, 'source-rechecked')
    assert item['watch_count'] == 1


async def correlation_decision(case, *, bindings, products=('tdp', 'sig'), change=None, plan=None):
    """Save program-owned correlation coverage independently of a risk card."""
    from flocks.monitoring.correlation import response_metadata
    await decision(case)
    saved = (await rows('SELECT evidence,result FROM monitor_investigations'))[0]
    evidence, result = json.loads(saved['evidence']), json.loads(saved['result'])
    result['correlation_scope'] = {'device': case.event['device'], 'bindings': bindings}
    if plan is not None:
        result['correlation_plan'] = plan
    for product in products:
        raw = {'data': {'items': [{'subnet': '10.1.1.0/24'}] if product == 'sig' else [
            {'net': {'src_ip': case.event['host'], 'dest_ip': '192.0.2.1'}}], 'total': 1}}
        proof = {**evidence[0], 'id': 'evidence-' + product, 'kind': 'related', 'device': product,
                 'product': product, 'tool': 'tdp_log_search' if product == 'tdp' else 'onesig_strategy_api_query',
                 'facts': raw, 'result_meta': response_metadata(raw, product)}
        if change is not None:
            change(proof)
        evidence.append(proof)
    await write('UPDATE monitor_investigations SET evidence=?,result=?', (encode(evidence), encode(result)))


async def test_explicit_bound_network_and_sig_asset_queries_allow_complete_observation(case):
    await correlation_decision(case, bindings=['tdp', 'sig'])
    item = await f.observe(case.policy, case.event, 'checked')
    assert item['watch_count'] == 1 and item['state'] == 'watching'


@pytest.mark.parametrize('bindings', [None, []])
async def test_legacy_or_explicitly_disabled_correlations_keep_observation_behavior(case, bindings):
    await correlation_decision(case, bindings=bindings, products=())
    assert (await f.observe(case.policy, case.event, 'checked'))['watch_count'] == 1


@pytest.mark.parametrize('status', ['not_queried', 'unavailable'])
async def test_unfinished_bound_plan_cannot_accumulate_observation_or_ignore(case, status):
    await correlation_decision(case, bindings=['tdp'], products=(),
                               plan=[{'device': 'tdp', 'status': status}])
    for index in range(6):
        item = await f.observe(case.policy, case.event, f'round-{index}')
        assert item['watch_count'] == 0 and item['state'] == 'watching'
    assert (await consume(case, 'after'))['processed'] == 0
    assert not mutations(case)


@pytest.mark.parametrize('change', ['missing', 'other_event', 'other_revision', 'partial',
    'metadata_partial', 'inconsistent', 'unknown_structure', 'missing_records', 'failed'])
async def test_bound_query_must_be_successful_current_complete_and_structured(case, change):
    def modify(proof):
        if change == 'other_event':
            proof['event'] = 'another-event'
        elif change == 'other_revision':
            proof['event_revision'] = 'another-revision'
        elif change == 'partial':
            proof['partial'] = True
        elif change == 'metadata_partial':
            proof['result_meta']['partial'] = True
        elif change == 'inconsistent':
            proof['result_meta']['inconsistent'] = True
        elif change in {'unknown_structure', 'missing_records'}:
            from flocks.monitoring.correlation import response_metadata
            proof['facts'] = {'opaque': 'success'} if change == 'unknown_structure' else {'total': 5}
            proof['result_meta'] = response_metadata(proof['facts'], 'tdp')
        elif change == 'failed':
            proof['success'] = False
    await correlation_decision(case, bindings=['tdp'], products=() if change == 'missing' else ('tdp',), change=modify)
    item = await f.observe(case.policy, case.event, 'checked')
    assert item['watch_count'] == 0 and item['state'] == 'watching'


async def test_explicit_empty_bound_query_is_complete_without_proving_safety(case):
    def empty(proof):
        from flocks.monitoring.correlation import response_metadata
        proof['facts'] = {'data': {'items': [], 'total': 0}}
        proof['result_meta'] = response_metadata(proof['facts'], 'tdp')
    await correlation_decision(case, bindings=['tdp'], products=('tdp',), change=empty)
    item = await f.observe(case.policy, case.event, 'checked')
    assert item['watch_count'] == 1 and item['state'] == 'watching'


@pytest.mark.parametrize('level,source', [(3, 'incidentSeverity'), (4, 'incidentSeverity'), (-1, 'incidentSeverity'), (None, None), (0, 'riskLevel'), (1, 'riskLevel'), (5, 'riskLevel')])
async def test_high_or_unknown_severity_never_expires(case, level, source):
    case.event['severitySource'] = source
    case.event['incidentSeverity'] = level if source == 'incidentSeverity' else None
    case.event['riskLevel'] = level if source == 'riskLevel' else None
    case.policy.observation_rounds = 1
    await decision(case)
    item = await f.observe(case.policy, case.event, 'round')
    assert item['state'] == 'watching' and item['target_status'] is None


async def test_suspected_risk_cannot_expire_as_low_risk(case):
    case.policy.observation_rounds = 1
    await decision(case, confidence='low', suspected=True)
    assert (await f.observe(case.policy, case.event, 'round'))['state'] == 'watching'
    await decision(case, 'risk', confidence='low')
    assert (await f.observe(case.policy, case.event, 'another'))['state'] == 'watching'


async def test_confirmed_risk_waits_for_mail_feedback(case):
    await decision(case, 'risk')
    assert (await f.observe(case.policy, case.event, 'first'))['state'] == 'risk_open'
    assert (await consume(case, 'next'))['processed'] == 0
    assert not mutations(case)


async def test_observation_can_be_disabled_without_disabling_benign_decision(case):
    case.policy.observation_rounds = 0
    await decision(case)
    assert (await f.observe(case.policy, case.event, 'one'))['state'] == 'watching'
    await decision(case, 'benign')
    assert (await f.observe(case.policy, case.event, 'two'))['state'] == 'queued'


@pytest.mark.parametrize('field,value', [('host', '10.9.9.9'), ('endTime', 110), ('name', 'new activity')])
async def test_changed_event_resets_observation_and_invalidates_old_epoch(case, field, value):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    case.event[field] = value
    item = await f.observe(case.policy, case.event, 'second')
    assert item['watch_count'] == 0 and item['state'] == 'watching'


async def test_config_change_discards_old_queued_intent(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    case.policy.observation_rounds = 12
    assert (await consume(case, 'next'))['invalidated'] == 1
    assert not case.calls


@pytest.mark.parametrize('field,value', [('hostIp', '10.1.1.2'), ('endTime', 101)])
async def test_new_remote_activity_prevents_queued_write(case, field, value):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    case.raw[field] = value
    assert (await consume(case, 'next'))['invalidated'] == 1
    assert not mutations(case)


async def test_change_between_remote_reads_prevents_write(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    original = case.adapter
    class Changed(original):
        async def call(self, device, params, message):
            if case.reads == 1:
                case.raw['endTime'] += 1
            return await super().call(device, params, message)
    case.adapter = Changed
    assert (await consume(case, 'next'))['invalidated'] == 1
    assert not mutations(case)


async def test_lost_write_response_only_rechecks_and_never_repeats(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    case.lose_write = True
    assert (await consume(case, 'next'))['pending'] == 1
    assert len(mutations(case)) == 1
    case.lose_write = False
    assert (await consume(case, 'later'))['verified'] == 1
    assert len(mutations(case)) == 1
    assert 'secret' not in (await rows('SELECT result FROM monitor_event_followup'))[0]['result']


async def test_unknown_write_not_reflected_remains_pending_without_repeat(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    case.lose_write = True
    await consume(case, 'next')
    case.raw['dealStatus'] = 0
    case.lose_write = False
    assert (await consume(case, 'later'))['pending'] == 1
    assert len(mutations(case)) == 1


async def test_concurrent_consumers_claim_only_one_network_write(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    await asyncio.gather(consume(case, 'next'), consume(case, 'next'))
    assert len(mutations(case)) == 1


async def test_query_failure_does_not_close_event_or_consume_queued_intent(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    case.fail_read = True
    result = await consume(case, 'next')
    assert result['errors'] and not mutations(case)
    item = (await f.snapshot(case.policy))[case.event['key']]
    assert item['state'] == 'queued' and not item['closed']


@pytest.mark.parametrize('status', [40, 60])
async def test_external_terminal_status_requires_exact_source_readback(case, status):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    assert await f.reconcile_external(case.policy, case.event, None, 'next', query_by_id=True) is None
    case.raw['dealStatus'] = status
    assert await f.reconcile_external(case.policy, case.event, case.raw, 'next') is None
    result = await f.reconcile_external(case.policy, case.event, case.raw, 'next', query_by_id=True)
    assert result['state'] == 'external_closed' and result['external'] and result['closed']
    assert not mutations(case)


async def test_external_terminal_new_activity_or_wrong_id_does_not_close(case):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    raw = {**case.raw, 'dealStatus': 40, 'endTime': 200}
    assert await f.reconcile_external(case.policy, case.event, raw, 'next', query_by_id=True) is None
    raw = {**case.raw, 'dealStatus': 40, 'uuId': 'different'}
    assert await f.reconcile_external(case.policy, case.event, raw, 'next', query_by_id=True) is None


async def test_queued_target_already_finished_by_other_actor_is_external(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    case.raw['dealStatus'] = 60
    assert (await consume(case, 'next'))['external_closed'] == 1
    assert not mutations(case)


async def test_device_scope_and_owner_project_do_not_share_intents(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    other = case.policy.model_copy(update={'owner': 'another'})
    assert await f.snapshot(other) == {}
    other = case.policy.model_copy(update={'project': 'another'})
    assert await f.snapshot(other) == {}
    wrong = {**case.event, 'device': 'two'}
    with pytest.raises(ContractError):
        await f.observe(case.policy, wrong, 'first')
    case.policy.devices = ['two']
    assert (await consume(case, 'next'))['invalidated'] == 1
    assert not mutations(case)


async def test_snapshot_includes_original_event_only_when_requested(case):
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    assert 'event' not in (await f.snapshot(case.policy))[case.event['key']]
    result = (await f.snapshot(case.policy, include_event=True))[case.event['key']]
    assert result['event']['id'] == 'event' and result['event']['device'] == 'one'


async def test_followup_authorization_requires_active_current_installation(case, monkeypatch):
    # Test the real authorization function even though normal tests isolate it.
    authorize = REAL_AUTHORIZED
    async def owned(_):
        return {'ready': True}, SimpleNamespace(status=SimpleNamespace(value='active')), case.policy
    monkeypatch.setattr('flocks.monitoring.lifecycle._owned_installation', owned)
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *_: SimpleNamespace(enabled=True))
    monkeypatch.setattr('flocks.project.project.Project.registry_state', lambda *_, **__: 'active')
    await authorize(case.policy)
    changed = case.policy.model_copy(update={'observation_rounds': 12})
    with pytest.raises(ContractError):
        await authorize(changed)
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *_: SimpleNamespace(enabled=False))
    with pytest.raises(ContractError):
        await authorize(case.policy)


async def test_benign_stale_failed_or_unread_event_does_not_queue(case):
    await decision(case, 'benign')
    item = await f.observe(case.policy, case.event, 'missing-query', query_complete=False)
    assert item['state'] == 'watching' and item['target_status'] is None
    await decision(case, 'benign', stale=True)
    item = await f.observe(case.policy, case.event, 'expired-evidence')
    assert item['state'] == 'watching' and item['watch_count'] == 0


async def test_followup_adapter_disallows_nonterminal_or_multiple_targets(case, monkeypatch):
    from flocks.monitoring.adapter import XdrAdapter
    calls = []
    async def execute(self, device, params, message_id):
        calls.append(params)
        return {'code': 'Success'}
    monkeypatch.setattr(XdrAdapter, 'call', execute)
    adapter = f.FollowupAdapter(case.policy, 'session')
    for params in ({'action': 'update_status', 'uuids': ['event'], 'deal_status': 10},
                   {'action': 'update_status', 'uuids': ['event', 'other'], 'deal_status': 60}):
        with pytest.raises(PermissionError):
            await adapter.call('one', params, 'message')
    assert not calls
    await adapter.call('one', {'action': 'update_status', 'uuids': ['event'], 'deal_status': 60}, 'message')
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['mail', 'mail_manual'])
async def test_verified_mail_or_manual_status_is_owned_not_external(case, mode):
    await decision(case, 'risk')
    await f.observe(case.policy, case.event, 'first')
    stamp = datetime.now(timezone.utc).isoformat()
    await write('INSERT INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,created_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('notice', case.policy.owner, case.policy.scope, case.policy.project, case.event['key'], 'a@example.com', 'revision', 'mailbox', 'message', 'subject', 'body', encode(case.event), 'sent', stamp, stamp))
    await write('INSERT INTO monitor_dispositions(id,owner,scope,project,event_key,comment,status,observed_status,created_at,updated_at,mode,target_status,decision) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('audit', case.policy.owner, case.policy.scope, case.policy.project, case.event['key'], 'feedback', 'verified', 40, stamp, stamp, mode, 40,
                 encode({'notice': 'notice', 'observed_end_time': 100, 'observed_host': '10.1.1.1'})))
    case.raw['dealStatus'] = 40
    result = await f.reconcile_external(case.policy, case.event, case.raw, 'next', query_by_id=True)
    assert result['state'] == 'verified' and not result['external']
    assert result['disposition_id'] == 'audit'
    assert not mutations(case)


async def test_old_verified_write_for_an_older_activity_cannot_claim_new_external_closure(case):
    await decision(case, 'risk')
    await f.observe(case.policy, case.event, 'first')
    stamp = datetime.now(timezone.utc).isoformat()
    await write('INSERT INTO monitor_dispositions(id,owner,scope,project,event_key,comment,status,observed_status,created_at,updated_at,mode,target_status,decision) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('old-audit', case.policy.owner, case.policy.scope, case.policy.project, case.event['key'], 'old-feedback', 'verified', 40, stamp, stamp, 'followup', 40,
                 encode({'event_version': 'different-activity'})))
    case.raw['dealStatus'] = 40
    result = await f.reconcile_external(case.policy, case.event, case.raw, 'next', query_by_id=True)
    assert result['state'] == 'external_closed' and result['external']


@pytest.mark.parametrize('new_decision', ['risk', 'unknown', 'failure', 'expired'])
async def test_changed_latest_conclusion_blocks_previous_benign_intent(case, new_decision):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    if new_decision == 'risk':
        await decision(case, 'risk')
    elif new_decision == 'unknown':
        await decision(case)
    elif new_decision == 'expired':
        await decision(case, 'benign', stale=True)
    else:
        await write("UPDATE monitor_investigations SET state='system_wait',error_kind='query_transient'")
    assert (await consume(case, 'next'))['watching'] == 1
    assert not mutations(case)


async def test_changed_latest_conclusion_blocks_previous_uncertain_intent(case):
    case.policy.observation_rounds = 1
    await decision(case)
    await f.observe(case.policy, case.event, 'first')
    await decision(case, 'risk', confidence='low')
    assert (await consume(case, 'next'))['watching'] == 1
    assert not mutations(case)


@pytest.mark.parametrize('state', ['queued', 'writing', 'pending', 'mismatch'])
async def test_unresolved_human_or_mail_feedback_has_priority_over_ignore(case, state):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    stamp = datetime.now(timezone.utc).isoformat()
    await write('INSERT INTO monitor_dispositions(id,owner,scope,project,event_key,comment,status,created_at,updated_at,mode,target_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('feedback', case.policy.owner, case.policy.scope, case.policy.project, case.event['key'], 'feedback', state, stamp, stamp, 'mail_manual', 40))
    assert (await consume(case, 'next'))['watching'] == 1
    assert not mutations(case)
    assert (await rows('SELECT status FROM monitor_dispositions'))[0]['status'] == state


async def test_replaying_an_older_known_round_cannot_consume_a_newer_intent(case):
    await decision(case, 'benign')
    for identifier in ('old', 'new', 'future'):
        await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (identifier, identifier, case.policy.owner, case.policy.project, case.policy.scope, '2026-10-13', 'session', 'message', datetime.now(timezone.utc).isoformat(), 'completed'))
    await f.observe(case.policy, case.event, 'new')
    assert (await consume(case, 'old'))['processed'] == 0
    assert not mutations(case)
    assert (await consume(case, 'future'))['verified'] == 1


async def test_zero_time_budget_does_not_touch_device_or_lose_intent(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    result = await f.process_due(case.policy, 'session', 'next', case.adapter, Recorder(), timeout_seconds=0)
    assert result['processed'] == 0 and not case.calls
    assert (await f.snapshot(case.policy))[case.event['key']]['state'] == 'queued'


async def test_production_write_adapter_keeps_exact_grant_under_unattended_policy(case, monkeypatch):
    from contextlib import asynccontextmanager
    from pathlib import Path
    import inspect
    import yaml
    from unittest.mock import AsyncMock
    from flocks.session.interaction_policy import unattended_scope, monitoring_read_scope
    from flocks.tool.registry import ToolRegistry, ToolResult
    from flocks.tool.tool_loader import yaml_to_tool
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    path = Path(__file__).resolve().parents[2] / '.flocks/plugins/tools/device/sangfor_xdr_v2_2/sangfor_xdr_incidents.yaml'
    tool = yaml_to_tool(yaml.safe_load(path.read_text()), path)
    fn = inspect.getclosurevars(tool.handler).nonlocals['fn']
    requests = []
    async def send(method, path, data=None, params=None):
        requests.append((path, data))
        if path.endswith('/dealstatus'):
            case.raw['dealStatus'] = data['dealStatus']
            return ToolResult(success=True, output={'code': 'Success'})
        return ToolResult(success=True, output={'code': 'Success', 'data': {'item': [copy.deepcopy(case.raw)], 'total': 1}})
    monkeypatch.setitem(fn.__globals__, '_run_request', send)
    monkeypatch.setattr(ToolRegistry, '_tools', {tool.info.name: tool})
    monkeypatch.setattr(ToolRegistry, '_initialized', True)
    monkeypatch.setattr(ToolRegistry, '_sync_configured_enabled_states', lambda: None)
    monkeypatch.setattr(ToolRegistry, '_resolve_device_target', AsyncMock(return_value=('one', None)))
    monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', AsyncMock(return_value=True))
    @asynccontextmanager
    async def credentials(device):
        yield True
    monkeypatch.setattr('flocks.tool.credential_context.activate_device_credentials', credentials)
    with unattended_scope(), monitoring_read_scope(case.policy.tool, case.policy.devices):
        result = await f.process_due(case.policy, 'session', 'next', recorder=Recorder())
    assert result['verified'] == 1, result
    writes = [request for request in requests if request[0].endswith('/dealstatus')]
    assert len(writes) == 1 and writes[0][1]['uuIds'] == ['event'] and writes[0][1]['dealStatus'] == 60


async def test_cancelled_write_keeps_readback_only_intent(case):
    await decision(case, 'benign')
    await f.observe(case.policy, case.event, 'first')
    entered = asyncio.Event()
    original = case.adapter
    class Hanging(original):
        async def call(self, device, params, message_id):
            result = await super().call(device, params, message_id)
            if params['action'] == 'update_status':
                entered.set()
                await asyncio.Event().wait()
            return result
    case.adapter = Hanging
    task = asyncio.create_task(consume(case, 'next'))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await f.snapshot(case.policy))[case.event['key']]['state'] == 'pending'
    case.adapter = original
    assert (await consume(case, 'later'))['verified'] == 1
    assert len(mutations(case)) == 1


async def test_nonconsecutive_round_replay_cannot_inflate_observation_count(case):
    await decision(case)
    for round_id, expected in [('one', 1), ('two', 2), ('one', 2), ('three', 3)]:
        item = await f.observe(case.policy, case.event, round_id)
        assert item['watch_count'] == expected
    assert not mutations(case)
