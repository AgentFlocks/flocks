"""The UI and reports distinguish scheduled decisions from verified source facts."""
from datetime import datetime, timezone

from flocks.monitoring import event_list, followup
from flocks.monitoring.assessment import assess_event, apply_followup
from flocks.monitoring.store import encode, write
from flocks.monitoring.reports import snapshot as report_snapshot
from tests.monitoring.test_followup import case, decision, consume


async def ledger(case):
    day = datetime.now(timezone.utc).date().isoformat()
    await write('INSERT INTO monitor_installations(owner,scope,project,policy) VALUES(?,?,?,?)',
                (case.policy.owner, case.policy.scope, case.policy.project, encode(case.policy.model_dump())))
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('round', 'exec', case.policy.owner, case.policy.project, case.policy.scope, day, 's', 'm', datetime.now(timezone.utc).isoformat(), 'completed'))
    await write('INSERT INTO monitor_observations VALUES(?,?,?)', ('round', case.event['key'], encode(case.event)))
    return day


async def member(case):
    # The list period follows first monitor detection, never source activity.
    day = datetime.now(timezone.utc).date().isoformat()
    data = await event_list.list_events(case.policy.owner, case.policy.scope, start_date=day, end_date=day)
    return data['items'][0]['members'][0]


async def test_benign_queued_is_not_presented_as_already_ignored(case):
    await decision(case, 'benign')
    day = await ledger(case)
    await followup.observe(case.policy, case.event, 'round')
    pending = (await member(case))['assessment']
    assert pending['label'] == '无风险 · 待下一轮忽略'
    assert pending['lifecycle'] == 'queued' and not pending['ended']
    assert not case.calls
    assert (await report_snapshot(case.policy.owner, case.policy.scope, day))['metrics']['ignored'] == 0
    await consume(case, 'later')
    finished = (await member(case))['assessment']
    assert finished['label'] == '无风险 · 已忽略' and finished['ended']
    assert not finished['notify_eligible']
    report = await report_snapshot(case.policy.owner, case.policy.scope, day)
    assert report['metrics']['ignored'] == 1 and report['events'][0]['closure'] == 'ignored'


async def test_uncertain_six_rounds_remain_distinct_from_benign(case):
    await decision(case)
    day = await ledger(case)
    for turn in range(1, 7):
        await followup.observe(case.policy, case.event, f'round-{turn}')
        assessment = (await member(case))['assessment']
        assert assessment['observation_rounds'] == turn
        assert not assessment['risk_excluded'] and not assessment['notify_eligible']
    assert assessment['label'] == '证据不足 · 观察期已满 · 待下一轮忽略'
    await consume(case, 'seventh')
    assessment = (await member(case))['assessment']
    assert assessment['label'] == '证据不足 · 已忽略'
    assert assessment['verdict'] == 'unknown' and not assessment['risk_excluded']


async def test_external_end_never_becomes_a_benign_verdict(case):
    await decision(case)
    day = await ledger(case)
    raw = {**case.raw, 'dealStatus': 40}
    await followup.reconcile_external(case.policy, case.event, raw, 'round', query_by_id=True)
    assessment = (await member(case))['assessment']
    assert assessment['label'] == '源端关闭'
    assert assessment['external'] and assessment['ended']
    assert assessment['verdict'] == 'unknown' and not assessment['risk_excluded']
    detail = await event_list.detail(case.policy.owner, case.policy.scope, case.event['key'])
    assert detail['event']['assessment'] == assessment


async def test_stale_followup_cannot_override_new_event_state(case):
    await decision(case, 'benign')
    intent = await followup.observe(case.policy, case.event, 'old-round')
    changed = {**case.event, 'endTime': 200}
    current = assess_event(changed)
    assert apply_followup(current, changed, intent) == current
    assert not current['notify_eligible']
