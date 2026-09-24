"""Verify the user-confirmed business formula, without inventing XDR wire values."""
import pytest

from flocks.monitoring.adapter import ContractError
from flocks.monitoring.pagination import IncidentPages
from flocks.monitoring.selection import (
    Disposition, IncidentState, FILTER_EXPRESSION,
    matches_monitoring_scope, monitoring_selection,
)
from flocks.monitoring.summaries import page_summary


@pytest.mark.parametrize('disposition,whitelisted,expected', [
    (Disposition.PENDING, False, True),
    (Disposition.IN_PROGRESS, False, True),
    (Disposition.OTHER, False, False),
    (Disposition.PENDING, True, False),
    (Disposition.IN_PROGRESS, True, False),
    (Disposition.OTHER, True, False),
])
def test_confirmed_business_formula(disposition, whitelisted, expected):
    assert matches_monitoring_scope(IncidentState(disposition, whitelisted)) is expected


@pytest.mark.parametrize('disposition', [None, '', 'unknown', '待处置', 0, 1, 2, 3, False, {}, []])
def test_unmapped_disposition_is_not_pending_or_other(disposition):
    with pytest.raises(ContractError, match='处置状态缺失或未识别'):
        matches_monitoring_scope(IncidentState(disposition, False))


@pytest.mark.parametrize('disposition', list(Disposition))
@pytest.mark.parametrize('whitelisted', [None, '', '未知', '已加白', 'false', 0, 1, 2, {}, []])
def test_unmapped_whitelist_is_not_false(disposition, whitelisted):
    with pytest.raises(ContractError, match='加白状态缺失或未识别'):
        matches_monitoring_scope(IncidentState(disposition, whitelisted))


@pytest.mark.parametrize('raw', [None, {}, {'dealStatus': 1, 'whiteStatus': 0},
                                  {'加白状态': '未加白', '处置状态': '待处置'}])
def test_raw_api_or_ui_fields_are_not_an_implicit_mapping(raw):
    with pytest.raises(ContractError, match='处置状态缺失或未识别'):
        matches_monitoring_scope(raw)


def test_verified_domain_states_feed_counts_and_exact_formula_summary():
    states = {
        'pending': IncidentState(Disposition.PENDING, False),
        'working': IncidentState(Disposition.IN_PROGRESS, False),
        'white': IncidentState(Disposition.PENDING, True),
        'other': IncidentState(Disposition.OTHER, False),
    }
    # Synthetic ID-to-domain-state reader. This is not an XDR field decoder.
    rule = monitoring_selection(lambda raw: states[raw['uuId']])
    items = [{'uuId': key, 'name': key} for key in states]
    batch = IncidentPages('fixture', rule)
    selected, complete = batch.add(items, 4, 100)
    assert complete and list(batch.events) == ['pending', 'working']
    assert batch.counts['source_unique'] == 4 and batch.counts['matched_unique'] == 2
    assert batch.counts['excluded_unique'] == 2
    summary = page_summary(1, selected, 2, complete, selection=rule.description,
                           received=4, counts=batch.counts)
    assert FILTER_EXPRESSION in summary.text
    assert '原始返回 4 条，符合条件 2 条，排除 2 条' in summary.text
    assert 'white' not in summary.details and 'other' not in summary.details
    assert 'pending' in summary.details and 'working' in summary.details
