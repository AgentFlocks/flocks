"""Daily history remains evidence-bound when a later incident version arrives."""
import json

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
