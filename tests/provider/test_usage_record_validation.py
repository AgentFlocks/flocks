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
