"""Data retention settings endpoints: admin only, validated, saved to flocks.json, cleanup on save."""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.retention import DataRetentionScheduler
from flocks.utils.log import get_log_retention_days, set_log_retention_days_override

DAY_MS = 86_400_000


@pytest.fixture(autouse=True)
def _reset_log_override(monkeypatch):
    monkeypatch.delenv("FLOCKS_LOG_RETENTION_DAYS", raising=False)
    set_log_retention_days_override(None)
    yield
    set_log_retention_days_override(None)


@pytest.fixture
def run_requests(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        DataRetentionScheduler,
        "request_run",
        classmethod(lambda cls: calls.append("run")),
    )
    return calls


def _member_client() -> TestClient:
    from flocks.server.routes.config import router

    app = FastAPI()

    @app.middleware("http")
    async def member_auth(request, call_next):
        request.state.auth_user = AuthUser(
            id="member-test",
            username="member-test",
            role="member",
            status="active",
            must_reset_password=False,
        )
        return await call_next(request)

    app.include_router(router, prefix="/api/config")
    return TestClient(app, raise_server_exceptions=True)


def _flocks_json() -> dict:
    from flocks.config.config_writer import ConfigWriter

    path = ConfigWriter._get_config_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


async def test_default_policy_keeps_previous_behaviour(client):
    response = await client.get("/api/config/data-retention")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["logDays"], body["messageDays"], body["sessionDays"]) == (30, 0, 0)
    assert body["running"] is False
    assert body["lastRun"] is None


async def test_saving_writes_flocks_json_applies_log_days_and_starts_cleanup(client, run_requests):
    response = await client.patch(
        "/api/config/data-retention",
        json={"logDays": 14, "messageDays": 30, "sessionDays": 90},
    )

    assert response.status_code == 200, response.text
    assert (response.json()["logDays"], response.json()["messageDays"], response.json()["sessionDays"]) == (14, 30, 90)
    assert _flocks_json()["dataRetention"] == {"logDays": 14, "messageDays": 30, "sessionDays": 90}
    assert get_log_retention_days() == 14
    assert run_requests == ["run"]

    again = await client.get("/api/config/data-retention")
    assert again.json()["sessionDays"] == 90


async def test_saving_keeps_unrelated_config_and_extra_keys(client, run_requests):
    from flocks.config.config_writer import ConfigWriter

    ConfigWriter._write_raw({
        "toolFailure": {"disableOnRepeatedFailure": False},
        "dataRetention": {"logDays": 7, "note": "kept"},
    })

    response = await client.patch(
        "/api/config/data-retention",
        json={"logDays": 0, "messageDays": 0, "sessionDays": 0},
    )

    assert response.status_code == 200, response.text
    saved = _flocks_json()
    assert saved["toolFailure"] == {"disableOnRepeatedFailure": False}
    assert saved["dataRetention"] == {"logDays": 0, "messageDays": 0, "sessionDays": 0, "note": "kept"}
    assert get_log_retention_days() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"logDays": -1, "messageDays": 0, "sessionDays": 0},
        {"logDays": 3651, "messageDays": 0, "sessionDays": 0},
        {"logDays": 30, "messageDays": 0},
        {"logDays": "soon", "messageDays": 0, "sessionDays": 0},
        {},
    ],
)
async def test_invalid_policies_are_rejected_without_saving(client, run_requests, payload):
    response = await client.patch("/api/config/data-retention", json=payload)
    preview = await client.post("/api/config/data-retention/preview", json=payload)

    assert response.status_code == 422
    assert preview.status_code == 422
    assert "dataRetention" not in _flocks_json()
    assert run_requests == []


async def test_preview_counts_without_deleting(client, tmp_path):
    from flocks.session.session import Session
    from flocks.storage.storage import Storage

    session = await Session.create(project_id="proj", directory=str(tmp_path))
    key = f"session:{session.project_id}:{session.id}"
    stored = await Storage.get(key)
    old_ms = int(time.time() * 1000) - 60 * DAY_MS
    stored["time"]["created"] = old_ms
    stored["time"]["updated"] = old_ms
    await Storage.set(key, stored, "session")
    Session.invalidate_cache()

    response = await client.post(
        "/api/config/data-retention/preview",
        json={"logDays": 30, "messageDays": 0, "sessionDays": 30},
    )

    assert response.status_code == 200, response.text
    assert response.json()["sessions"] == 1
    assert set(response.json()) == {
        "sessions", "messages", "logPaths", "logBytes", "recordFiles", "recordBytes", "skippedBusy",
    }
    assert await Session.get_by_id_unfiltered(session.id) is not None
    assert "dataRetention" not in _flocks_json()


