"""Confirmed incident facts survive supplementary investigation failures."""
import json
from types import SimpleNamespace

import pytest

from flocks.monitoring.assessment import assess_event
from flocks.monitoring.conclusions import capture_case, restore_confirmed_risks
from flocks.monitoring.investigation import fingerprint
from flocks.monitoring.store import connection, encode, rows


def incident(**changes):
    return {'key': 'xdr:incident:one', 'id': 'one', 'device': 'xdr', 'host': '192.0.2.3',
            'name': '异常文件上传', 'endTime': 100, 'dealStatus': 0, **changes}


def confirmed(event, **changes):
    return {'event_key': event['key'], 'event': event, 'state': 'ready', 'revision': 1,
            'updated_at': '2026-10-15T00:00:00+00:00',
            'result': {'verdict': 'risk', 'confidence': 'high', 'risk_confirmed': True,
                       'reason': '上传接口接受了异常文件写入，需要处置。',
                       'operator_summary': '上传接口存在异常文件写入风险。',
                       'operator_explanation': ['上传请求及响应确认路径穿越写入。'],
                       'gaps': ['缺少后续执行记录，尚未确定影响范围。'],
                       'blocking_gaps': [], 'evidence_ids': ['evidence-1']},
            'evidence': [{'id': 'evidence-1', 'event': event['id'], 'device': event['device'],
                          'event_revision': fingerprint(event), 'success': True,
                          'facts': {'requestBody': 'filename=../../test.jsp', 'responseBody': 'saved'}}],
            **changes}


def waiting(event, snapshot):
    return {'event': event, 'state': 'deferred', 'confirmed_risk': snapshot, 'evidence': [],
            'result': {'verdict': 'unknown', 'reason': '模型处于恢复等待期', 'gaps': ['调查尚未完成']}}


def test_ttl_or_model_failure_cannot_revoke_confirmed_risk_or_grant_notification():
    event = incident()
    snapshot = capture_case(confirmed(event))
    assert 'facts' not in snapshot['evidence'][0]  # No packet duplication.
    result = assess_event(event, waiting(event, snapshot))
    assert result['verdict'] == 'risk' and result['confirmed_risk_retained']
    assert result['risk_scope'] == 'current' and result['previously_confirmed_risk']
    assert result['reason'] == snapshot['result']['reason']
    assert result['risk_fact_summary'] == '上传接口存在异常文件写入风险。'
    assert result['risk_fact_findings'] == ['上传请求及响应确认路径穿越写入。']
    assert result['risk_fact_gaps'] == ['缺少后续执行记录，尚未确定影响范围。']
    assert '调查尚未形成最终结论' not in ''.join(result['gaps'])
    assert result['recheck_reason'] == '模型处于恢复等待期'
    assert not result['notify_eligible'] and not result['risk_excluded']


def test_snapshot_never_recursively_copies_runtime_results_or_packet_data():
    event = incident(investigation={'confirmed_risk': {'raw': 'do not duplicate'}}, story={'packets': ['large']})
    snapshot = capture_case(confirmed(event))
    assert 'investigation' not in snapshot['event'] and 'story' not in snapshot['event']
    assert assess_event(event, waiting(event, snapshot))['confirmed_risk_retained']


def test_new_activity_keeps_previous_risk_without_binding_old_proof_to_new_version():
    old = incident()
    snapshot = capture_case(confirmed(old))
    event = incident(endTime=200)
    result = assess_event(event, waiting(event, snapshot))
    assert result['verdict'] == 'risk' and result['risk_scope'] == 'previous_activity'
    assert not result['is_current'] and not result['notify_eligible']
    assert snapshot['evidence'][0]['event_revision'] == fingerprint(old)
    assert snapshot['evidence'][0]['event_revision'] != fingerprint(event)


@pytest.mark.parametrize('changes', [{'device': 'other'}, {'id': 'other'}, {'key': 'other-key'}])
def test_other_event_or_device_cannot_borrow_confirmed_risk(changes):
    old = incident()
    event = incident(**changes)
    result = assess_event(event, waiting(event, capture_case(confirmed(old))))
    assert result['verdict'] == 'unknown' and not result.get('previously_confirmed_risk')


def test_source_tags_and_mail_presence_never_create_a_confirmed_risk_fact():
    event = incident()
    case = confirmed(event)
    case['evidence'][0]['facts'] = {'attackResult': 3, 'gptResult': 120}
    assert capture_case(case) == {}
    case['notice'] = {'state': 'sent'}
    assert capture_case(case) == {}


def test_independently_verified_benign_supersedes_risk_and_prevents_later_revival():
    event = incident()
    risk_case = confirmed(event)
    snapshot = capture_case(risk_case)
    benign = confirmed(event, confirmed_risk=snapshot)
    benign['result'].update(verdict='benign', risk_confirmed=False,
                            reason='已核实该请求来自授权上传验证。')
    assert assess_event(event, benign)['verdict'] == 'benign'
    replaced = capture_case(benign)
    assert replaced['superseded_by']['reason'] == benign['result']['reason']
    result = assess_event(event, waiting(event, replaced))
    assert result['verdict'] == 'unknown' and result['previously_confirmed_risk']
    assert not result.get('confirmed_risk_retained') and not result['notify_eligible']


