"""Time-limited reminders with recoverable, per-user display reservations."""

from __future__ import annotations

import calendar
import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from multiprocessing import current_process, parent_process
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

from flocks.config.config import Config
from flocks.storage.storage import Storage

CAMPAIGN_ID = "token-policy-change"
CAMPAIGN_KEY = f"notifications/token-policy-campaign/{CAMPAIGN_ID}"
LEASE_SECONDS = 15
BEIJING = timezone(timedelta(hours=8))
T = TypeVar("T")


def service_boot_id() -> str:
    existing = os.environ.get("_FLOCKS_SERVICE_BOOT_ID")
    if existing:
        return existing
    # `flocks serve` sets the id before starting workers. Direct uvicorn
    # workers/reload children share their supervisor's multiprocessing authkey.
    boot = hashlib.sha256(bytes(current_process().authkey)).hexdigest() if parent_process() else uuid4().hex
    return os.environ.setdefault("_FLOCKS_SERVICE_BOOT_ID", boot)


def utc_now() -> datetime:
    return datetime.now(UTC)


def month_after(start: datetime) -> datetime:
    start = start.astimezone(BEIJING)
    year, month = start.year + (start.month == 12), start.month % 12 + 1
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


class Campaign(BaseModel):
    starts_at: datetime

    @property
    def expires_at(self) -> datetime:
        return month_after(self.starts_at)


class PolicyNotice(BaseModel):
    id: str = CAMPAIGN_ID
    occurrence_id: str
    expires_at: datetime


class PolicyStatus(BaseModel):
    state: Literal["active", "finished", "disabled", "unavailable"]
    server_now: datetime
    notice: PolicyNotice | None = None
    next_check_at: datetime | None = None
    waiting_for_display: bool = False
    lease_expires_at: datetime | None = None


class PolicyClaim(BaseModel):
    occurrence_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{1,80}$")


class PolicyConfirmation(BaseModel):
    confirmed: bool


def week_slot(now: datetime, start: datetime) -> tuple[str | None, datetime]:
    local = now.astimezone(BEIJING)
    monday = (local - timedelta(days=local.weekday())).replace(hour=10, minute=0, second=0, microsecond=0)
    # Never replay the previous week's slot on Monday before 10.
    slot = monday.isoformat() if start <= monday <= now else None
    return slot, monday if now < monday else monday + timedelta(days=7)


async def _update_record(key: str, change: Callable[[dict], T]) -> T:
    """Serialize campaign initialization and delivery transitions across workers."""
    await Storage.init(Storage.get_db_path())
    async with Storage.connect() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT value FROM storage WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
            record = json.loads(row[0]) if row else {}
            before = json.dumps(record)
            result = change(record)
            after = json.dumps(record)
            if after != before:
                now = utc_now().isoformat()
                await db.execute(
                    "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, 'json', ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, after, now, now),
                )
            await db.commit()
            return result
        except BaseException:
            await db.rollback()
            raise


