"""Evidence pipeline: original incident → bound alerts → packets → narrative."""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import investigation as i, capabilities as c
from flocks.monitoring.adapter import normalize
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import connection, rows
from tests.monitoring.test_automatic import auto, notice


class Recorder:
    session_id = 'details-session'
    def __init__(self):
        self.summaries = []
    async def call(self, name, params, operation):
        outcome = await operation('details-message')
        self.summaries.append((name, outcome[2] if len(outcome) > 2 else None))
        return outcome[0]


@pytest.fixture
async def detail_case(tmp_path, monkeypatch):
    policy = MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['xdr'])
    event = normalize('xdr', {'uuId': 'incident-web', 'name': 'SQL注入攻击', 'hostIp': '192.0.2.2',
        'startTime': 100, 'endTime': 200, 'incidentSeverity': 3, 'dealStatus': 0,
        'alertIds': ['alert-1', 'alert-2', 'alert-3'], 'dataSource': 'STA'})
    event['investigationWindow'] = {'start': 100, 'end': 201}
    catalog = [c.Capability('cap-1', 'xdr', policy.tool, 'xdr', '样例 XDR', 'unknown'),
               c.Capability('cap-2', 'xdr', 'sangfor_xdr_alerts', 'xdr_alerts', '样例 XDR 告警', 'unknown')]
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=(catalog, [])))
    monkeypatch.setattr(i.Agent, 'list', AsyncMock(return_value=[]))
    async def query(policy, session, message, capability, target, entity, *, alert_id=''):
        assert target['device'] == 'xdr'
        if entity == 'proof':
            return {'data': [{'uuId': event['id'], 'alertTimeLine': [
                {'alertId': 'alert-3', 'name': '稍后请求', 'lastTime': 180},
                {'alertId': 'alert-1', 'name': 'SQL尝试', 'lastTime': 120}]}]}, {'action': 'get_proof'}
        if entity == 'alerts':
            return {'data': {'item': [{'uuId': x, 'name': 'SQL请求', 'lastTime': 120,
                'srcIp': '198.51.100.3', 'destIp': '192.0.2.2'} for x in target['alertIds']], 'total': 3}}, {'action': 'list'}
        assert entity == 'alert_proof' and alert_id in target['alertIds']
        return {'data': {'uuId': alert_id, 'proof': {
            'requestHead': 'POST /search HTTP/1.1\nHost: example.test\nAuthorization: Bearer PRIVATE_SECRET',
            'requestBody': '{"orderBy":"case when @@version like 0x382e then publish_date end", "password":"PRIVATE_SECRET"}',
            'responseHead': 'HTTP/1.1 200 OK\nContent-Type: application/json',
            'responseBody': '{"code":200,"msg":"查询成功"}'}}}, {'action': 'get_proof', 'uuid': alert_id}
    call = AsyncMock(side_effect=query)
    monkeypatch.setattr(c, 'query', call)
    async with connection() as db:
        await i.enqueue(db, policy, event, i.now().isoformat())
    return SimpleNamespace(policy=policy, event=event, recorder=Recorder(), call=call)


async def finish(agent, data):
    return i.Choice(action='finish', verdict='risk', evidence_ids=[p['id'] for p in data['evidence'] if p['success']],
        reason='请求orderBy字段包含@@version及case when语句，支持SQL注入尝试；200响应尚不能证明利用成功，需核查服务端影响')


async def test_core_details_reach_model_chat_and_saved_story(detail_case, monkeypatch):
    model = AsyncMock(side_effect=finish)
    monkeypatch.setattr(i, 'choose', model)
    case = detail_case
    await i.investigate(case.policy, case.event, case.recorder)
    assert [call.args[5] for call in case.call.await_args_list] == ['proof', 'alerts', 'alert_proof', 'alert_proof']
    model_data = model.await_args.args[1]
    assert model_data['event']['basics']['data_source'] == 'STA'
    assert '@@version' in json.dumps(model_data, ensure_ascii=False)
    saved = (await rows('SELECT evidence,result FROM monitor_investigations'))[0]
    assert 'PRIVATE_SECRET' not in saved['evidence'] + saved['result']
    evidence = json.loads(saved['evidence'])
    assert evidence[2]['query_key'][-1] == 'alert-1' and evidence[3]['query_key'][-1] == 'alert-2'
    story = case.event['story']
    assert '200响应尚不能证明利用成功' in story['conclusion']
    assert any('2/3' in gap for gap in story['gaps'])
    assert [row['time'] for row in story['timeline']] == sorted(row['time'] for row in story['timeline'])
    assert len(story['packets']) == 8
    assert case.recorder.summaries[-1][1].packets == story['packets']
    assert 'Authorization: Bearer PRIVATE_SECRET' not in json.dumps(story, ensure_ascii=False)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.call.await_count == 4 and model.await_count == 1


