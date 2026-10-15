"""HTTP boundaries for the read-only incident list; no remote devices or mail."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from flocks.monitoring import event_list
from flocks.monitoring.models import COMPONENT_ID
from flocks.server.routes import security_monitoring as api


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    app.dependency_overrides[api.require_user] = lambda: SimpleNamespace(id='owner')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as value:
        yield value


BASE = '/api/monitoring/host-security-monitor/events'
DATES = {'start_date': '2026-10-01', 'end_date': '2026-10-12'}


async def test_http_passes_current_owner_and_24_hour_ten_row_defaults(client, monkeypatch):
    read = AsyncMock(return_value={'items': [], 'total': 0})
    monkeypatch.setattr(event_list, 'list_events', read)
    response = await client.get(BASE, params=DATES)
    assert response.status_code == 200
    args, kwargs = read.call_args
    assert args == ('owner', COMPONENT_ID)
    assert kwargs['window_hours'] == 24 and kwargs['include_backlog'] and kwargs['group']
    assert kwargs['limit'] == 10


@pytest.mark.parametrize('extra', [
    {'window_hours': 0}, {'window_hours': 25}, {'limit': 101},
    {'offset': -1}, {'assessment': 'safe'}, {'risk': 'safe'}, {'handling': 'done'}, {'search': 'a' * 201},
])
async def test_http_rejects_invalid_filters_before_read(client, monkeypatch, extra):
    read = AsyncMock()
    monkeypatch.setattr(event_list, 'list_events', read)
    response = await client.get(BASE, params={**DATES, **extra})
    assert response.status_code == 422
    read.assert_not_called()


async def test_http_rejects_invalid_date_semantics(client, monkeypatch):
    monkeypatch.setattr(event_list, 'list_events', AsyncMock(side_effect=ValueError('日期范围无效')))
    response = await client.get(BASE, params={'start_date': '2026-10-32', 'end_date': '2026-10-01'})
    assert response.status_code == 422


async def test_http_detail_is_owner_scoped(client, monkeypatch):
    read = AsyncMock(return_value={'event': {'key': 'device:incident:123'}})
    monkeypatch.setattr(event_list, 'detail', read)
    response = await client.get(BASE + '/detail', params={'event_key': 'device:incident:123'})
    assert response.status_code == 200
    read.assert_awaited_once_with('owner', COMPONENT_ID, 'device:incident:123')


async def test_http_unknown_or_other_owner_event_returns_404(client, monkeypatch):
    monkeypatch.setattr(event_list, 'detail', AsyncMock(side_effect=LookupError('事件不存在')))
    response = await client.get(BASE + '/detail', params={'event_key': 'other:incident:123'})
    assert response.status_code == 404


async def test_http_read_error_never_becomes_empty_success(client, monkeypatch):
    monkeypatch.setattr(event_list, 'list_events', AsyncMock(side_effect=RuntimeError('private db path')))
    response = await client.get(BASE, params=DATES)
    assert response.status_code == 503
    assert '不能据此判断没有事件' in response.json()['detail']
    assert 'private db path' not in response.text


async def test_http_timeout_is_unavailable_not_no_events(client, monkeypatch):
    original = asyncio.wait_for

    async def short_wait(operation, timeout):
        return await original(operation, timeout=0.001)

    async def slow(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(event_list, 'list_events', slow)
    monkeypatch.setattr(api.asyncio, 'wait_for', short_wait)
    response = await client.get(BASE, params=DATES)
    assert response.status_code == 503


async def test_http_forwards_business_assessment_filter(client, monkeypatch):
    read = AsyncMock(return_value={'items': [], 'total': 0})
    monkeypatch.setattr(event_list, 'list_events', read)
    response = await client.get(BASE, params={**DATES, 'assessment': 'insufficient'})
    assert response.status_code == 200
    assert read.call_args.kwargs['assessment'] == 'insufficient'


@pytest.mark.parametrize('state', ['ended', 'source_closed_risk', 'source_closed_benign', 'source_closed_unknown'])
async def test_http_accepts_source_closure_filters_that_previously_failed_validation(client, monkeypatch, state):
    read = AsyncMock(return_value={'items': [], 'total': 0})
    monkeypatch.setattr(event_list, 'list_events', read)
    response = await client.get(BASE, params={**DATES, 'assessment': state})
    assert response.status_code == 200
    assert read.call_args.kwargs['assessment'] == state


async def test_http_forwards_both_sort_clocks_and_shape(client, monkeypatch):
    read = AsyncMock(return_value={'items': [], 'total': 0})
    monkeypatch.setattr(event_list, 'list_events', read)
    response = await client.get(BASE, params={**DATES, 'sort_by': 'last_processed_at', 'sort_order': 'asc', 'shape': 'grouped'})
    assert response.status_code == 200
    assert read.call_args.kwargs['sort_by'] == 'last_processed_at'
    assert read.call_args.kwargs['sort_order'] == 'asc'
    assert read.call_args.kwargs['shape'] == 'grouped'


@pytest.mark.parametrize('extra', [{'sort_by': 'occurrence_at'}, {'sort_order': 'backward'}, {'shape': 'all'}])
async def test_http_rejects_unknown_sort_and_shape(client, monkeypatch, extra):
    read = AsyncMock()
    monkeypatch.setattr(event_list, 'list_events', read)
    assert (await client.get(BASE, params={**DATES, **extra})).status_code == 422
    read.assert_not_called()


async def test_http_source_filter_uses_real_list_and_preserves_summary_card_counts(client):
    from test_event_list import DAY, incident, install, observe
    await install()
    await observe(incident('source-closed', dealStatus=40))
    await observe(incident('open'))
    response = await client.get(BASE, params={'start_date': DAY, 'end_date': DAY,
        'assessment': 'ended', 'sort_by': 'last_processed_at', 'sort_order': 'asc'})
    assert response.status_code == 200
    value = response.json()
    assert value['metrics']['events'] == 1 and value['summary_metrics']['events'] == 2
    assert value['items'][0]['members'][0]['source_closed']
    assert value['items'][0]['members'][0]['assessment']['label'] == '源端关闭'
    assert '_risk_restore' not in value
