"""Selection plumbing only: synthetic flags are NOT an XDR field contract.

Production runtime does not select statuses until the appliance's whitelist
field/enum is verified. These tests isolate completeness/counting from that
missing external contract, without connecting to a device or shared storage.
"""
import json
from types import SimpleNamespace

import pytest

from flocks.monitoring import runtime
from flocks.monitoring.adapter import ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.pagination import IncidentPages, SelectionRule
from flocks.monitoring.reports import snapshot
from flocks.monitoring.store import rows, write
from flocks.project.project import Project
from flocks.session.message import Message
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionTriggerType


def synthetic_match(raw):
    if type(raw.get('synthetic_match')) is not bool:
        raise ContractError('隔离测试的筛选字段缺失或未知')
    return raw['synthetic_match']


RULE = SelectionRule('隔离测试条件（不是 XDR 状态映射）', synthetic_match)


def item(index, matches=False):
    return {'uuId': f'event-{index}', 'synthetic_match': matches,
            'name': f"{'Included' if matches else 'Excluded'}-{index}",
            'riskLevel': 1, 'hostIp': '192.0.2.1'}


class QuietRecorder:
    def __init__(self):
        self.outputs = []

    async def call(self, name, params, operation):
        outcome = await operation('synthetic-message')
        self.outputs.append(outcome)
        return outcome[0]


def pages_adapter(pages, total, max_pages=10):
    calls = []

    async def call(device, params, message):
        calls.append(params.copy())
        data = {'list': pages[params['page_num'] - 1]}
        if total is not None:
            data['total'] = total
        return {'data': data}
    return SimpleNamespace(policy=SimpleNamespace(max_pages=max_pages), call=call), calls


@pytest.mark.parametrize('total', [201, None])
async def test_excluded_middle_page_does_not_end_source_paging(total):
    pages = [[item(i, i in {0, 200}) for i in range(start, end)]
             for start, end in [(0, 100), (100, 200), (200, 201)]]
    adapter, calls = pages_adapter(pages, total)
    recorder = QuietRecorder()
    events = await runtime.query_device(adapter, 'fixture', 0, 100, recorder, selection=RULE)
    assert [c['page_num'] for c in calls] == [1, 2, 3]
    assert [event['id'] for event in events] == ['event-0', 'event-200']
    middle = recorder.outputs[1]
    assert middle[0] is False
    assert middle[1]['selection']['matched'] == 0
    assert '分页尚未结束' in middle[2].text
    assert middle[2].details == ''
    final = recorder.outputs[-1]
    assert final[1]['selection']['cumulative'] == {
        'received': 201, 'source_unique': 201, 'matched_unique': 2,
        'excluded_unique': 199, 'duplicates': 0}
    assert '符合条件 2 条' in final[2].text
    assert 'Excluded' not in '\n'.join(outcome[2].details for outcome in recorder.outputs)


async def test_all_excluded_is_complete_zero_matching_with_source_counts():
    adapter, calls = pages_adapter([[item(i) for i in range(100)], [item(i) for i in range(100, 105)]], 105)
    recorder = QuietRecorder()
    assert await runtime.query_device(adapter, 'fixture', 0, 100, recorder, selection=RULE) == []
    assert len(calls) == 2
    assert recorder.outputs[-1][0] is True
    assert recorder.outputs[-1][1]['selection']['cumulative']['source_unique'] == 105
    assert '原始累计去重 105 条，符合条件 0 条，排除 105 条' in recorder.outputs[-1][2].text


def test_unique_count_is_independent_of_selected_count_and_duplicates():
    batch = IncidentPages('fixture', RULE)
    selected, complete = batch.add([item(i, i % 2 == 0) for i in range(100)], 105, 100)
    assert len(selected) == 50 and not complete
    selected, complete = batch.add([item(i, i % 2 == 0) for i in range(95, 105)], 105, 100)
    assert complete
    assert batch.counts == {'received': 110, 'source_unique': 105, 'matched_unique': 53,
                            'excluded_unique': 52, 'duplicates': 5}
    assert len(batch.events) == 53


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', {}, []])
def test_unknown_selection_never_means_unwhitelisted(invalid):
    batch = IncidentPages('fixture', SelectionRule('invalid synthetic rule', lambda raw: invalid))
    with pytest.raises(ContractError, match='筛选结果无效'):
        batch.add([item(1)], 1, 100)


def test_missing_selection_field_fails_and_duplicate_status_change_fails():
    batch = IncidentPages('fixture', RULE)
    with pytest.raises(ContractError, match='字段缺失或未知'):
        batch.add([{'uuId': 'unknown'}], 1, 100)
    batch = IncidentPages('fixture', RULE)
    batch.add([item(i, True) for i in range(100)], 101, 100)
    with pytest.raises(ContractError, match='筛选状态变化'):
        batch.add([item(0, False), item(100, True)], 101, 100)


