"""POST /api/usage/record must reject negative token counters (422), see PLT-0269."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from flocks.provider.usage_service import RecordUsageRequest


@pytest.mark.parametrize(
    "field",
    ["input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens", "latency_ms"],
)
def test_record_usage_request_rejects_negative_counters(field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        RecordUsageRequest(provider_id="x", model_id="m", **{field: -1})
    errors = exc.value.errors()
    assert errors and errors[0]["loc"] == (field,)
    assert errors[0]["type"] == "greater_than_equal"


def test_record_usage_request_still_accepts_zero_and_numeric_strings() -> None:
    req = RecordUsageRequest(provider_id="x", model_id="m", input_tokens=0, output_tokens="10")  # type: ignore[arg-type]
    assert req.input_tokens == 0 and req.output_tokens == 10


def test_usage_record_endpoint_returns_422_for_negative_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.server.routes import usage as usage_routes

    async def _must_not_be_called(*_args, **_kwargs):  # pragma: no cover - guards the contract
        raise AssertionError("usage service must not be reached for an invalid payload")

    monkeypatch.setattr(usage_routes, "record_usage", _must_not_be_called, raising=False)
    app = FastAPI()
    app.include_router(usage_routes.router, prefix="/api/usage")
    client = TestClient(app)
    resp = client.post("/api/usage/record", json={"provider_id": "x", "model_id": "m", "input_tokens": -1})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail[0]["loc"][-1] == "input_tokens" and detail[0]["type"] == "greater_than_equal"


def _usage_client(monkeypatch: pytest.MonkeyPatch, calls: list) -> TestClient:
    from flocks.server.routes import usage as usage_routes

    async def _fake_stats(**kwargs):
        calls.append(kwargs)
        from flocks.provider.usage_service import UsageSummary

        return usage_routes.UsageStatsResponse(summary=UsageSummary(), by_provider=[], by_model=[], daily=[])

    monkeypatch.setattr(usage_routes, "get_usage_stats", _fake_stats)
    app = FastAPI()
    app.include_router(usage_routes.router, prefix="/api/usage")
    return TestClient(app)


@pytest.mark.parametrize(
    "params",
    [
        {"start_date": "invalid"},
        {"start_date": "not-a-date"},
        {"end_date": "2026-13-40"},
        {"start_date": "20261020"},
        {"end_date": "2026-10-20T25:00:00"},
    ],
)
def test_usage_summary_rejects_dates_that_are_not_iso(monkeypatch: pytest.MonkeyPatch, params: dict) -> None:
    """An unreadable bound used to answer 200 with empty statistics."""
    calls: list = []
    resp = _usage_client(monkeypatch, calls).get("/api/usage/summary", params=params)
    assert resp.status_code == 400, resp.text
    assert "ISO 8601" in resp.json()["detail"]
    assert calls == []


def test_usage_summary_rejects_a_start_after_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    resp = _usage_client(monkeypatch, calls).get(
        "/api/usage/summary", params={"start_date": "2026-10-20", "end_date": "2026-10-01"},
    )
    assert resp.status_code == 400, resp.text
    assert calls == []


@pytest.mark.parametrize(
    ("params", "start", "end"),
    [
        ({}, None, None),
        ({"start_date": "2026-10-01", "end_date": "2026-10-20"},
         "2026-10-01T00:00:00+00:00", "2026-10-20T23:59:59.999999+00:00"),
        ({"start_date": "2026-10-20T08:00:00", "end_date": "2026-10-20T09:30:00.250000+00:00"},
         "2026-10-20T08:00:00+00:00", "2026-10-20T09:30:00.250000+00:00"),
        ({"start_date": "2026-10-20 08:00", "end_date": "2026-10-21T00:00:00Z"},
         "2026-10-20T08:00:00+00:00", "2026-10-21T00:00:00+00:00"),
        ({"start_date": "2026-10-20T08:00:00+02:00"}, "2026-10-20T06:00:00+00:00", None),
    ],
)
def test_usage_summary_hands_on_bounds_in_the_stored_utc_form(
    monkeypatch: pytest.MonkeyPatch, params: dict, start, end,
) -> None:
    calls: list = []
    resp = _usage_client(monkeypatch, calls).get("/api/usage/summary", params=params)
    assert resp.status_code == 200, resp.text
    assert calls and calls[0]["start_date"] == start
    assert calls[0]["end_date"] == end


def test_usage_summary_rejects_bounds_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    resp = _usage_client(monkeypatch, calls).get("/api/usage/summary", params={"end_date": "9999-12-31T23:00:00-02:00"})
    assert resp.status_code == 400, resp.text
    assert calls == []