def test_successful_unknown_reassessment_cannot_silently_retract_verified_risk():
    event = incident()
    snapshot = capture_case(confirmed(event))
    case = confirmed(event, confirmed_risk=snapshot)
    case['result'].update(verdict='unknown', risk_confirmed=False, reason='无法核实后续执行情况。')
    result = assess_event(event, case)
    assert result['verdict'] == 'risk' and result['confirmed_risk_retained']
    assert not result['notify_eligible']


@pytest.mark.parametrize('state,target,label', [('watching', None, '有风险 · 处置中'),
                                               ('queued', 60, '有风险 · 忽略待核验')])
def test_old_followup_does_not_relabel_retained_risk_as_insufficient(state, target, label):
    from flocks.monitoring.assessment import apply_followup
    from flocks.monitoring.followup import event_version
    event = incident()
    assessed = assess_event(event, waiting(event, capture_case(confirmed(event))))
    result = apply_followup(assessed, event, {'state': state, 'target_status': target,
                                            'event_version': event_version(event)})
    assert result['verdict'] == 'risk' and result['label'] == label
    assert not result['notify_eligible'] and not result['risk_excluded']


async def store_case(db, case, *, version=False, owner='owner', project='project'):
    event = case['event']
    if version:
        await db.execute('INSERT INTO monitor_investigation_versions '
                         '(owner,project,event_key,revision,event,evidence,result,state,attempts,archived_at) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (owner, project, event['key'], case['revision'], encode(event), encode(case['evidence']),
                          encode(case['result']), case['state'], 1, case['updated_at']))
    else:
        await db.execute('INSERT INTO monitor_investigations '
                         '(owner,project,event_key,event,evidence,result,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                         (owner, project, event['key'], encode(event), encode(case['evidence']),
                          encode(case['result']), case['state'], case.get('updated_at', '2026-10-15T01:00:00Z')))


async def test_backfill_restores_only_evidence_validated_scoped_historical_risk_once():
    event = incident()
    async with connection() as db:
        await store_case(db, confirmed(event), version=True)
        await store_case(db, waiting(event, None))
        await store_case(db, waiting(event, None), owner='other-owner')
    recovered = await restore_confirmed_risks('owner', 'project', [event['key']])
    assert recovered[event['key']]['result']['verdict'] == 'risk'
    saved = (await rows('SELECT * FROM monitor_investigations WHERE owner=?', ('owner',)))[0]
    assert assess_event(event, saved)['confirmed_risk_retained']
    other = (await rows('SELECT * FROM monitor_investigations WHERE owner=?', ('other-owner',)))[0]
    assert other['confirmed_risk'] is None
    # A checked row reuses the durable projection, including after restart.
    async with connection() as db:
        await db.execute('DELETE FROM monitor_investigation_versions')
    assert await restore_confirmed_risks('owner', 'project', [event['key']]) == recovered
    assert json.loads(saved['confirmed_risk'])['evidence'][0]['fact_fields'] == ['requestBody', 'responseBody']


async def test_backfill_newer_verified_benign_prevents_older_risk_revival():
    event = incident()
    benign = confirmed(event, revision=2)
    benign['result'].update(verdict='benign', risk_confirmed=False, reason='已核实为授权检查。')
    async with connection() as db:
        await store_case(db, confirmed(event), version=True)
        await store_case(db, benign, version=True)
        await store_case(db, waiting(event, None))
    recovered = await restore_confirmed_risks('owner', 'project', [event['key']])
    value = assess_event(event, waiting(event, recovered[event['key']]))
    assert value['verdict'] == 'unknown' and value['previously_confirmed_risk']
    assert recovered[event['key']]['superseded_by']['reason'] == '已核实为授权检查。'


async def test_empty_legacy_backfill_marks_checked_and_never_infers_from_notifications():
    event = incident()
    async with connection() as db:
        await store_case(db, waiting(event, None))
    assert await restore_confirmed_risks('owner', 'project', [event['key']]) == {event['key']: {}}
    saved = (await rows('SELECT confirmed_risk FROM monitor_investigations'))[0]
    assert saved['confirmed_risk'] == '{}'


async def test_many_failed_checkpoints_cannot_hide_older_confirmed_risk_from_backfill():
    event = incident()
    async with connection() as db:
        await store_case(db, confirmed(event), version=True)
        for revision in range(2, 70):
            await store_case(db, {**waiting(event, None), 'revision': revision,
                                 'updated_at': '2026-10-15T01:00:00Z'}, version=True)
        await store_case(db, waiting(event, None))
    recovered = await restore_confirmed_risks('owner', 'project', [event['key']])
    assert assess_event(event, waiting(event, recovered[event['key']]))['confirmed_risk_retained']


async def test_new_observation_restores_unchecked_legacy_risk_before_replacing_checkpoint():
    from flocks.monitoring.investigation import enqueue
    event = incident()
    async with connection() as db:
        await store_case(db, confirmed(event), version=True)
        await store_case(db, waiting(event, None))
        await enqueue(db, SimpleNamespace(owner='owner', project='project'),
                      incident(endTime=200), '2026-10-15T02:00:00Z')
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    result = assess_event(incident(endTime=200), saved)
    assert result['verdict'] == 'risk' and result['risk_scope'] == 'previous_activity'
    assert not result['notify_eligible']
