"""Formal query coverage and migration from retired development sampling."""
import copy
from types import SimpleNamespace

import pytest

from flocks.monitoring import sampling
from flocks.monitoring.adapter import ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.runtime import query_device


class Recorder:
    async def call(self, name, params, operation):
        return (await operation('message'))[0]


class Adapter:
    def __init__(self, count=205, *, total=True):
        # Even callers carrying old raw configuration cannot re-enable sampling.
        self.policy = SimpleNamespace(development_sample=True, max_pages=10)
        self.calls = []
        self.total = total
        self.events = [{'uuId': f'event-{i}', 'incidentSeverity': (i % 4) + 1,
                        'dealStatus': 10 if i % 2 else 0} for i in range(count)]

    async def call(self, device, params, message):
        self.calls.append(copy.deepcopy(params))
        start = (params['page_num'] - 1) * params['page_size']
        value = {'item': self.events[start:start + params['page_size']]}
        if self.total:
            value['total'] = len(self.events)
        return {'data': value}


@pytest.mark.parametrize('count', [0, 1, 100, 205])
@pytest.mark.parametrize('has_total', [False, True])
async def test_formal_query_reads_all_pages_without_random_or_severity_filter(count, has_total):
    adapter = Adapter(count, total=has_total)
    result = await query_device(adapter, 'device', 0, 100, Recorder())
    assert [event['id'] for event in result] == [f'event-{i}' for i in range(count)]
    assert not any(event.get('development_sample') for event in result)
    expected_pages = max(1, (count + 99) // 100) if has_total else count // 100 + 1
    assert [call['page_num'] for call in adapter.calls] == list(range(1, expected_pages + 1))
    assert all(call['page_size'] == 100 and call['deal_statuses'] == [0, 10]
               and call['white_status'] == ['未加白', '部分加白']
               and call['time_field'] == 'endTime' and 'api_params' not in call
               for call in adapter.calls)


@pytest.mark.parametrize('case', ['changed_total', 'missing_row', 'duplicate_page', 'budget'])
async def test_incomplete_formal_query_cannot_return_an_admissible_batch(case):
    adapter = Adapter()
    if case == 'budget':
        adapter.policy.max_pages = 1
    original = adapter.call
    async def bad(*args):
        value = await original(*args)
        if len(adapter.calls) == 2:
            if case == 'changed_total': value['data']['total'] += 1
            if case == 'missing_row': value['data']['item'].pop()
            if case == 'duplicate_page': value['data']['item'] = adapter.events[:100]
        return value
    adapter.call = bad
    with pytest.raises(ContractError):
        await query_device(adapter, 'device', 0, 100, Recorder())


@pytest.mark.parametrize('saved', [{}, {'development_sample': True, 'investigation_engine': 'rules'},
                                   {'development_sample': False, 'investigation_engine': 'agent-v1'}])
def test_older_saved_policy_migrates_to_formal_agent_investigation(saved):
    policy = MonitoringPolicy.model_validate({'owner': 'owner', 'project': 'project',
                                             'directory': '/tmp/monitor', **saved})
    assert policy.investigation_engine == 'agent-v1'
    assert policy.development_sample is False
    assert policy.model_dump()['development_sample'] is False
    assert not sampling.enabled(SimpleNamespace(development_sample=True))
