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
    policy = SimpleNamespace(development_sample=True)

    def __init__(self, severities=None):
        self.calls = []
        self.events = [{'uuId': f'event-{i}', 'incidentSeverity': level, 'dealStatus': 40 if i % 2 else 60}
                       for i, level in enumerate(severities or [2, 3, 4] * 11)]

    async def call(self, device, params, message):
        self.calls.append(copy.deepcopy(params))
        severities = params['api_params']['severities']
        candidates = [e for e in self.events if not severities or e['incidentSeverity'] in severities]
        start = (params['page_num'] - 1) * params['page_size']
        return {'data': {'item': candidates[start:start + params['page_size']], 'total': len(candidates)}}


@pytest.mark.parametrize('index', [0, 4, 5, 32])
async def test_random_index_across_pages_only_admits_one(monkeypatch, index):
    adapter = Adapter()
    monkeypatch.setattr(sampling.secrets, 'randbelow', lambda total: index)
    result = await query_device(adapter, 'device', 0, 100, Recorder())
    assert len(result) == 1 and result[0]['id'] == f'event-{index}'
    assert result[0]['development_sample'] is True
    assert len(adapter.calls) == (1 if index < 5 else 2)
    assert all(c['page_size'] == 5 and c['deal_statuses'] == [] and c['api_params']['severities'] == [2, 3, 4]
               and c['white_status'] == ['未加白', '部分加白'] for c in adapter.calls)


async def test_prefer_medium_and_higher_then_fallback(monkeypatch):
    monkeypatch.setattr(sampling.secrets, 'randbelow', lambda total: total - 1)
    adapter = Adapter([1, 2, 1, 4, 3])
    result = await query_device(adapter, 'device', 0, 100, Recorder())
    assert result[0]['id'] == 'event-4'
    fallback = Adapter([1] * 7)
    result = await query_device(fallback, 'device', 0, 100, Recorder())
    assert result[0]['id'] == 'event-6'
    assert len(fallback.calls) == 3
    assert [c['api_params']['severities'] for c in fallback.calls] == [[2, 3, 4], [], []]


@pytest.mark.parametrize('case', ['missing_total', 'changed_total', 'over_limit', 'missing_row', 'bad_severity', 'duplicate'])
async def test_invalid_or_changing_sample_is_not_committed(monkeypatch, case):
    adapter = Adapter()
    original = adapter.call
    monkeypatch.setattr(sampling.secrets, 'randbelow', lambda total: 7)
    async def bad(*args):
        value = await original(*args)
        data = value['data']
        if case == 'missing_total': data.pop('total')
        if case == 'changed_total' and len(adapter.calls) == 2: data['total'] += 1
        if case == 'over_limit': data['item'].append({'uuId': 'extra', 'incidentSeverity': 4})
        if case == 'missing_row': data['item'].pop()
        if case == 'bad_severity': data['item'][0]['incidentSeverity'] = 1
        if case == 'duplicate': data['item'][1] = data['item'][0]
        return value
    adapter.call = bad
    with pytest.raises(ContractError):
        await query_device(adapter, 'device', 0, 100, Recorder())


def test_older_saved_policy_defaults_to_development_sample():
    policy = MonitoringPolicy.model_validate({'owner': 'owner', 'project': 'project', 'directory': '/tmp/monitor'})
    assert policy.development_sample is True
    assert not sampling.enabled(policy.model_copy(update={'development_sample': False}))
