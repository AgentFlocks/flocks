"""Execution failures must remain distinguishable from inconclusive security assessments."""
import pytest

from flocks.monitoring.summaries import (
    analysis_summary,
    investigation_failure_summary,
    investigation_progress_summary,
    round_summary,
    step_purpose,
)
from flocks.monitoring.rounds import next_step_for
from unittest.mock import AsyncMock


def event(key, risk='unknown', state=None, **fields):
    value = {'key': key, 'id': key, 'device': 'xdr', 'device_name': '测试 XDR',
             'name': '恶意通信事件', 'host': '192.0.2.1', 'risk': risk, 'reason': '等待调查', 'related': []}
    if state:
        value['investigation'] = {'state': state, 'reason': '等待补充依据', **fields}
    return value


def facts(**fields):
    return {'risk': 1, 'benign': 0, 'unknown': 3, 'analyzed': 2, 'errors': [], **fields}


def test_summary_separates_failed_execution_from_finished_inconclusive_assessment():
    events = [event('risk', 'risk', 'ready'), event('uncertain', state='ready'),
              event('failed', state='system_wait'), event('not-started')]
    result = facts(errors=['TDP 关联查询时间参数校验失败'])
    summary = analysis_summary(result, events, {('xdr', '192.0.2.1'): [item['key'] for item in events]})
    assert '确认风险 1 条、无风险 0 条、待判定 3 条' in summary.text
    assert '已完成调查但证据不足 1 条；调查未完成 2 条' in summary.text
    assert '结果汇总完成，不代表事件调查全部成功' in summary.text
    assert '程序故障不能当作证据不足或无风险结论' in summary.text
    assert summary.success is True  # Correlation reporting completed; the failing investigation did not.
    assert '结果汇总完成，不代表每条事件都已调查成功' in step_purpose('关联分析', {})


@pytest.mark.parametrize('stage,reason,expected', [
    ('query_time', 'TDP 关联查询未执行：调查时间窗口缺少结束时间', '关联查询时间参数校验'),
    ('query', '关联查询发生程序错误（TypeError）', '关联取证查询'),
    ('model', '调查模型网关超时（HTTP 504），已有证据保留', '调查模型请求'),
    ('internal', '调查程序发生内部异常', '调查程序执行'),
])
def test_recorded_failure_stage_is_preserved_without_guessing_model_outage(stage, reason, expected):
    outcome = {'failure_stage': stage, 'reason': reason, 'failure_source': '测试 TDP', 'failure_tool': '日志查询',
               'http_status': 504 if stage == 'model' else None, 'error_type': 'TypeError' if stage == 'query' else None}
    text = investigation_failure_summary(outcome)
    assert text.startswith(expected)
    assert '测试 TDP / 日志查询' in text and reason in text
    assert text.count('HTTP 504') == (1 if stage == 'model' else 0)
    if stage != 'model':
        assert '检查模型' not in text and '调查模型请求' not in text


def test_failure_summary_does_not_render_raw_provider_payload_or_invalid_error_fields():
    text = investigation_failure_summary({'failure_stage': 'query', 'reason': '关联查询发生程序错误',
        'error_type': '<script>PRIVATE_EXCEPTION</script>', 'http_status': True,
        'response_body': 'PRIVATE_PROVIDER_BODY', 'request': {'api_key': 'PRIVATE_API_KEY'}})
    assert 'PRIVATE_' not in text and 'HTTP ' not in text


def test_old_records_keep_recorded_reason_without_inventing_a_failure_stage():
    text = investigation_failure_summary({'reason': '原始调查失败说明', 'error_kind': 'system'})
    assert text == '原始调查失败说明'
    assert investigation_progress_summary(facts(), [event('legacy')]) == ''
    assert investigation_progress_summary(facts(investigation_incomplete=2), []) == '已完成调查但证据不足 1 条；调查未完成 2 条。'


def test_round_summary_retains_failure_object_cause_and_recovery_without_mail_attribution():
    failed = event('incident-test', state='system_wait', failure_stage='query_time', failure_source='测试 TDP',
        next_retry_at='2026-10-14T06:20:00Z', reason='TDP 关联查询未执行：调查时间窗口无效')
    result = facts(risk=0, unknown=1, analyzed=0, errors=['测试 XDR · 恶意通信事件：TDP 查询时间参数无效'])
    text = round_summary('failed', result, [failed],
        {'processed': 0, 'verified': 0, 'pending': 0}, {'sent': 0, 'pending': 0}, False, False)
    assert '待判定 1 条' in text
    assert '已完成调查但证据不足 0 条；调查未完成 1 条' in text
    assert '设备「测试 XDR」' in text and 'incident-test' in text
    assert '关联查询时间参数校验（测试 TDP）' in text
    assert '不早于 2026-10-14T06:20:00Z' in text
    assert '邮件跟进未启用' in text and '检查模型' not in text


def test_query_parameter_wait_is_not_retry_exhaustion_or_model_outage():
    failed = event('incident-test', state='system_wait', failure_stage='query_time', error_kind='query_time',
        retry_exhausted=True, reason='调查窗口缺少结束时间')
    result = facts(risk=0, unknown=1, analyzed=0, investigation_backlog={
        'system_wait': 1, 'query_time_blocked': 1, 'retry_exhausted': 1})
    text = round_summary('failed', result, [failed],
        {'processed': 0, 'verified': 0, 'pending': 0}, {'sent': 0, 'pending': 0}, False, False)
    assert '等待正式 XDR 查询' in text
    assert '恢复次数已用完' not in text and '手动重新启动' not in text and '本轮额度用完' not in text


async def test_round_next_step_explains_parameter_repair_without_restart(monkeypatch):
    from flocks.monitoring import investigation, rounds
    from flocks.monitoring.models import MonitoringPolicy
    policy = MonitoringPolicy(owner='owner', project='project', directory='/tmp/synthetic', devices=['xdr'])
    monkeypatch.setattr(rounds, 'rows', AsyncMock(return_value=[{'policy': policy.model_dump_json()}]))
    monkeypatch.setattr(investigation, 'status_summary', AsyncMock(return_value={
        'system_wait': 1, 'query_time_blocked': 1, 'retry_exhausted': 1}))
    text = await next_step_for({'owner': 'owner', 'scope': policy.scope, 'project': 'project'}, True)
    assert '正式 XDR 查询提供合法时间窗口后重新调查' in text
    assert '暂停并重新启动' not in text
