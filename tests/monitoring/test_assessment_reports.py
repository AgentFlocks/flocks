"""Daily history remains evidence-bound when a later incident version arrives."""
import json

import pytest

from flocks.monitoring.assessment import assess_event
from flocks.monitoring.investigation import fingerprint
from flocks.monitoring.reports import snapshot
from flocks.monitoring.store import rows, write, encode
from tests.monitoring.test_automatic import auto


async def test_historical_confirmed_risk_is_not_replaced_by_new_activity_uncertainty(auto):
    case = (await rows('SELECT * FROM monitor_investigations'))[0]
    event = dict(auto.event)
    event['assessment'] = assess_event(event, case)
    assert event['assessment']['status'] == 'risk_open'
    await write('UPDATE monitor_observations SET data=?', (encode(event),))
    newer = {**event, 'endTime': 200}
    await write("UPDATE monitor_investigations SET event=?,result='{}',state='pending'", (encode(newer),))
    report = await snapshot('owner', auto.policy.scope, auto.day)
    assert report['metrics']['risk'] == 1 and report['metrics']['openRisk'] == 1
    assert report['events'][0]['assessment']['status'] == 'risk_open'
    assert report['events'][0]['assessment']['event_revision'] == fingerprint(event)


async def test_source_completed_risk_counts_as_handled_with_source_label(auto):
    closed = {**auto.event, 'dealStatus': 40,
        'basics': {**auto.event['basics'], 'deal_status': 40}}
    await write('UPDATE monitor_observations SET data=?', (encode(closed),))
    report = await snapshot('owner', auto.policy.scope, auto.day)
    assert report['metrics']['closed'] == 1 and report['metrics']['openRisk'] == 0
    assert report['events'][0]['closureSource'] == 'xdr'


async def test_missing_event_keeps_old_risk_but_is_not_counted_as_verified_remediation(auto):
    from flocks.monitoring import followup
    from flocks.monitoring.reports import render, render_summary
    await followup.observe(auto.policy, auto.event, 'previous')
    # An old readback field must not turn a later source-list absence into a
    # fresh, verified remediation fact.
    await write("UPDATE monitor_event_followup SET result=json_set(result,'$.observed_status',40)")
    await followup.end_missing(auto.policy, auto.event, 'complete-empty-snapshot')
    report = await snapshot('owner', auto.policy.scope, auto.day)
    event = report['events'][0]
    assert event['assessment']['verdict'] == 'risk'
    assert event['assessment']['status'] == 'ended'
    assert event['closure'] == 'ended' and event['closureSource'] == 'monitoring'
    assert report['metrics']['monitoringEnded'] == 1
    assert report['metrics']['closed'] == report['metrics']['riskClosed'] == report['metrics']['openRisk'] == 0
    assert 'XDR 未再返回' in render(report)
    assert '不计为已确认处置或风险消除' in render_summary(report)
    assert not await rows('SELECT * FROM monitor_dispositions')


@pytest.mark.parametrize(('source_status', 'closure'), [(40, 'closed'), (60, 'ignored')])
async def test_explicit_source_terminal_overrides_old_local_absence(auto, source_status, closure):
    from flocks.monitoring import followup
    await followup.end_missing(auto.policy, auto.event, 'complete-empty-snapshot')
    latest = {**auto.event, 'dealStatus': source_status,
              'basics': {**auto.event['basics'], 'deal_status': source_status}}
    await write('UPDATE monitor_observations SET data=?', (encode(latest),))
    report = await snapshot('owner', auto.policy.scope, auto.day)
    assert report['events'][0]['closure'] == closure
    assert report['events'][0]['closureSource'] == 'xdr'
    assert report['metrics']['monitoringEnded'] == 0


async def test_stopped_uncertain_event_is_not_counted_as_waiting_for_judgment(auto):
    from flocks.monitoring import followup
    await write("UPDATE monitor_investigations SET result='{}',state='pending'")
    await followup.end_missing(auto.policy, auto.event, 'complete-empty-snapshot')
    report = await snapshot('owner', auto.policy.scope, auto.day)
    assert report['events'][0]['assessment']['verdict'] == 'unknown'
    assert report['metrics']['unknown'] == report['metrics']['insufficient'] == 0
    assert report['metrics']['monitoringEnded'] == 1