async def test_members_cannot_change_policy_via_generic_config_endpoint(client):
    # The generic PATCH /api/config/ must not be a back door around the admin check.
    from flocks.auth.context import AuthUser

    async def as_member(request, call_next):
        request.state.auth_user = AuthUser(
            id="member", username="member", role="member", status="active", must_reset_password=False,
        )
        return await call_next(request)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flocks.server.routes.config import router

    app = FastAPI()
    app.middleware("http")(as_member)
    app.include_router(router, prefix="/api/config")
    member = TestClient(app, raise_server_exceptions=False)

    # Changing the policy -> 403; an unrelated partial save or an unchanged
    # echo still works for a member.
    assert member.patch("/api/config/", json={"dataRetention": {"logDays": 1, "messageDays": 1, "sessionDays": 1}}).status_code == 403
    assert member.patch("/api/config/", json={"toolFailure": {"disableOnRepeatedFailure": True}}).status_code == 200
    assert member.patch("/api/config/", json={"dataRetention": {"logDays": 30, "messageDays": 0, "sessionDays": 0}}).status_code == 200
    # The destructive 1/1/1 never landed; at most the harmless default is stored.
    saved = _flocks_json().get("dataRetention")
    assert saved in (None, {"logDays": 30, "messageDays": 0, "sessionDays": 0})


