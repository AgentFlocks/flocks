"""A fresh trusted query can repair bad history without rebinding old evidence."""
import copy
import json
from datetime import timedelta

import pytest

from flocks.monitoring import investigation as i
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import connection, encode, rows, write


def event(window):
    return {'key': 'xdr:incident:original', 'id': 'original', 'device': 'xdr',
            'name': 'Synthetic', 'host': '192.0.2.1', 'startTime': 100, 'endTime': 199,
            'dealStatus': 0, 'investigationWindow': copy.deepcopy(window)}


def proof(item, window=None, **updates):
    return {'id': 'evidence-1', 'schema': i.EVIDENCE_SCHEMA,
            'event': item['id'], 'event_revision': i.fingerprint(item),
            'window': copy.deepcopy(item['investigationWindow'] if window is None else window),
            'retrieved_at': i.now().isoformat(), 'success': True, 'partial': False,
            'facts': {'data': {'hostIp': '192.0.2.1'}}, **updates}


@pytest.fixture
def policy(tmp_path):
    return MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['xdr'])


async def enqueue(policy, item):
    async with connection() as db:
        await i.enqueue(db, policy, item, i.now().isoformat())


@pytest.mark.parametrize('bad_window', [None, {}, {'start': 100, 'end': None},
                                        {'start': 100, 'end': 'not-a-second'},
                                        {'start': 200, 'end': 100},
                                        {'start': 100, 'end': 86501}])
async def test_same_activity_fresh_valid_query_repairs_bad_canonical_window(policy, bad_window):
    old, current = event(bad_window), event({'start': 100, 'end': 200})
    current['query_batch_complete'] = True
    assert i.fingerprint(old) == i.fingerprint(current), 'the incident activity itself has not changed'
    await enqueue(policy, old)
    old_case = (await rows('SELECT * FROM monitor_investigations'))[0]
    previous = proof(old)
    await write('UPDATE monitor_investigations SET evidence=?,result=?,state=?,error_kind=?,failure_count=?,next_retry_at=?,retry_exhausted=1',
                (encode([previous]), encode({'reason': 'old time parameter failure', 'verdict': 'unknown'}),
                 'system_wait', 'query_time', 3, (i.now()+timedelta(hours=1)).isoformat()))

    await enqueue(policy, current)

    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert json.loads(saved['event'])['investigationWindow'] == {'start': 100, 'end': 200}
    assert saved['revision'] == old_case['revision']+1
    assert json.loads(saved['evidence']) == [] and json.loads(saved['result']) == {}
    assert saved['state'] == 'pending' and saved['failure_count'] == 0
    assert saved['next_retry_at'] is None and not saved['retry_exhausted']
    archived = (await rows('SELECT * FROM monitor_investigation_versions'))[0]
    assert json.loads(archived['event'])['investigationWindow'] == bad_window
    assert json.loads(archived['evidence']) == [previous]
    assert [item['key'] for item in await i.pending(policy)] == [current['key']]


async def test_same_activity_valid_historical_window_does_not_slide_to_new_query_window(policy):
    old = event({'start': 100, 'end': 200})
    await enqueue(policy, old)
    original = (await rows('SELECT * FROM monitor_investigations'))[0]
    await enqueue(policy, event({'start': 200, 'end': 300}))
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert json.loads(saved['event'])['investigationWindow'] == old['investigationWindow']
    assert saved['revision'] == original['revision']
    assert not await rows('SELECT * FROM monitor_investigation_versions')


async def test_another_bad_window_cannot_replace_bad_history_or_clear_failure(policy):
    old = event(None)
    await enqueue(policy, old)
    await write("UPDATE monitor_investigations SET state='system_wait',error_kind='query_time',failure_count=3,retry_exhausted=1")
    original = (await rows('SELECT * FROM monitor_investigations'))[0]
    await enqueue(policy, event({'start': 100, 'end': None}))
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert saved['event'] == original['event'] and saved['revision'] == original['revision']
    assert saved['state'] == 'system_wait' and saved['retry_exhausted']
    assert not await rows('SELECT * FROM monitor_investigation_versions')


@pytest.mark.parametrize('old_kind', ['query_time', 'system'])
@pytest.mark.parametrize('activity', [{'endTime': 200}, {'alertIds': ['new-alert']}, {'host': '192.0.2.2'}])
async def test_new_activity_with_valid_window_releases_fixed_parameter_failure(policy, old_kind, activity):
    old = event({'start': 100, 'end': None})
    await enqueue(policy, old)
    await write('UPDATE monitor_investigations SET state=?,error_kind=?,failure_count=3,retry_exhausted=1',
                ('system_wait', old_kind))
    current = {**event({'start': 100, 'end': 201}), **activity, 'query_batch_complete': True}
    assert i.fingerprint(old) != i.fingerprint(current)

    await enqueue(policy, current)

    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert json.loads(saved['event'])['investigationWindow'] == {'start': 100, 'end': 201}
    assert saved['state'] == 'pending' and saved['failure_count'] == 0
    assert saved['next_retry_at'] is None and not saved['retry_exhausted'] and saved['error_kind'] is None
    assert json.loads(saved['evidence']) == [] and json.loads(saved['result']) == {}
    assert len(await rows('SELECT * FROM monitor_investigation_versions')) == 1
    assert [item['key'] for item in await i.pending(policy)] == [current['key']]


async def test_new_activity_window_repair_does_not_bypass_real_model_dependency_cooldown(policy):
    old = event({'start': 100, 'end': None})
    await enqueue(policy, old)
    retry_at = (i.now()+timedelta(minutes=20)).isoformat()
    await write('UPDATE monitor_investigations SET state=?,error_kind=?,failure_count=1,next_retry_at=?,retry_exhausted=0',
                ('system_wait', 'model_transient', retry_at))
    await enqueue(policy, {**event({'start': 100, 'end': 201}), 'endTime': 200})
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert saved['state'] == 'system_wait' and saved['error_kind'] == 'model_transient'
    assert saved['next_retry_at'] == retry_at and saved['failure_count'] == 1
    assert not await i.pending(policy)


@pytest.mark.parametrize('bad_window', [None, {}, {'start': 100, 'end': None}, {'start': 200, 'end': 100}])
def test_invalid_canonical_and_proof_window_cannot_be_fresh_even_if_equal(bad_window):
    item = event(bad_window)
    assert i.fresh_evidence([proof(item)], item) == []


def test_fresh_evidence_requires_same_seconds_and_original_event_fingerprint():
    item = event({'start': 100, 'end': 200})
    evidence = [proof(item, id='exact'),
                proof(item, {'start': '100', 'end': '200'}, id='same-seconds'),
                proof(item, {'start': 101, 'end': 201}, id='different-window'),
                proof(item, id='different-activity', event_revision='another-fingerprint'),
                proof(item, {'start': 100, 'end': 'invalid'}, id='invalid'),
                proof(item, id='expired', retrieved_at=(i.now()-timedelta(minutes=31)).isoformat())]
    assert [value['id'] for value in i.fresh_evidence(evidence, item)] == ['exact', 'same-seconds']