class TokenPolicyService:
    @staticmethod
    async def initialize_campaign() -> Campaign:
        def initialize(record: dict) -> Campaign:
            record.setdefault("starts_at", utc_now().isoformat())
            return Campaign.model_validate(record)

        return await _update_record(CAMPAIGN_KEY, initialize)

    @staticmethod
    async def load_campaign() -> Campaign | None:
        """Read only: polling must never create or restart the campaign clock."""
        config = (await Config.get()).token_policy_notice
        if config is not None and not config.enabled:
            return None
        return await Storage.get(CAMPAIGN_KEY, Campaign)

    @staticmethod
    def state_key(user_id: str) -> str:
        return f"notifications/token-policy/{CAMPAIGN_ID}/{user_id}"

    @staticmethod
    def evaluate(campaign: Campaign, now: datetime, boot: str, record: dict) -> PolicyStatus:
        result = PolicyStatus(state="active", server_now=now)
        if now >= campaign.expires_at:
            result.state = "finished"
            return result
        slot, next_monday = week_slot(now, campaign.starts_at)
        result.next_check_at = min(next_monday, campaign.expires_at)
        if record.get("shown_boot") == boot and (slot is None or record.get("shown_week") == slot):
            return result
        occurrence = hashlib.sha256(f"{CAMPAIGN_ID}:{boot}:{slot}".encode()).hexdigest()
        result.notice = PolicyNotice(occurrence_id=occurrence, expires_at=campaign.expires_at)
        return result

    @staticmethod
    def active_lease(record: dict, status: PolicyStatus) -> dict | None:
        lease = record.get("lease")
        if (
            lease
            and status.notice
            and lease["occurrence_id"] == status.notice.occurrence_id
            and datetime.fromisoformat(lease["expires_at"]) > status.server_now
        ):
            return lease
        return None

    @staticmethod
    def defer_to_lease(status: PolicyStatus, lease: dict) -> PolicyStatus:
        status.notice = None
        status.waiting_for_display = True
        status.next_check_at = min(status.next_check_at, datetime.fromisoformat(lease["expires_at"]))
        return status

    @classmethod
    async def status_for_user(cls, user_id: str) -> PolicyStatus:
        campaign = await cls.load_campaign()
        if campaign is None:
            config = (await Config.get()).token_policy_notice
            state = "disabled" if config is not None and not config.enabled else "unavailable"
            return PolicyStatus(state=state, server_now=utc_now())
        record = await Storage.get(cls.state_key(user_id)) or {}
        status = cls.evaluate(campaign, utc_now(), service_boot_id(), record)
        lease = cls.active_lease(record, status)
        return cls.defer_to_lease(status, lease) if lease else status

    @classmethod
    async def claim(cls, user_id: str, claim: PolicyClaim) -> PolicyStatus:
        campaign = await cls.load_campaign()
        if campaign is None:
            return await cls.status_for_user(user_id)

        def reserve(record: dict) -> PolicyStatus:
            now, boot = utc_now(), service_boot_id()
            status = cls.evaluate(campaign, now, boot, record)
            if not status.notice or status.notice.occurrence_id != claim.occurrence_id:
                status.notice = None
                return status
            lease = cls.active_lease(record, status)
            if lease and lease["request_id"] != claim.request_id:
                return cls.defer_to_lease(status, lease)
            if lease is None:
                slot, _ = week_slot(now, campaign.starts_at)
                lease = {
                    **claim.model_dump(),
                    "boot_id": boot,
                    "week_slot": slot,
                    "expires_at": min(now + timedelta(seconds=LEASE_SECONDS), campaign.expires_at).isoformat(),
                }
                record["lease"] = lease
            # An HTTP retry keeps the original deadline; it cannot monopolize delivery.
            status.lease_expires_at = datetime.fromisoformat(lease["expires_at"])
            return status

        return await _update_record(cls.state_key(user_id), reserve)

    @classmethod
    async def confirm_display(cls, user_id: str, claim: PolicyClaim) -> PolicyConfirmation:
        campaign = await cls.load_campaign()
        if campaign is None:
            return PolicyConfirmation(confirmed=False)

        def confirm(record: dict) -> PolicyConfirmation:
            now, boot = utc_now(), service_boot_id()
            if now >= campaign.expires_at:
                return PolicyConfirmation(confirmed=False)
            # A lost confirmation response can be retried after lease expiry.
            if (
                record.get("shown_request") == claim.request_id
                and record.get("shown_occurrence") == claim.occurrence_id
                and record.get("shown_boot") == boot
            ):
                return PolicyConfirmation(confirmed=True)
            status = cls.evaluate(campaign, now, boot, record)
            lease = cls.active_lease(record, status)
            if not lease or lease["request_id"] != claim.request_id or lease["occurrence_id"] != claim.occurrence_id:
                return PolicyConfirmation(confirmed=False)
            record.update(
                shown_boot=boot,
                shown_week=lease["week_slot"] or record.get("shown_week"),
                shown_request=claim.request_id,
                shown_occurrence=claim.occurrence_id,
                shown_at=now.isoformat(),
            )
            record.pop("lease", None)
            return PolicyConfirmation(confirmed=True)

        return await _update_record(cls.state_key(user_id), confirm)
