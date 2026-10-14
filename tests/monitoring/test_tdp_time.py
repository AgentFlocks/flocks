"""Time validation uses mock devices/clocks only; it never queries a live TDP."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.monitoring import capabilities as c
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.query_time import QueryTimeError, normalize_window, tdp_window
from flocks.session.interaction_policy import monitoring_read_scope, require_monitor_read
from flocks.tool.registry import ToolResult
from flocks.tool.structured_output import capture_output


@pytest.mark.parametrize('window', [
    {'start': 1, 'end': 101}, {'start': '1', 'end': '101'},
    {'start': ' 1 ', 'end': '00101'}, {'start': 1.0, 'end': 101.0},
])
def test_same_second_values_normalize_without_changing_boundaries(window):
    original = dict(window)
    assert normalize_window(window) == {'start': 1, 'end': 101}
    assert window == original


@pytest.mark.parametrize('window,code,field', [
    (None, 'missing_window', 'window'), ([], 'missing_window', 'window'),
    ({}, 'missing_value', 'start'), ({'start': 1}, 'missing_value', 'end'),
    ({'start': 1, 'end': None}, 'missing_value', 'end'),
    ({'start': 1, 'end': ''}, 'missing_value', 'end'),
    ({'start': 1, 'end': True}, 'invalid_timestamp', 'end'),
    ({'start': 1, 'end': 1.5}, 'invalid_timestamp', 'end'),
    ({'start': 1, 'end': float('nan')}, 'invalid_timestamp', 'end'),
    ({'start': 1, 'end': float('inf')}, 'invalid_timestamp', 'end'),
    ({'start': 1, 'end': '101.0'}, 'invalid_timestamp', 'end'),
    ({'start': 1, 'end': '2026-10-13T10:12:25Z'}, 'invalid_timestamp', 'end'),
    ({'start': 1791886200000, 'end': 1791886300000}, 'invalid_timestamp', 'start'),
    ({'start': -1, 'end': 101}, 'invalid_timestamp', 'start'),
    ({'start': 101, 'end': 101}, 'invalid_order', 'window'),
    ({'start': 101, 'end': 1}, 'invalid_order', 'window'),
    ({'start': 1, 'end': 86402}, 'window_too_wide', 'window'),
])
def test_invalid_window_has_specific_safe_error_instead_of_type_error(window, code, field):
    with pytest.raises(QueryTimeError) as caught:
        normalize_window(window, source='TDP 关联查询')
    assert caught.value.code == code and caught.value.field == field
    assert caught.value.kind == 'query_time'
    assert str(caught.value).startswith('TDP 关联查询未执行：')
    assert '不能按无风险处理' in str(caught.value)
    assert '模型' not in str(caught.value)


def test_historical_window_stays_historical_and_future_end_only_shortens():
    assert tdp_window({'start': '1', 'end': '101'}, 864000) == {'start': 1, 'end': 101}
    assert tdp_window({'start': 1, 'end': 101}, 99) == {'start': 1, 'end': 99}
    assert tdp_window({'start': 0, 'end': 86400}, 86400) == {'start': 0, 'end': 86400}
    with pytest.raises(QueryTimeError, match='尚未开始') as caught:
        tdp_window({'start': 101, 'end': 201}, 101)
    assert caught.value.code == 'future_window'
    with pytest.raises(QueryTimeError) as caught:
        tdp_window({'start': 1, 'end': 101}, 'not-a-clock')
    assert caught.value.field == 'current'


@pytest.fixture
def query_case(tmp_path, monkeypatch):
    policy = MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['xdr'],
                              correlation_devices=['tdp'])
    capability = c.Capability('tdp-cap', 'tdp', 'tdp_log_search__tdp', 'tdp', 'Fixture TDP', 'unknown')
    event = {'id': 'incident-original', 'device': 'xdr', 'host': '192.0.2.1',
             'investigationWindow': {'start': '1', 'end': '101'}}
    monkeypatch.setattr(c, 'assert_active', AsyncMock())
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([capability], [])))
    skill, clock = AsyncMock(return_value='loaded'), AsyncMock(return_value=99)
    monkeypatch.setattr(c, 'tdp_skill', skill)
    monkeypatch.setattr(c, 'tdp_timestamp', clock)
    seen = []

    async def execute(name, ctx, **params):
        await require_monitor_read(name, params, ctx.session_id)
        seen.append(params)
        result = ToolResult(success=True, output={'data': {'items': []}})
        capture_output(ctx, name, result)
        return result

    monkeypatch.setattr(c.ToolRegistry, 'execute', execute)
    return SimpleNamespace(policy=policy, capability=capability, event=event, seen=seen, skill=skill, clock=clock)


async def test_tdp_query_accepts_historical_numeric_strings_and_pins_host_scope(query_case):
    case = query_case
    with monitoring_read_scope(case.policy.tool, case.policy.devices):
        value, params = await c.query(case.policy, 'session', 'message', case.capability, case.event, 'related')
    assert value == {'data': {'items': []}}
    assert params == {'action': 'search', 'time_from': 1, 'time_to': 99,
                      'net_data_type': ['attack', 'risk', 'action'], 'size': 50,
                      'sql': "net.src_ip = '192.0.2.1' OR net.dest_ip = '192.0.2.1'"}
    assert case.seen == [{**params, 'device_id': 'tdp'}]
    assert case.skill.await_count == case.clock.await_count == 1
    assert case.event['investigationWindow'] == {'start': '1', 'end': '101'}


@pytest.mark.parametrize('window', [None, {}, {'start': 1, 'end': None}, {'start': 1, 'end': 'oops'},
                                    {'start': 101, 'end': 1}, {'start': 1, 'end': 86402},
                                    {'start': 1791886200000, 'end': 1791886300000}])
async def test_bad_window_never_executes_tdp_or_dynamic_clock(query_case, window):
    case = query_case
    case.event['investigationWindow'] = window
    with pytest.raises(QueryTimeError):
        await c.query(case.policy, 'session', 'message', case.capability, case.event, 'related')
    assert case.seen == [] and case.clock.await_count == 0


async def test_future_window_uses_dynamic_clock_but_does_not_execute_tdp(query_case):
    case = query_case
    case.event['investigationWindow'] = {'start': 101, 'end': 201}
    with pytest.raises(QueryTimeError, match='尚未开始'):
        await c.query(case.policy, 'session', 'message', case.capability, case.event, 'related')
    assert case.seen == [] and case.clock.await_count == 1


async def test_tdp_clock_is_constant_bash_uv_command_without_event_input(monkeypatch):
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'1791886200\n', b'')))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(c.asyncio, 'create_subprocess_exec', spawn)
    assert await c.tdp_timestamp() == 1791886200
    args, options = spawn.call_args
    assert args[:2] == ('bash', '-c')
    assert 'uv run --no-project python' in args[2]
    assert 'datetime.now(timezone.utc)' in args[2]
    assert options['stdout'] == asyncio.subprocess.PIPE
    assert options['stderr'] == asyncio.subprocess.DEVNULL


@pytest.mark.parametrize('returncode,output', [(1, b''), (0, b'not-a-clock\n')])
async def test_tdp_clock_failure_is_explicit_and_does_not_invent_timestamp(monkeypatch, returncode, output):
    process = SimpleNamespace(returncode=returncode, communicate=AsyncMock(return_value=(output, b'')))
    monkeypatch.setattr(c.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    with pytest.raises(c.ContractError, match='动态时间计算失败'):
        await c.tdp_timestamp()


async def test_tdp_clock_subprocess_start_failure_names_actual_environment(monkeypatch):
    monkeypatch.setattr(c.asyncio, 'create_subprocess_exec', AsyncMock(side_effect=FileNotFoundError()))
    with pytest.raises(c.ContractError, match='时间计算环境不可用'):
        await c.tdp_timestamp()


@pytest.mark.parametrize('exit_race', [False, True])
async def test_tdp_clock_timeout_reaps_subprocess_and_names_actual_stage(monkeypatch, exit_race):
    process = SimpleNamespace(returncode=None, communicate=AsyncMock(), wait=AsyncMock(),
                              kill=Mock(side_effect=ProcessLookupError() if exit_race else None))
    monkeypatch.setattr(c.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))

    async def timeout(awaitable, seconds):
        assert seconds == 15
        awaitable.close()
        raise TimeoutError()

    monkeypatch.setattr(c.asyncio, 'wait_for', timeout)
    with pytest.raises(c.ContractError, match='动态时间计算超时'):
        await c.tdp_timestamp()
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_tdp_clock_cancellation_stays_cancelled_after_subprocess_cleanup(monkeypatch):
    started = asyncio.Event()

    async def waiting():
        started.set()
        await asyncio.Event().wait()

    process = SimpleNamespace(returncode=None, communicate=waiting, wait=AsyncMock(), kill=Mock())
    monkeypatch.setattr(c.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    task = asyncio.create_task(c.tdp_timestamp())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
