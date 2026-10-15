"""Specific, current operator facts and gaps without loosening risk admission."""
import pytest
from flocks.monitoring.assessment import assess_event
from flocks.monitoring.case_overview import operator_overview
from flocks.monitoring.event_details import build_story
from flocks.monitoring.evidence_presenter import bound_evidence
from flocks.monitoring.investigation import fingerprint


def example():
    event = {'key': 'xdr:incident:one', 'id': 'one', 'device': 'xdr', 'host': '192.0.2.23',
             'name': '通用系统命令注入攻击', 'startTime': 100, 'endTime': 200, 'dealStatus': 0}
    proof = {'id': 'evidence-1', 'kind': 'alerts', 'device': 'xdr', 'event': 'one',
             'event_revision': fingerprint(event), 'success': True, 'partial': True,
             'facts': {'data': {'item': [{'name': '命令注入攻击', 'srcIp': ['192.0.2.73', '192.0.2.77'],
                         'dstIp': '192.0.2.23', 'dstPort': [80]}]}}}
    case = {'event': event, 'state': 'system_wait', 'evidence': [proof],
            'result': {'verdict': 'unknown', 'reason': '相同查询已存在结果', 'gaps': ['调查尚未完成']}}
    return event, case


def render(event, case):
    valid = bound_evidence(event, case['evidence'], canonical=case['event'])
    assessment = assess_event(event, case)
    story = build_story(event, valid, case['result'], canonical=case['event'])
    return operator_overview(event, case, assessment, story, valid), assessment


def test_failed_investigation_shows_actual_access_and_missing_payload_response():
    event, case = example()
    result, assessment = render(event, case)
    assert '命令注入攻击' in result['summary']
    assert any('192.0.2.73' in line and '192.0.2.23:80' in line for line in result['findings'])
    assert any('攻击请求' in line for line in result['missing_evidence'])
    assert any('服务端响应' in line for line in result['missing_evidence'])
    assert assessment['verdict'] == 'unknown' and not assessment['notify_eligible']
    assert '相同查询' not in result['summary']


@pytest.mark.parametrize('invalid', ['failed', 'stale', 'wrong_event'])
def test_invalid_evidence_is_never_presented_as_current_findings(invalid):
    event, case = example()
    if invalid == 'failed':
        case['evidence'][0]['success'] = False
    elif invalid == 'stale':
        event = {**event, 'endTime': 201}
    else:
        case['evidence'][0]['event'] = 'other'
    result, assessment = render(event, case)
    assert not any('192.0.2.73' in line for line in result['findings'])
    assert '行为详情' in result['findings'][0]
    assert not assessment['notify_eligible']


def test_confirmed_risk_has_short_behavior_summary_and_no_machine_bookkeeping():
    event, case = example()
    case['state'] = 'ready'
    case['result'] = {'verdict': 'risk', 'confidence': 'high', 'risk_confirmed': True,
                      'blocking_gaps': [], 'evidence_ids': ['evidence-1'],
                      'operator_summary': '已确认主机收到恶意命令注入请求，需要处置。',
                      'reason': '具体请求内容与事件相符，存在风险'}
    case['evidence'][0]['facts']['data']['item'][0]['requestBody'] = 'cmd=whoami'
    result, assessment = render(event, case)
    assert assessment['notify_eligible']
    assert result['summary'] == case['result']['operator_summary']
    assert result['missing_evidence'] == []
    assert 'evidence-1' not in str(result)


def test_request_summary_does_not_repeat_the_source_prefix():
    event, case = example()
    case['evidence'][0]['source_name'] = '示例XDR'
    case['evidence'][0]['facts']['data']['item'][0]['requestBody'] = 'cmd=whoami'
    result, _ = render(event, case)
    assert result['findings'][0].count('示例XDR') == 1


def test_archived_unknown_event_does_not_announce_active_investigation():
    event, case = example()
    valid = bound_evidence(event, case['evidence'], canonical=case['event'])
    story = build_story(event, valid, case['result'], canonical=case['event'])
    result = operator_overview(event, case, {'verdict': 'unknown', 'ended': True, 'label': '已归档'}, story, valid)
    assert '已归档' in result['summary']
    assert '当前仍需补证' not in result['summary']
    assert any('192.0.2.73' in line for line in result['findings'])


def test_candidate_request_keeps_its_correlation_boundary():
    event, case = example()
    proof = case['evidence'][0]
    proof.update(device='other-device', kind='related', source_name='示例关联源')
    proof['facts']['data']['item'][0].update(requestBody='cmd=whoami', responseBody='denied')
    result, _ = render(event, case)
    assert result['findings'][0].startswith('候选关联：')
    assert result['findings'][0].count('示例关联源') == 1
    assert any('实际攻击请求' in line for line in result['missing_evidence'])
    assert any('服务端响应' in line for line in result['missing_evidence'])


def test_retained_risk_uses_confirmed_facts_and_separates_refresh_failure():
    event, case = example()
    case['result']['reason'] = '模型请求超时，调查尚未形成最终结论'
    case['confirmed_risk'] = {'result': {'operator_explanation': ['上传请求与响应确认文件写入。']}}
    assessment = {'verdict': 'risk', 'confirmed_risk_retained': True, 'risk_scope': 'current',
                  'risk_fact_summary': '已证实上传接口允许异常文件写入，需要处置。',
                  'gaps': ['未确认该文件是否被服务端执行。', '调查尚未形成最终结论'],
                  'recheck_state': 'deferred', 'recheck_reason': '模型退避中，等待恢复'}
    result = operator_overview(event, case, assessment, {}, [])
    assert result['summary'] == assessment['risk_fact_summary']
    assert result['findings'] == ['上传请求与响应确认文件写入。']
    assert result['missing_evidence'] == ['未确认该文件是否被服务端执行。']
    assert result['recheck_state'] == 'deferred'
    assert '超时' not in result['summary']


def test_previous_activity_risk_keeps_its_boundary_and_source_closed_hides_refresh():
    event, case = example()
    assessment = {'verdict': 'risk', 'confirmed_risk_retained': True, 'risk_scope': 'previous_activity',
                  'risk_fact_summary': '已证实异常文件写入。', 'risk_fact_findings': ['响应中返回写入路径。'],
                  'risk_fact_gaps': ['缺少文件后续执行记录。'], 'source_closed': True,
                  'recheck_state': 'system_wait', 'recheck_reason': '模型暂不可用'}
    result = operator_overview(event, case, assessment, {}, [])
    assert result['summary'].startswith('关闭前已确认风险：')
    assert result['findings'][0].startswith('此前活动依据：')
    assert 'recheck_state' not in result
    assert result['missing_evidence'] == ['缺少文件后续执行记录。']


def test_confirmed_risk_reports_specific_supplementary_gaps_without_retracting_verdict():
    event, case = example()
    case['result'] = {'operator_summary': '已确认存在异常上传风险。',
                      'gaps': ['主机侧缺少后续执行记录。', '关联告警举证覆盖2/47条，未取全', '调查尚未完成']}
    result = operator_overview(event, case, {'verdict': 'risk'}, {}, [])
    assert result['summary'] == '已确认存在异常上传风险。'
    assert result['missing_evidence'] == ['主机侧缺少后续执行记录。']
