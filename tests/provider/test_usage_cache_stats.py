"""Usage records keep whether the provider reported cache usage, and stats tell it apart."""


import pytest

from flocks.provider import usage_service
from flocks.provider.types import PriceConfig
from flocks.provider.usage_service import (
    RecordUsageRequest,
    backfill_usage_records,
    get_usage_records,
    get_usage_stats,
    record_usage,
)
from flocks.storage.storage import Storage


@pytest.fixture
async def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_service, "_auto_backfill_complete", True)
    Storage._initialized = False
    Storage._db_path = None
    await Storage.init(db_path=tmp_path / "usage.db")
    yield tmp_path / "usage.db"
    await Storage.shutdown()
    Storage._initialized = False
    Storage._db_path = None


def _request(**kwargs) -> RecordUsageRequest:
    return RecordUsageRequest(provider_id="threatbook-cn-llm", model_id="m", **kwargs)


async def test_summary_tells_reported_cache_from_unreported(storage):
    await record_usage(_request(input_tokens=10_000, cached_tokens=8_192, cache_reported=True))
    await record_usage(_request(input_tokens=5_000, cached_tokens=0, cache_reported=True))
    await record_usage(_request(input_tokens=4_000, cache_reported=False))
    # Rows written before cache reporting was tracked count in the totals but
    # not in the hit rate: their input may leave the cache out.
    await record_usage(_request(input_tokens=2_000, cached_tokens=100))
    await record_usage(_request(input_tokens=3_000))

    summary = (await get_usage_stats()).summary

    assert summary.total_requests == 5
    assert summary.total_cache_read_tokens == 8_292
    assert summary.cache_reported_requests == 2
    assert summary.cache_reported_read_tokens == 8_192
    assert summary.cache_reported_input_tokens == 15_000
    flags = sorted((r.input_tokens, r.cache_reported) for r in await get_usage_records())
    assert flags == [(2_000, None), (3_000, None), (4_000, False), (5_000, True), (10_000, True)]


async def test_record_keeps_the_response_id(storage):
    await record_usage(_request(input_tokens=10, response_id="chatcmpl-abc", cache_reported=False))

    [record] = await get_usage_records()
    assert record.response_id == "chatcmpl-abc"
    assert record.cache_reported is False


async def test_existing_usage_table_gains_the_new_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "old.db"
    # The table as it was before the two columns, in this test's PostgreSQL schema.
    Storage._initialized = False
    Storage._db_path = None
    async with Storage.transaction(db_path, ensure_init=False) as db:
        await db.execute(
            """CREATE TABLE usage_records (
                id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
                credential_id TEXT, session_id TEXT, message_id TEXT,
                input_tokens BIGINT NOT NULL DEFAULT 0, output_tokens BIGINT NOT NULL DEFAULT 0,
                cached_tokens BIGINT NOT NULL DEFAULT 0, cache_write_tokens BIGINT NOT NULL DEFAULT 0,
                reasoning_tokens BIGINT NOT NULL DEFAULT 0, total_tokens BIGINT NOT NULL DEFAULT 0,
                input_cost DOUBLE PRECISION NOT NULL DEFAULT 0, output_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                total_cost DOUBLE PRECISION NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'USD',
                latency_ms BIGINT, source TEXT NOT NULL DEFAULT 'live', created_at TEXT NOT NULL,
                backfilled_at TEXT)"""
        )
        # An Anthropic-style row from before the upgrade: input_tokens kept
        # only the uncached 100 next to 900 cache reads.
        await db.execute(
            "INSERT INTO usage_records"
            " (id, provider_id, model_id, input_tokens, output_tokens, cached_tokens, total_tokens, created_at)"
            " VALUES ('old-row', 'anthropic', 'm', 100, 20, 900, 120, '2026-10-01T00:00:00+00:00')"
        )
    monkeypatch.setattr(usage_service, "_auto_backfill_complete", True)
    Storage._initialized = False
    Storage._db_path = None
    await Storage.init(db_path=db_path)
    try:
        [record] = await get_usage_records()
        before_new_rows = (await get_usage_stats()).summary
        await record_usage(_request(input_tokens=1_000, cached_tokens=900, cache_reported=True))
        summary = (await get_usage_stats()).summary
    finally:
        await Storage.shutdown()
        Storage._initialized = False
        Storage._db_path = None

    assert record.id == "old-row"
    assert record.cache_reported is None
    assert record.response_id is None
    assert before_new_rows.cache_reported_requests == 0
    # The old row stays out of the hit rate (900 / 100 would read 900%).
    assert (summary.cache_reported_read_tokens, summary.cache_reported_input_tokens) == (900, 1_000)
    assert summary.total_cache_read_tokens == 1_800


