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