@pytest.mark.parametrize('case', ['repeat', 'total_changed', 'early_end', 'over_total', 'budget'])
async def test_exclusion_does_not_hide_incomplete_source_pages(case):
    batch = IncidentPages('fixture', RULE)
    first = [item(i) for i in range(100)]
    if case == 'budget':
        adapter, _ = pages_adapter([first], 101, max_pages=1)
        with pytest.raises(ContractError, match='分页预算'):
            await runtime.query_device(adapter, 'fixture', 0, 100, QuietRecorder(), selection=RULE)
        return
    batch.add(first, 101, 100)
    with pytest.raises(ContractError):
        if case == 'repeat':
            batch.add(first, 101, 100)
        elif case == 'total_changed':
            batch.add([item(100)], 102, 100)
        elif case == 'early_end':
            batch.add([], 101, 100)
        else:
            batch.add([item(100), item(101)], 101, 100)


async def test_default_queries_keep_every_record_without_selection_metadata():
    adapter, _ = pages_adapter([[item(1, False), {'uuId': 'no-status'}]], 2)
    recorder = QuietRecorder()
    events = await runtime.query_device(adapter, 'fixture', 0, 100, recorder)
    assert len(events) == 2
    assert 'selection' not in recorder.outputs[0][1]
    assert '本轮 XDR 安全事件查询到 2 条事件' in recorder.outputs[0][2].text


@pytest.mark.parametrize('mode', ['matches', 'all_excluded', 'late_failure', 'unknown'])
async def test_injected_selection_persists_only_matches_and_keeps_cursor_atomic(tmp_path, monkeypatch, mode):
    directory = tmp_path / 'monitor-project'
    directory.mkdir()
    project = await Project.create(owner_id='owner', name='selection-fixture', worktree=str(directory))
    policy = MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['fixture'])
    original = runtime.query_device
    async def selected(*args):
        return await original(*args, selection=RULE)
    monkeypatch.setattr(runtime, 'query_device', selected)
    calls = []
    class Adapter:
        def __init__(self, policy, session):
            self.policy = policy

        async def call(self, device, params, message):
            calls.append(params.copy())
            if params['action'] == 'get_entities':
                return {'data': {'list': []}}
            page = params['page_num']
            if page == 2 and mode == 'late_failure':
                raise ContractError('隔离测试晚页失败')
            records = [item(i, i in {0, 104} and mode != 'all_excluded')
                       for i in (range(100) if page == 1 else range(100, 105))]
            if mode == 'unknown' and page == 2:
                del records[-1]['synthetic_match']
            return {'data': {'list': records, 'total': 105}}

    await write('INSERT INTO monitor_cursors VALUES(?,?,?,?)', (policy.owner, policy.scope, 'fixture', 1000))
    scheduler = await TaskManager.create_scheduler(title='selection fixture', context={'monitoring': policy.model_dump()})
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    await runtime.run(execution, policy, Adapter)
    attempt = (await rows('SELECT * FROM monitor_attempts'))[0]
    data = await snapshot(policy.owner, policy.scope, attempt['business_date'])
    cursor = (await rows('SELECT * FROM monitor_cursors'))[0]
    entity_ids = [call['uuid'] for call in calls if call['action'] == 'get_entities']
    expected = 2 if mode == 'matches' else 0
    assert len(data['events']) == data['metrics']['events'] == json.loads(attempt['result'])['events'] == expected
    assert entity_ids == (['event-0', 'event-104'] if expected else [])
    assert len(await rows('SELECT * FROM monitor_observations')) == expected
    if mode in {'late_failure', 'unknown'}:
        assert attempt['status'] == 'failed' and cursor['through_time'] == 1000
    else:
        assert attempt['status'] == 'completed' and cursor['through_time'] > 1000
        query_steps = [step for step in data['runs'][0]['steps'] if step['tool'] == '查询 XDR 事件']
        counts = json.loads(query_steps[-1]['output'])['selection']['cumulative']
        assert counts['source_unique'] == 105 and counts['matched_unique'] == expected
    messages = await Message.list_with_parts(attempt['session_id'])
    details = [part.metadata.get('details', '') for message in messages for part in message.parts
               if part.type == 'text' and part.metadata and part.metadata.get('monitoringSummary')]
    assert 'Excluded-' not in '\n'.join(details)
    report = (await rows('SELECT * FROM monitor_reports'))[0]['content']
    assert f'去重事件：{expected}' in report
    assert 'Excluded-' not in report
