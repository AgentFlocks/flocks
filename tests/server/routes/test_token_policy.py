from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from flocks.config.config import Config, ConfigInfo, TokenPolicyNoticeConfig
from flocks.notifications import token_policy as policy
from flocks.notifications.token_policy import PolicyClaim, TokenPolicyService
from flocks.storage.storage import Storage


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture
async def clock(monkeypatch):
    current = [at("2026-09-09T09:00:00+08:00")]
    monkeypatch.setattr(policy, "utc_now", lambda: current[0])
    monkeypatch.setenv("_FLOCKS_SERVICE_BOOT_ID", "boot-1")
    await TokenPolicyService.initialize_campaign()
    return current


async def reserve(user="alice", request="request-1"):
    status = await TokenPolicyService.status_for_user(user)
    assert status.notice is not None
    claim = PolicyClaim(occurrence_id=status.notice.occurrence_id, request_id=request)
    return await TokenPolicyService.claim(user, claim), claim


async def display(user="alice", request="request-1"):
    status, claim = await reserve(user, request)
    assert status.notice
    assert (await TokenPolicyService.confirm_display(user, claim)).confirmed
    return status


async def test_startup_starts_one_persistent_month(clock, monkeypatch):
    initial = await TokenPolicyService.load_campaign()
    assert initial.starts_at == clock[0]
    assert initial.expires_at == at("2026-10-09T09:00:00+08:00")
    await display()
    assert (await TokenPolicyService.status_for_user("alice")).notice is None
    clock[0] += timedelta(days=1)
    monkeypatch.setenv("_FLOCKS_SERVICE_BOOT_ID", "boot-2")
    assert (await TokenPolicyService.initialize_campaign()).starts_at == initial.starts_at
    await display(request="restart")
    assert (await TokenPolicyService.status_for_user("alice")).notice is None
    assert (await TokenPolicyService.status_for_user("bob")).notice


async def test_query_never_initializes_campaign(clock):
    await Storage.delete(policy.CAMPAIGN_KEY)
    assert (await TokenPolicyService.status_for_user("alice")).state == "unavailable"
    assert await Storage.get(policy.CAMPAIGN_KEY) is None


async def test_monday_ten_and_late_login_once(clock):
    await display()
    clock[0] = at("2026-09-14T09:59:59+08:00")
    status = await TokenPolicyService.status_for_user("alice")
    assert status.notice is None
    assert status.next_check_at == at("2026-09-14T10:00:00+08:00")
    clock[0] += timedelta(seconds=1)
    assert (await TokenPolicyService.status_for_user("alice")).notice
    clock[0] = at("2026-09-15T18:00:00+08:00")
    await display(request="week-2")
    assert (await TokenPolicyService.status_for_user("alice")).notice is None


async def test_restart_and_weekly_merge_without_missed_week_queue(clock, monkeypatch):
    await display()
    clock[0] = at("2026-09-28T10:00:00+08:00")
    monkeypatch.setenv("_FLOCKS_SERVICE_BOOT_ID", "boot-2")
    await display(request="combined")
    assert (await TokenPolicyService.status_for_user("alice")).notice is None


async def test_previous_week_not_replayed_monday_before_ten(clock):
    await display()
    clock[0] = at("2026-09-21T09:59:00+08:00")
    assert (await TokenPolicyService.status_for_user("alice")).notice is None


async def test_expiry_stops_restart_claims_and_confirmation(clock, monkeypatch):
    status, claim = await reserve()
    clock[0] = status.notice.expires_at
    monkeypatch.setenv("_FLOCKS_SERVICE_BOOT_ID", "boot-2")
    status = await TokenPolicyService.status_for_user("alice")
    assert status.state == "finished"
    assert status.next_check_at is None
    assert (await TokenPolicyService.claim("alice", claim)).notice is None
    assert not (await TokenPolicyService.confirm_display("alice", claim)).confirmed


async def test_concurrent_tabs_only_one_lease_then_persistent_display(clock):
    candidate = (await TokenPolicyService.status_for_user("alice")).notice
    claims = [PolicyClaim(occurrence_id=candidate.occurrence_id, request_id=f"tab-{i}") for i in range(4)]
    results = await asyncio.gather(*(TokenPolicyService.claim("alice", claim) for claim in claims))
    winners = [i for i, result in enumerate(results) if result.notice]
    assert len(winners) == 1
    waiting = await TokenPolicyService.status_for_user("alice")
    assert waiting.waiting_for_display
    assert waiting.next_check_at == clock[0] + timedelta(seconds=policy.LEASE_SECONDS)
    winner = claims[winners[0]]
    assert (await TokenPolicyService.confirm_display("alice", winner)).confirmed
    status = await TokenPolicyService.status_for_user("alice")
    assert not status.waiting_for_display
    assert status.notice is None