async def test_backfill_records_the_whole_prompt_and_the_cache_report(storage, monkeypatch):
    from flocks.session.message import (
        AssistantMessageInfo,
        MessagePath,
        MessageWithParts,
        TokenCache,
        TokenUsage,
    )
    from flocks.session.session import SessionInfo, SessionTime

    session = SessionInfo(
        id="ses_backfill_cache",
        projectID="proj",
        directory="/tmp/backfill-cache",
        title="Backfill",
        time=SessionTime(created=1_000, updated=2_000),
    )
    assistant = AssistantMessageInfo(
        id="msg_backfill_cache",
        sessionID=session.id,
        role="assistant",
        time={"created": 1_100, "completed": 1_200},
        parentID="msg_user",
        modelID="deepseek-v4-flash-0731",
        providerID="threatbook-cn-llm",
        mode="rex",
        agent="rex",
        path=MessagePath(cwd="/tmp", root="/tmp"),
        tokens=TokenUsage(input=1_808, output=16, cache=TokenCache(read=8_192, write=0, reported=True)),
    )

    async def fake_list_all():
        return [session]

    async def fake_list_with_parts(session_id: str, include_archived: bool = False):
        return [MessageWithParts(info=assistant, parts=[])]

    monkeypatch.setattr(usage_service.Session, "list_all", fake_list_all)
    monkeypatch.setattr(usage_service.Message, "list_with_parts", fake_list_with_parts)
    monkeypatch.setattr(
        usage_service, "resolve_usage_pricing",
        lambda *_: PriceConfig(input=2.0, output=12.0, cache_read=0.2, unit=1_000_000),
    )

    result = await backfill_usage_records()
    [record] = await get_usage_records(session_ids=[session.id])

    assert result.inserted_records == 1
    assert (record.input_tokens, record.cached_tokens, record.cache_reported) == (10_000, 8_192, True)
    assert record.total_cost == pytest.approx((1_808 * 2.0 + 8_192 * 0.2 + 16 * 12.0) / 1e6)


async def test_column_added_by_another_process_does_not_break_startup(storage, monkeypatch):
    """Two processes upgrading one database can both see a column missing;
    the one that adds it second must not fail on the duplicate."""
    real_connect = Storage.connect

    class StaleColumns:
        def __init__(self, db):
            self._db = db

        def __getattr__(self, name):
            return getattr(self._db, name)

        def execute(self, sql, *args, **kwargs):
            if sql.startswith("PRAGMA table_info(usage_records)"):
                return self._db.execute("SELECT 1 WHERE 0")
            return self._db.execute(sql, *args, **kwargs)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def stale_connect(*args, **kwargs):
        async with real_connect(*args, **kwargs) as db:
            yield StaleColumns(db)

    monkeypatch.setattr(Storage, "connect", stale_connect)
    await Storage._create_model_management_tables()
    monkeypatch.setattr(Storage, "connect", real_connect)

    await record_usage(_request(input_tokens=10, cache_reported=True, response_id="chatcmpl-x"))
    [record] = await get_usage_records()
    assert (record.cache_reported, record.response_id) == (True, "chatcmpl-x")


