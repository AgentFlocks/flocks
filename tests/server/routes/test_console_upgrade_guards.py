"""Regression guards for console login / upgrade request state machine.

Covers the defects found during the 2026-09-14 full test run:
- whitespace-only company / applicant_name are rejected (422), not stored blank
- only one pending upgrade request may exist at a time (409)
- an activated request cannot be withdrawn (409)
- a request whose install stream is running refuses a second concurrent /start (409)
- receipt timestamps written by the reporters are persisted after the done stage
- an exchanged console login record cannot be replayed through finish (local reject)
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from flocks.storage.storage import Storage

pytestmark = pytest.mark.asyncio

_BODY = {
    "product": "Flocks Pro",
    "license_type": "poc",
    "company": "acme",
    "applicant_name": "alice",
    "applicant_email": "alice@example.com",
    "applicant_phone": "13800000000",
}


async def _bind_console_session() -> None:
    await Storage.set(
        "console:session",
        {
            "console_login_id": "login_ok",
            "console_session_token": "token_abc",
            "fingerprint": "fp_1",
            "install_id": "inst_1",
            "passport_uid": "pass_1",
            "user_display": "alice",
            "updated_at": "2026-05-08T08:00:00+00:00",
        },
        "json",
    )


@pytest.fixture(autouse=True)
def _local_mode(monkeypatch: pytest.MonkeyPatch):
    # No console base URL → requests are created/cancelled locally, no network.
    monkeypatch.delenv("FLOCKS_CONSOLE_BASE_URL", raising=False)


@pytest.mark.parametrize("field", ["company", "applicant_name"])
async def test_whitespace_only_required_text_rejected(client: AsyncClient, field: str):
    await _bind_console_session()
    body = dict(_BODY)
    body[field] = "   "
    resp = await client.post("/api/console/upgrade-requests", json=body)
    assert resp.status_code == 422, resp.text
    assert f"{field} is required." in resp.text


async def test_second_pending_request_conflicts(client: AsyncClient):
    await _bind_console_session()
    first = await client.post("/api/console/upgrade-requests", json=_BODY)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"

    second = await client.post("/api/console/upgrade-requests", json=_BODY)
    assert second.status_code == 409, second.text
    assert "已有升级申请在处理中" in second.json()["message"]

    listed = await client.get("/api/console/upgrade-requests")
    assert [item["status"] for item in listed.json()] == ["pending"]


async def test_activated_request_cannot_be_cancelled(client: AsyncClient):
    await _bind_console_session()
    created = await client.post("/api/console/upgrade-requests", json=_BODY)
    request_id = created.json()["request_id"]
    from flocks.server.routes.console_upgrade import _request_key

    raw = await Storage.get(_request_key(request_id))
    raw["status"] = "activated"
    await Storage.set(_request_key(request_id), raw, "json")

    resp = await client.post(f"/api/console/upgrade-requests/{request_id}/cancel")
    assert resp.status_code == 409, resp.text
    assert resp.json()["message"] == "已激活的申请不可撤回"
    assert (await Storage.get(_request_key(request_id)))["status"] == "activated"


async def test_concurrent_start_rejected_and_receipts_persisted(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    import asyncio

    from flocks.server.routes import console_upgrade as routes

    await _bind_console_session()
    created = await client.post("/api/console/upgrade-requests", json=_BODY)
    request_id = created.json()["request_id"]
    raw = await Storage.get(routes._request_key(request_id))
    raw["status"] = "approved"
    await Storage.set(routes._request_key(request_id), raw, "json")

    class _Progress:
        def __init__(self, stage: str, message: str = ""):
            self.stage = stage
            self.message = message

        def model_dump_json(self) -> str:
            return json.dumps({"stage": self.stage, "message": self.message, "success": self.stage != "error"})

    gate = asyncio.Event()

    async def fake_install(**kwargs):
        yield _Progress("fetching", "f")
        await gate.wait()
        yield _Progress("done", "d")

    async def _noop(*a, **k):
        return None

    async def fake_report(raw_record, *, install_result, error_message=None):
        raw_record.setdefault("details", {})["install_receipt_reported_at"] = "2026-09-14T00:00:00+00:00"

    async def fake_mark(raw_record):
        raw_record.setdefault("details", {})["console_activated_reported_at"] = "2026-09-14T00:00:01+00:00"

    monkeypatch.setattr(routes, "perform_pro_bundle_install", fake_install)
    monkeypatch.setattr(routes, "_maybe_activate_pro_license", _noop)
    monkeypatch.setattr(routes, "_maybe_refresh_pro_license", _noop)
    monkeypatch.setattr(routes, "_read_pro_bundle_install_marker", lambda: {"bundle_version": "1.0"})
    monkeypatch.setattr(routes, "_record_pro_capability", lambda details: {"pro_enabled": True})
    monkeypatch.setattr(routes, "_enrich_record_from_install_marker", lambda raw_record: None)
    monkeypatch.setattr(routes, "_report_pro_bundle_installation", fake_report)
    monkeypatch.setattr(routes, "_mark_console_upgrade_activated", fake_mark)

    async def _consume_first():
        async with client.stream("POST", f"/api/console/upgrade-requests/{request_id}/start") as resp:
            assert resp.status_code == 200
            frames = []
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    frames.append(json.loads(line[5:].strip()))
            return frames

    first_task = asyncio.create_task(_consume_first())
    # Wait until the first stream has entered the install loop.
    for _ in range(50):
        if request_id in routes._MANUAL_UPGRADE_RUNNING:
            break
        await asyncio.sleep(0.02)
    assert request_id in routes._MANUAL_UPGRADE_RUNNING

    second = await client.post(f"/api/console/upgrade-requests/{request_id}/start")
    assert second.status_code == 409, second.text
    assert second.json()["message"] == "该申请的升级正在进行中"

    gate.set()
    frames = await first_task
    assert [f["stage"] for f in frames] == ["fetching", "done"]
    assert request_id not in routes._MANUAL_UPGRADE_RUNNING

    stored = await Storage.get(routes._request_key(request_id))
    assert stored["details"]["install_receipt_reported_at"] == "2026-09-14T00:00:00+00:00"
    assert stored["details"]["console_activated_reported_at"] == "2026-09-14T00:00:01+00:00"
    assert stored["status"] == "activated"

    # A finished request can be started again only if still installable; the
    # running guard itself must have been released.
    third = await client.post(f"/api/console/upgrade-requests/{request_id}/start")
    assert third.status_code != 409


async def test_finish_console_login_rejects_replay(monkeypatch: pytest.MonkeyPatch):
    from flocks.console.login import ConsoleLoginService

    monkeypatch.setenv("FLOCKS_CONSOLE_BASE_URL", "https://console.mock")
    calls = {"exchange": 0}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"console_session_token": "tok", "passport_uid": "u1", "user_display": "Alice", "expires_at": None}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, **kwargs):
            calls["exchange"] += 1
            return _Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    await Storage.set("console:login:cl-1", {"state": "S1", "status": "pending_remote"}, "json")

    first = await ConsoleLoginService.finish_console_login(console_login_id="cl-1", state="S1")
    assert first["console_session_token"] == "tok" and first["user_display"] == "Alice"
    assert calls["exchange"] == 1
    assert (await Storage.get("console:login:cl-1"))["status"] == "exchanged"

    with pytest.raises(ValueError, match="已使用"):
        await ConsoleLoginService.finish_console_login(console_login_id="cl-1", state="S1")
    # No second exchange was sent to the console.
    assert calls["exchange"] == 1


async def test_list_skips_corrupt_record(client: AsyncClient):
    from flocks.server.routes.console_upgrade import _push_request_id, _request_key

    await _bind_console_session()
    created = await client.post("/api/console/upgrade-requests", json=_BODY)
    good_id = created.json()["request_id"]
    # A record missing mandatory fields (created_at/updated_at) must not 500 the list.
    await Storage.set(_request_key("dirty-1"), {"request_id": "dirty-1", "status": "pending"}, "json")
    await _push_request_id("dirty-1")
    resp = await client.get("/api/console/upgrade-requests")
    assert resp.status_code == 200, resp.text
    assert [item["request_id"] for item in resp.json()] == [good_id]
