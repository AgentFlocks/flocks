import pytest
from flocks.monitoring.summaries import analysis_summary

pytestmark = pytest.mark.no_database

def sample(**changes):
    r=dict(risk=0,benign=0,unknown=1,analyzed=0,errors=[],query_events=1,resumed_events=0,query_resumed_overlap=0,investigation_incomplete=1)
    r.update(changes)
    e=dict(key='one',id='one',risk='unknown',host='192.0.2.1',related=[],investigation={'state':'pending'})
    return r,e

def test_single_pending_event_is_natural_and_zero_counters_stay_in_evidence():
    r,e=sample()
    s=analysis_summary(r,[e],{'host':['one']})
    assert s.text.startswith('本轮查询返回 1 条事件，无历史待办。未形成多事件关联组，不代表没有关联主机。调查尚未完成，风险待判定。\n\n')
    assert '0 条' not in s.text and '已有调查结论 0 条' in s.details
    assert '当前事件版本' in s.text and '高置信' in s.text and 'XDR 回查' in s.text
    assert len(s.text)<200

def test_finished_inconclusive_is_not_presented_as_unfinished():
    r,e=sample(investigation_incomplete=0,analyzed=1)
    e['investigation']['state']='ready'
    s=analysis_summary(r,[e],{})
    assert '调查已完成，但证据不足' in s.text and '调查尚未完成' not in s.text

def test_partial_query_and_overlap_are_kept_without_claiming_no_alerts():
    r,e=sample(query_complete=False,resumed_events=2,query_resumed_overlap=1,errors=['query failure'])
    s=analysis_summary(r,[e],{})
    assert '查询未完整成功' in s.text and '历史待办 2 条' in s.text and '重合 1 条，已去重' in s.text
    assert '系统故障不作无风险或证据不足结论' in s.text
    s=analysis_summary(r,[],{})
    assert '不能据此判断没有告警' in s.text

def test_legacy_unknown_does_not_invent_investigation_progress():
    r,e=sample()
    r.pop('investigation_incomplete');e.pop('investigation')
    s=analysis_summary(r,[e],{})
    assert '风险待判定' in s.text and '调查尚未完成' not in s.text and '调查已完成' not in s.text