async def test_summary_route_filters_records_by_real_time(storage):
    """Bounds written with a space, an offset or as a bare end date select by time, not text."""
    from datetime import datetime, timezone

    from flocks.server.routes import usage as usage_routes

    for hour, day, n in ((7, 20, 1), (9, 20, 2), (1, 21, 4)):
        await record_usage(_request(
            input_tokens=n,
            created_at=datetime(2026, 10, day, hour, 0, 0, 123000, tzinfo=timezone.utc),
        ))

    async def total(**bounds) -> int:
        stats = await usage_routes.api_get_usage_stats(
            start_date=bounds.get("start"), end_date=bounds.get("end"), provider_id=None, model_id=None,
        )
        return stats.summary.total_input_tokens

    # 08:00 written with a space used to compare as text and take in 07:00 too.
    assert await total(start="2026-10-20 08:00") == 2 + 4
    # 08:00+02:00 is 06:00 UTC: 07:00 belongs in, and text comparison left it out.
    assert await total(start="2026-10-20T08:00:00+02:00") == 1 + 2 + 4
    # A bare end date covers the day; as text it ended before the day began.
    assert await total(end="2026-10-20") == 1 + 2
    assert await total(start="2026-10-20", end="2026-10-20") == 1 + 2
    assert await total(start="2026-10-21") == 4


async def test_summary_filters_rows_stored_with_an_offset_by_their_instant(storage):
    """Rows posted with another offset (or none) before writes were kept in UTC filter by time."""
    from flocks.server.routes import usage as usage_routes

    stored = {
        # 09:00+02:00 is 07:00 UTC; as text it sorted after an 08:00 UTC bound.
        "2026-10-20T09:00:00+02:00": 1,
        # 02:00-08:00 is 10:00 UTC; as text it sorted before one.
        "2026-10-20T02:00:00-08:00": 2,
        # No offset: UTC.
        "2026-10-20T08:30:00": 4,
        # 08:00 UTC both, with offsets past the 14 hours of real zones (the
        # request model takes up to 23:59) and past PostgreSQL's own 15:59.
        "2026-10-19T17:00:00-15:00": 8,
        "2026-10-21T04:00:00+20:00": 16,
    }
    for text, n in stored.items():
        record = await record_usage(_request(input_tokens=n))
        async with Storage.transaction(Storage._db_path) as db:
            await db.execute("UPDATE usage_records SET created_at = ? WHERE id = ?", (text, record.id))

    async def total(**bounds) -> int:
        stats = await usage_routes.api_get_usage_stats(
            start_date=bounds.get("start"), end_date=bounds.get("end"), provider_id=None, model_id=None,
        )
        return stats.summary.total_input_tokens

    assert await total(start="2026-10-20T08:00:00Z") == 2 + 4 + 8 + 16
    assert await total(end="2026-10-20T08:00:00Z") == 1 + 8 + 16
    assert await total(start="2026-10-20T08:00:00Z", end="2026-10-20T08:00:00Z") == 8 + 16
    assert await total(start="2026-10-20T07:00:00Z", end="2026-10-20T07:00:00Z") == 1
    assert await total(start="2026-10-20T09:00:00Z") == 2
    # Bounds at the ends of the calendar, where the widened text range does not exist.
    assert await total(start="0001-01-01", end="9999-12-31") == 1 + 2 + 4 + 8 + 16
    # Days are UTC days too, like the bounds: the -15:00 and +20:00 rows are
    # written on 10-19 and 10-21.
    stats = await usage_routes.api_get_usage_stats(
        start_date=None, end_date=None, provider_id=None, model_id=None,
    )
    assert [(day.date, day.total_tokens) for day in stats.daily] == [("2026-10-20", 1 + 2 + 4 + 8 + 16)]


async def test_recorded_time_is_stored_in_utc(storage):
    from datetime import datetime, timedelta, timezone

    record = await record_usage(_request(
        input_tokens=1,
        created_at=datetime(2026, 10, 20, 9, 0, tzinfo=timezone(timedelta(hours=2))),
    ))

    assert record.created_at == datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    async with Storage.transaction(Storage._db_path) as db:
        row = await db.fetch_one("SELECT created_at FROM usage_records WHERE id = ?", (record.id,))
    assert row[0] == "2026-10-20T07:00:00+00:00"