async def test_low_budget_can_conclude_unknown_without_priming_starvation(detail_case, monkeypatch):
    case = detail_case
    case.policy.investigation_calls = 1
    async def limited(agent, data):
        assert data['remaining_calls'] == 0
        return i.Choice(action='finish', verdict='unknown', evidence_ids=['evidence-1'], reason='举证已取得，告警详情待补查，不能确认影响')
    monkeypatch.setattr(i, 'choose', limited)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.call.await_count == 1
    assert case.event['investigation']['state'] == 'ready'
    assert any('0/3' in gap for gap in case.event['story']['gaps'])


async def test_budget_deferral_reuses_core_evidence_next_round(detail_case, monkeypatch):
    case = detail_case
    case.policy.investigation_calls = 2
    async def more(agent, data):
        if not any(p['kind'] == 'alert_proof' for p in data['evidence']):
            return i.Choice(action='query', capability='cap-2', entity='alert_proof', alert_id='alert-1', reason='补请求与响应')
        return await finish(agent, data)
    monkeypatch.setattr(i, 'choose', more)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'deferred' and case.call.await_count == 2
    future = i.now() + timedelta(minutes=11)
    monkeypatch.setattr(i, 'now', lambda: future)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert case.call.await_count == 4
    assert [call.args[5] for call in case.call.await_args_list] == ['proof', 'alerts', 'alert_proof', 'alert_proof']


def test_other_incident_proof_cannot_promote_unbound_alert_ids():
    event = {'id': 'original', 'device': 'xdr', 'alertIds': ['bound']}
    proof = {'event': 'original', 'device': 'xdr', 'kind': 'proof', 'success': True,
        'facts': {'data': [{'uuId': 'other', 'alertTimeLine': [{'alertId': 'unbound'}]}]}}
    assert i.trusted_alert_ids(event, [proof]) == ['bound']
    proof['facts']['data'][0]['uuId'] = 'original'
    assert i.trusted_alert_ids(event, [proof]) == ['bound', 'unbound']


def test_model_context_retains_packet_excerpts_and_attempt_id():
    proofs = [{'id': f'evidence-{n}', 'kind': 'alert_proof', 'success': True, 'partial': False,
               'attempt_id': 'attempt', 'query_key': ['xdr', 'alerts', 'alert_proof', str(n)],
               'facts': {'data': {'uuId': f'alert-{n}', 'proof': {
                   'requestBody': 'REQUEST_MARKER ' + 'x' * 7900,
                   'responseBody': 'RESPONSE_MARKER ' + 'y' * 7900}}}} for n in range(24)]
    bounded = i.model_evidence({'name': 'Web攻击'}, proofs)
    assert len(json.dumps(bounded, ensure_ascii=False)) < 60_000
    assert all(p['attempt_id'] == 'attempt' and p['context_excerpt'] and p['partial'] for p in bounded)
    assert 'REQUEST_MARKER' in json.dumps(bounded) and 'RESPONSE_MARKER' in json.dumps(bounded)


def test_failed_query_retry_limit_survives_context_projection():
    cap = c.Capability('cap', 'xdr', 'tool', 'xdr', 'fixture', 'unknown')
    proofs = [{'id': f'evidence-{n}', 'kind': 'host', 'success': False, 'attempt_id': 'attempt',
               'partial': True, 'query_key': ['xdr', 'tool', 'host'], 'facts': {}} for n in (1, 2)]
    data = {'event': {'device': 'xdr'}, 'capabilities': [cap.json()], 'evidence': i.model_evidence({}, proofs), 'attempt_id': 'attempt'}
    with pytest.raises(i.InvestigationError, match='相同查询'):
        i.validate_choice(i.Choice(action='query', capability='cap', entity='host', reason='retry'), data)


async def test_mail_uses_same_story_and_preserves_one_notice(auto):
    from flocks.monitoring.event_details import build_story
    from flocks.monitoring.store import write, encode
    proof = {'id': 'evidence-1', 'kind': 'alert_proof', 'success': True, 'partial': False,
             'facts': {'data': {'uuId': 'alert-example', 'proof': {
                 'requestHead': 'POST /search HTTP/1.1',
                 'requestBody': '{"orderBy":"@@version", "token":"PRIVATE_SECRET"}',
                 'responseHead': 'HTTP/1.1 200 OK', 'responseBody': '{}'}}}}
    auto.event['story'] = build_story(auto.event, [proof], {'reason': '发现注入尝试，尚不能证明利用成功', 'evidence_ids': ['evidence-1']})
    await write('UPDATE monitor_observations SET data=?', (encode(auto.event),))
    sent = await notice(auto)
    assert sent['state'] == 'sent'
    body = sent['body']
    assert '事件基本信息' in body and '调查结论' in body and '关键证据' in body
    assert '@@version' in body and 'evidence-1' in body and 'PRIVATE_SECRET' not in body
    assert '其他联动查询' not in body and '文件：0' not in body
    await notice(auto)
    assert auto.send.await_count == 1
