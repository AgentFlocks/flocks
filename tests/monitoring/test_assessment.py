"""Business classifications require facts, not source labels or mail states."""
import json

import pytest

from flocks.monitoring.assessment import assess_event
from flocks.monitoring.investigation import fingerprint


def event(**changes):
    return {'key': 'a:incident:one', 'id': 'one', 'device': 'a', 'host': '192.0.2.3',
            'name': 'SQL注入', 'startTime': 1791763200, 'endTime': 1791763200, 'dealStatus': 0, **changes}


def case(current, **changes):
    proof = {'id': 'evidence-1', 'event': current['id'], 'device': current['device'],
             'event_revision': fingerprint(current), 'success': True,
             'facts': {'data': {'requestBody': "id=1 UNION SELECT passwd FROM users"}}}
    result = {'verdict': 'risk', 'reason': '请求包含实际SQL注入行为，需要核查并修复入口。',
              'confidence': 'high', 'risk_confirmed': True, 'blocking_gaps': [], 'evidence_ids': ['evidence-1']}
    return {'event': current, 'state': 'ready', 'evidence': [proof], 'result': result, **changes}


def test_confirmed_behavior_can_notify_and_local_tag_never_writes_status():
    current = event()
    assessed = assess_event(current, case(current))
    assert assessed['status'] == 'risk_open' and assessed['notify_eligible']
    assert current['dealStatus'] == 0
    assert assessed['policy_version'] == 2 and assessed['event_revision'] == fingerprint(current)


@pytest.mark.parametrize('facts', [
    {}, {'data': {'item': []}}, {'attackState': 3, 'attackResult': 3, 'gptResult': 120},
    {'domain': 'suspicious.example', 'threatLevel': 3}, {'ip': '192.0.2.3', 'threatLevel': 3},
])
def test_source_labels_empty_results_and_entity_names_do_not_substantiate_risk(facts):
    current = event()
    saved = case(current)
    saved['evidence'][0]['facts'] = facts
    result = assess_event(current, saved)
    assert result['status'] == 'insufficient' and not result['notify_eligible']
    assert not result['risk_excluded']
    assert '尚未排除风险' in result['reason']


@pytest.mark.parametrize('field,value', [('state', 'system_wait'), ('state', 'pending')])
def test_incomplete_investigations_never_notify(field, value):
    current = event()
    assert not assess_event(current, case(current, **{field: value}))['notify_eligible']


@pytest.mark.parametrize('change', [
    {'success': False}, {'event': 'another'}, {'event_revision': 'older'}, {'id': 'other-proof'},
])
def test_unsuccessful_or_misattributed_evidence_is_rejected(change):
    current = event()
    saved = case(current)
    saved['evidence'][0].update(change)
    assert assess_event(current, saved)['status'] == 'insufficient'


def test_changed_event_invalidates_previous_conclusion_and_attached_result_is_version_bound():
    old = event()
    saved = case(old)
    assert assess_event(event(endTime=1791763800), saved)['status'] == 'insufficient'
    attached = {'state': 'ready', 'event_revision': fingerprint(old), **saved['result']}
    assert assess_event(old, attached, saved['evidence'])['notify_eligible']
    assert not assess_event(event(host='192.0.2.4'), attached, saved['evidence'])['notify_eligible']


def test_legacy_high_source_score_cannot_bypass_new_confidence_contract():
    current = event()
    saved = case(current)
    saved['result'].pop('confidence')
    saved['result'].pop('risk_confirmed')
    assert assess_event(current, saved)['status'] == 'insufficient'


def test_material_gap_blocks_but_supplementary_gap_or_partial_independent_fact_does_not():
    current = event()
    saved = case(current)
    saved['evidence'][0]['partial'] = True
    saved['result']['gaps'] = ['暂未查清攻击者来源，但已有完整恶意请求。']
    assert assess_event(current, saved)['notify_eligible']
    saved['result']['blocking_gaps'] = ['现有请求无法确认是否为实际恶意行为。']
    assert not assess_event(current, saved)['notify_eligible']


def test_benign_requires_high_confidence_cited_business_facts():
    current = event()
    saved = case(current)
    saved['result'].update(verdict='benign', risk_confirmed=False,
                           reason='请求来自已核实的授权巡检，行为与巡检记录一致。')
    assert assess_event(current, saved)['status'] == 'benign'
    assert assess_event(current, saved)['risk_excluded']
    saved['result']['confidence'] = 'low'
    assert assess_event(current, saved)['status'] == 'insufficient'


def test_actual_completed_status_and_source_ignore_are_distinct():
    for value, status, notify in [(40, 'risk_closed', False), (60, 'risk_open', True), (70, 'risk_open', True)]:
        current = event(dealStatus=value)
        result = assess_event(current, case(current))
        assert result['status'] == status and result['notify_eligible'] == notify


def test_json_database_row_and_presence_projection_use_same_gate():
    current = event()
    saved = case(current)
    compact = [{k: v for k, v in saved['evidence'][0].items() if k != 'facts'} | {'fact_fields': ['requestBody']}]
    row = {**saved, 'event': json.dumps(current), 'result': json.dumps(saved['result']),
           'evidence': None, 'assessment_proofs': json.dumps(compact)}
    assert assess_event(current, row) == assess_event(current, saved)