async def test_members_cannot_change_policy_via_snake_case_key(client):
    # The config model accepts the field name too, so the guard must cover both.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flocks.auth.context import AuthUser
    from flocks.server.routes.config import router

    app = FastAPI()

    @app.middleware("http")
    async def as_member(request, call_next):
        request.state.auth_user = AuthUser(id="m", username="m", role="member", status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(router, prefix="/api/config")
    member = TestClient(app, raise_server_exceptions=False)

    resp = member.patch("/api/config/", json={"data_retention": {"log_days": 1, "message_days": 1, "session_days": 1}})
    assert resp.status_code == 403
    assert "dataRetention" not in _flocks_json() and "data_retention" not in _flocks_json()


async def test_member_cannot_reestablish_policy_by_echoing_stale_value(client):
    # On-disk is "keep forever"; a stale worker cache might say 1/1/1. A member
    # echoing 1/1/1 must still be refused, because it differs from disk.
    from flocks.config.config import Config, ConfigInfo
    from flocks.config.config_writer import ConfigWriter
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flocks.auth.context import AuthUser

    ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})
    # Simulate a cache that lags the on-disk "forever".
    Config._cached_config = ConfigInfo.model_validate({"dataRetention": {"logDays": 1, "messageDays": 1, "sessionDays": 1}})

    from flocks.server.routes.config import router

    app = FastAPI()

    @app.middleware("http")
    async def as_member(request, call_next):
        request.state.auth_user = AuthUser(id="m", username="m", role="member", status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(router, prefix="/api/config")
    member = TestClient(app, raise_server_exceptions=False)

    resp = member.patch("/api/config/", json={"dataRetention": {"logDays": 1, "messageDays": 1, "sessionDays": 1}})
    assert resp.status_code == 403
    # Disk still says keep forever.
    assert _flocks_json()["dataRetention"] == {"logDays": 0, "messageDays": 0, "sessionDays": 0}


async def test_member_cannot_bypass_guard_with_dual_name_keys(client):
    # The exploit: a null (or non-object) camelCase value sent alongside a real
    # snake_case value. The guard used to read camelCase-first, see the null, and
    # judge "unchanged" — while the save re-parsed the snake_case value and wrote
    # it. The guard now resolves the policy exactly as the save does, over the
    # same two keys, so the mix cannot diverge.
    from flocks.auth.context import AuthUser
    from flocks.server.routes.config import router

    app = FastAPI()

    @app.middleware("http")
    async def as_member(request, call_next):
        request.state.auth_user = AuthUser(id="m", username="m", role="member", status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(router, prefix="/api/config")
    member = TestClient(app, raise_server_exceptions=False)

    destructive = {"logDays": 1, "messageDays": 1, "sessionDays": 1}

    resp = member.patch("/api/config/", json={
        "dataRetention": None,
        "data_retention": {"log_days": 1, "message_days": 1, "session_days": 1},
    })
    assert resp.status_code == 403, resp.text
    assert _flocks_json().get("dataRetention") != destructive

    # Non-object camelCase values must not be a back door either.
    for camel in ([], "soon"):
        member.patch("/api/config/", json={
            "dataRetention": camel,
            "data_retention": {"log_days": 1, "message_days": 1, "session_days": 1},
        })
        assert _flocks_json().get("dataRetention") != destructive


async def test_admin_relaxing_policy_via_dual_name_notifies_scheduler(client, run_requests):
    # Disk currently deletes aggressively; an admin relaxing it to "keep forever"
    # — even through a dual-name payload — must register as a real change so the
    # scheduler is kicked (bumping the generation that supersedes an in-flight,
    # stricter round). The guard used to mis-read this as "unchanged".
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    ConfigWriter._write_raw({"dataRetention": {"logDays": 30, "messageDays": 30, "sessionDays": 30}})
    Config.clear_cache()

    resp = await client.patch("/api/config/", json={
        "dataRetention": None,
        "data_retention": {"log_days": 0, "message_days": 0, "session_days": 0},
    })
    assert resp.status_code == 200, resp.text
    assert run_requests == ["run"]


_FOREVER_SNAKE = {"log_days": 0, "message_days": 0, "session_days": 0}


@pytest.mark.parametrize("camel", [None, [], "soon", False, 123])
async def test_member_cannot_change_policy_held_on_disk_under_mixed_spellings(client, camel):
    # On disk the policy in force is "keep forever", stored with a null or junk
    # camelCase key next to the real snake_case one (the loader resolves that to
    # forever). The guard must read the disk the same way: a member saving
    # camelCase 30/0/0 would make logs deletable, so it is a real change and is
    # refused — it used to compare against the default and slip through.
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    on_disk = {"dataRetention": camel, "data_retention": _FOREVER_SNAKE}
    ConfigWriter._write_raw(on_disk)
    Config.clear_cache()

    resp = _member_client().patch(
        "/api/config/", json={"dataRetention": {"logDays": 30, "messageDays": 0, "sessionDays": 0}},
    )

    assert resp.status_code == 403, resp.text
    assert _flocks_json() == on_disk  # nothing written


async def test_admin_relaxing_policy_held_on_disk_under_mixed_spellings_notifies(client, run_requests):
    # Same disk state; an admin's save is a real change, so it goes through and
    # kicks the scheduler (the guard used to call it "unchanged" and skip that).
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter

    ConfigWriter._write_raw({"dataRetention": None, "data_retention": _FOREVER_SNAKE})
    Config.clear_cache()

    resp = await client.patch(
        "/api/config/", json={"dataRetention": {"logDays": 30, "messageDays": 0, "sessionDays": 0}},
    )

    assert resp.status_code == 200, resp.text
    assert run_requests == ["run"]


async def test_generic_config_change_triggers_the_scheduler(client, monkeypatch):
    # Changing the policy through the generic endpoint must kick the cleanup too,
    # the same as the dedicated endpoint, so an in-flight round is superseded.
    from flocks.retention import DataRetentionScheduler

    calls = []
    monkeypatch.setattr(DataRetentionScheduler, "request_run", classmethod(lambda cls: calls.append("run")))

    # A no-op (unchanged) save does not trigger; a real change does.
    await client.patch("/api/config/", json={"toolFailure": {"disableOnRepeatedFailure": True}})
    assert calls == []
    resp = await client.patch("/api/config/", json={"dataRetention": {"logDays": 5, "messageDays": 5, "sessionDays": 5}})
    assert resp.status_code == 200, resp.text
    assert calls == ["run"]


async def test_admin_can_change_policy_via_generic_config_endpoint(client):
    # TUI and admins save through the generic endpoint; that must keep working.
    resp = await client.patch("/api/config", json={"dataRetention": {"logDays": 7, "messageDays": 7, "sessionDays": 7}})
    assert resp.status_code in (200, 405)  # /api/config vs /api/config/
    resp = await client.patch("/api/config/", json={"dataRetention": {"logDays": 7, "messageDays": 7, "sessionDays": 7}})
    assert resp.status_code == 200, resp.text
    assert _flocks_json()["dataRetention"] == {"logDays": 7, "messageDays": 7, "sessionDays": 7}


def test_members_cannot_read_preview_or_change_the_policy(run_requests):
    client = _member_client()
    payload = {"logDays": 1, "messageDays": 1, "sessionDays": 1}

    responses = [
        client.get("/api/config/data-retention"),
        client.post("/api/config/data-retention/preview", json=payload),
        client.patch("/api/config/data-retention", json=payload),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert "dataRetention" not in _flocks_json()
    assert run_requests == []