async def test_lost_claim_or_refresh_recovers_after_lease_expiry(clock):
    _, abandoned = await reserve(request="old-page")
    record = await Storage.get(TokenPolicyService.state_key("alice"))
    assert "shown_at" not in record
    clock[0] += timedelta(seconds=policy.LEASE_SECONDS)
    _, replacement = await reserve(request="new-page")
    assert not (await TokenPolicyService.confirm_display("alice", abandoned)).confirmed
    assert (await TokenPolicyService.confirm_display("alice", replacement)).confirmed


async def test_lease_retry_does_not_extend_deadline_and_confirmation_is_idempotent(clock):
    initial, claim = await reserve()
    clock[0] += timedelta(seconds=2)
    assert (await TokenPolicyService.claim("alice", claim)).lease_expires_at == initial.lease_expires_at
    assert (await TokenPolicyService.confirm_display("alice", claim)).confirmed
    clock[0] += timedelta(seconds=policy.LEASE_SECONDS)
    assert (await TokenPolicyService.confirm_display("alice", claim)).confirmed
    assert (await TokenPolicyService.status_for_user("alice")).notice is None


async def test_stale_boot_confirmation_and_claim_cannot_consume_new_boot(clock, monkeypatch):
    _, old_claim = await reserve()
    monkeypatch.setenv("_FLOCKS_SERVICE_BOOT_ID", "boot-2")
    assert not (await TokenPolicyService.confirm_display("alice", old_claim)).confirmed
    assert (await TokenPolicyService.claim("alice", old_claim)).notice is None
    await display(request="new-boot")


async def test_stale_week_confirmation_cannot_consume_new_week(clock):
    clock[0] = at("2026-09-14T09:59:55+08:00")
    _, old_claim = await reserve()
    clock[0] = at("2026-09-14T10:00:00+08:00")
    assert not (await TokenPolicyService.confirm_display("alice", old_claim)).confirmed
    await display(request="monday")


def test_calendar_month_end_and_configuration_scope():
    assert policy.month_after(at("2026-01-31T10:00:00+08:00")) == at("2026-02-28T10:00:00+08:00")
    assert policy.month_after(at("2026-12-31T10:00:00+08:00")) == at("2027-01-31T10:00:00+08:00")
    with pytest.raises(ValidationError):
        TokenPolicyNoticeConfig(startsAt="2026-09-09T10:00:00+08:00")


def test_workers_share_supervisor_boot_id(monkeypatch):
    monkeypatch.delenv("_FLOCKS_SERVICE_BOOT_ID", raising=False)
    monkeypatch.setattr(policy, "parent_process", lambda: object())
    monkeypatch.setattr(policy, "current_process", lambda: SimpleNamespace(authkey=b"supervisor-one"))
    first = policy.service_boot_id()
    monkeypatch.delenv("_FLOCKS_SERVICE_BOOT_ID")
    assert policy.service_boot_id() == first
    monkeypatch.delenv("_FLOCKS_SERVICE_BOOT_ID")
    monkeypatch.setattr(policy, "current_process", lambda: SimpleNamespace(authkey=b"supervisor-two"))
    assert policy.service_boot_id() != first


def test_unrelated_config_preserves_campaign_switch():
    merged = Config.merge_config_concat_arrays(
        ConfigInfo(tokenPolicyNotice={"enabled": False}), ConfigInfo(theme="dark")
    )
    assert merged.token_policy_notice.enabled is False


async def test_disabled_campaign_rejects_inflight_delivery(clock, monkeypatch):
    _, claim = await reserve()

    async def disabled_config():
        return ConfigInfo(tokenPolicyNotice={"enabled": False})

    monkeypatch.setattr(Config, "get", disabled_config)
    assert (await TokenPolicyService.status_for_user("alice")).state == "disabled"
    assert not (await TokenPolicyService.confirm_display("alice", claim)).confirmed
    assert (await TokenPolicyService.claim("alice", claim)).notice is None


async def test_routes_auth_and_validation(client, clock):
    response = await client.get("/api/notifications/token-policy")
    assert response.status_code == 200
    body = {"occurrence_id": response.json()["notice"]["occurrence_id"], "request_id": "browser-1"}
    response = await client.post("/api/notifications/token-policy/claim", json=body)
    assert response.status_code == 200
    assert response.json()["lease_expires_at"]
    response = await client.post("/api/notifications/token-policy/displayed", json=body)
    assert response.json() == {"confirmed": True}
    assert (await client.get("/api/notifications/token-policy")).json()["notice"] is None
    for action in ["claim", "displayed"]:
        response = await client.post(
            f"/api/notifications/token-policy/{action}", json={"occurrence_id": "invalid", "request_id": "browser-1"}
        )
        assert response.status_code == 422
    from flocks.auth.service import AuthService

    if not await AuthService.has_users():
        await AuthService.bootstrap_admin(username="admin", password="Password123!")
    for method, path in [
        ("GET", "/api/notifications/token-policy"),
        ("POST", "/api/notifications/token-policy/claim"),
        ("POST", "/api/notifications/token-policy/displayed"),
    ]:
        response = await client.request(method, path, headers={"sec-fetch-mode": "cors"}, json=body)
        assert response.status_code == 401