def test_only_disposition_change_preserves_canonical_evidence_but_activity_change_does_not():
    old = event(basics={'deal_status': 0, 'host': '192.0.2.3'})
    saved = case(old)
    completed = {**old, 'dealStatus': 40, 'basics': {**old['basics'], 'deal_status': 40}}
    assert assess_event(completed, saved)['status'] == 'risk_closed'
    assert not assess_event(completed, saved)['notify_eligible']
    # Keeping the original proof revision is mandatory even when a status-only
    # refresh was accepted; no rebinding to a made-up current proof is allowed.
    saved['evidence'][0]['event_revision'] = fingerprint(completed)
    assert assess_event(completed, saved)['status'] == 'insufficient'
    saved = case(old)
    assert assess_event({**completed, 'endTime': old['endTime']+1}, saved)['status'] == 'insufficient'


@pytest.mark.parametrize('change', [ {'reason': ''}, {'blocking_gaps': {}},
                                    {'verdict': 'benign', 'risk_confirmed': True} ])
def test_inconsistent_or_malformed_result_never_authorizes_notice(change):
    current = event()
    saved = case(current)
    saved['result'].update(change)
    assert assess_event(current, saved)['status'] == 'insufficient'


@pytest.mark.parametrize('result', ['null', '[]', 'true', '1', '"wrong"'])
def test_valid_json_wrong_result_shape_stays_insufficient(result):
    current = event()
    saved = case(current, result=result)
    assert assess_event(current, saved)['status'] == 'insufficient'


def test_duplicate_or_malformed_evidence_references_are_not_silently_accepted():
    current = event()
    saved = case(current)
    saved['evidence'].append(saved['evidence'][0])
    assert assess_event(current, saved)['status'] == 'insufficient'
    saved = case(current)
    saved['result']['evidence_ids'].append(None)
    assert assess_event(current, saved)['status'] == 'insufficient'



def dga_case():
    current = event(name='主机存在感染蠕虫病毒（DGA）', host='10.32.1.173')
    saved = case(current)
    saved['result']['reason'] = 'attack_result=3与attackState=3均为失陷，gptResult=120，故判定风险。'
    saved['evidence'][0]['facts'] = {'data': {'item': [{
        'srcIp': '10.32.1.173', 'srcPort': [61579, 58506, 61859, 53699, 53013, 50849],
        'destIp': '223.5.5.5', 'destPort': 53, 'protocol': 'DNS',
        'name': '访问大量随机域名', 'attackState': 3, 'gptResult': 120,
        'firstTime': 1791860154, 'lastTime': 1791860390,
    }]}}
    return current, saved


def test_reported_dga_email_cannot_send_from_public_dns_connection_and_source_tags():
    current, saved = dga_case()
    # Reproduce an overconfident model against the exact substantive facts in
    # the user's mail: many ports and a DGA label still provide no DNS names.
    result = assess_event(current, saved)
    assert result['status'] == 'insufficient' and not result['notify_eligible']
    assert not result['risk_excluded'] and not result['evidence_supported']
    assert '普通 DNS' in result['reason'] and '实际查询域名' in result['reason']
    compact = [{k: v for k, v in saved['evidence'][0].items() if k != 'facts'}
               | {'fact_fields': ['srcIp', 'srcPort', 'destIp', 'destPort', 'protocol']}]
    assert assess_event(current, {**saved, 'evidence': compact}) == result


def test_domain_presence_is_investigation_material_not_an_automatic_malicious_verdict():
    current, saved = dga_case()
    saved['evidence'][0]['facts']['data']['item'][0]['domain'] = 'www.example.com'
    saved['result'].update(verdict='unknown', risk_confirmed=False,
                           reason='目前仅取得一个普通域名，未取得异常查询行为证据。')
    result = assess_event(current, saved)
    assert result['status'] == 'insufficient' and not result['notify_eligible']
    assert not result['risk_excluded']
    # Even with domains available, a low-confidence risk proposal cannot send.
    saved['result'].update(verdict='risk', risk_confirmed=True, confidence='low')
    assert not assess_event(current, saved)['notify_eligible']


@pytest.mark.parametrize('extra', [
    {'domain': ['g6n7k4z9.invalid', 'p8q3y7r2.invalid']},
    {'requestBody': 'DNS query: g6n7k4z9.invalid; repeated NXDOMAIN replies'},
    {'filePath': '/tmp/worm', 'fileMd5': 'f' * 32, 'virusName': 'Confirmed.Worm'},
    {'processName': 'worm-agent', 'commandLine': '/tmp/worm-agent --generate-domains'},
])
def test_dga_with_actual_investigation_material_still_uses_confirmed_risk_gate(extra):
    current, saved = dga_case()
    saved['evidence'][0]['facts']['data']['item'][0].update(extra)
    saved['result']['reason'] = '已核实的异常查询或主机侧恶意行为与本事件相符，需要处置。'
    assert assess_event(current, saved)['notify_eligible']
    saved['result']['blocking_gaps'] = ['无法核实观察到的行为是否为恶意。']
    assert not assess_event(current, saved)['notify_eligible']


def test_dga_guard_does_not_restrict_other_confirmed_network_incident_categories():
    current, saved = dga_case()
    changed = {**current, 'name': '已证实的恶意网络访问'}
    saved['event'] = changed
    saved['evidence'][0]['event_revision'] = fingerprint(changed)
    assert assess_event(changed, saved)['notify_eligible']
