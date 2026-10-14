"""
Tests for the versioned knowledge store.

The PostgreSQL-backed cases need a real database with pgvector, because what
they check -- transactional publishing, access filtering inside the query, and
vector ordering -- is behaviour of the database, not of the Python around it. A
fake would only assert that the mock was called. They skip when
FLOCKS_TEST_PG_DSN is unset; the pure-logic cases always run.
"""

import math
import os
import uuid

import pytest

from flocks.knowledge import (
    AccessScope,
    Chunk,
    Document,
    EmbeddingMismatch,
    EmbeddingSpec,
    KnowledgeError,
    VersionState,
)
from flocks.knowledge.postgres import PostgresKnowledgeStore, _vector_literal

DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not DSN, reason="FLOCKS_TEST_PG_DSN is not set")

SPEC = EmbeddingSpec(model="test-embed", dimensions=4, provider="local")


def _schema_dsn():
    """The test's own schema (tests/conftest.py), not the bare test database.

    ANN results depend on every row in the table: rows other runs left in a
    shared schema crowd out this test's own.
    """
    return os.environ["FLOCKS_DB_DSN"]


def unit(*values):
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def test_embedding_specs_compare_on_model_and_dimensions():
    """Vectors are only comparable when both match."""
    base = EmbeddingSpec(model="m", dimensions=8)
    assert base.matches(EmbeddingSpec(model="m", dimensions=8, provider="other"))
    assert not base.matches(EmbeddingSpec(model="m", dimensions=16))
    assert not base.matches(EmbeddingSpec(model="n", dimensions=8))


def test_vector_literal_round_trips_exactly():
    """A float must survive the text form unchanged.

    str() would shorten some values, shifting a stored vector by the last digit
    and with it every similarity score computed against it.
    """
    values = [0.1, 1 / 3, -2.5e-8, 1e300]
    literal = _vector_literal(values)
    parsed = [float(part) for part in literal.strip("[]").split(",")]
    assert parsed == values


def test_unrestricted_scope_is_recognised():
    assert AccessScope().is_unrestricted
    assert not AccessScope(owner_id="u1").is_unrestricted
    assert not AccessScope(groups=["g"]).is_unrestricted


# ---------------------------------------------------------------------------
# Against a real database
# ---------------------------------------------------------------------------

@pytest.fixture
async def store():
    s = PostgresKnowledgeStore(_schema_dsn())
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def base_id():
    return f"kb-{uuid.uuid4().hex[:8]}"


async def _seed(store, base_id, version, docs):
    await store.begin_version(base_id, version, SPEC)
    for document, chunks in docs:
        await store.add_document(base_id, version, document, chunks)
    return await store.publish_version(base_id, version)


@requires_pg
async def test_building_version_is_not_retrievable(store, base_id):
    """A half-ingested version must never answer a query."""
    await store.begin_version(base_id, "v1", SPEC)
    await store.add_document(
        base_id, "v1",
        Document(document_id="d1", title="One"),
        [Chunk(document_id="d1", ordinal=0, text="body", embedding=unit(1, 0, 0, 0))],
    )

    assert await store.search(base_id, unit(1, 0, 0, 0), SPEC, AccessScope()) == []
    assert await store.current_version(base_id) is None


@requires_pg
async def test_publishing_supersedes_the_previous_version(store, base_id):
    """Exactly one version serves at a time, and the switch is atomic."""
    await _seed(store, base_id, "v1", [
        (Document(document_id="d1", title="One"),
         [Chunk(document_id="d1", ordinal=0, text="first", embedding=unit(1, 0, 0, 0))]),
    ])
    await _seed(store, base_id, "v2", [
        (Document(document_id="d1", title="One"),
         [Chunk(document_id="d1", ordinal=0, text="second", embedding=unit(1, 0, 0, 0))]),
    ])

    current = await store.current_version(base_id)
    assert current.version == "v2"
    hits = await store.search(base_id, unit(1, 0, 0, 0), SPEC, AccessScope())
    assert [h.text for h in hits] == ["second"]


@requires_pg
async def test_access_scope_is_applied_inside_the_query(store, base_id):
    """A caller only ever sees rows it is allowed to see.

    Filtering after the search would spend the ranking on rows the caller
    cannot read and then return fewer results than asked for.
    """
    await _seed(store, base_id, "v1", [
        (Document(document_id="open", title="Open"),
         [Chunk(document_id="open", ordinal=0, text="open", embedding=unit(1, 1, 0, 0))]),
        (Document(document_id="proj", title="Project", project_id="p1"),
         [Chunk(document_id="proj", ordinal=0, text="project", embedding=unit(1, 1, 0, 0))]),
        (Document(document_id="grp", title="Group", groups=["sec"]),
         [Chunk(document_id="grp", ordinal=0, text="group", embedding=unit(1, 1, 0, 0))]),
    ])

    query = unit(1, 1, 0, 0)
    outsider = await store.search(base_id, query, SPEC, AccessScope(owner_id="u9", project_id="pz"), limit=10)
    member = await store.search(base_id, query, SPEC, AccessScope(owner_id="u1", project_id="p1"), limit=10)
    grouped = await store.search(base_id, query, SPEC, AccessScope(owner_id="u2", groups=["sec"]), limit=10)

    assert {h.document_id for h in outsider} == {"open"}
    assert {h.document_id for h in member} == {"open", "proj"}
    assert {h.document_id for h in grouped} == {"open", "grp"}


@requires_pg
async def test_revocation_takes_effect_in_the_query(store, base_id):
    """Withdrawn access stops serving now, not at the next index rebuild."""
    await _seed(store, base_id, "v1", [
        (Document(document_id="keep", title="Keep"),
         [Chunk(document_id="keep", ordinal=0, text="keep", embedding=unit(1, 0, 0, 0))]),
        (Document(document_id="drop", title="Drop"),
         [Chunk(document_id="drop", ordinal=0, text="drop", embedding=unit(1, 0, 0, 0))]),
    ])

    assert await store.revoke_document(base_id, "drop") >= 1

    hits = await store.search(base_id, unit(1, 0, 0, 0), SPEC, AccessScope(), limit=10)
    assert {h.document_id for h in hits} == {"keep"}


@requires_pg
async def test_embedding_mismatch_is_refused(store, base_id):
    """A different model produces a number, not a similarity."""
    await _seed(store, base_id, "v1", [
        (Document(document_id="d1", title="One"),
         [Chunk(document_id="d1", ordinal=0, text="body", embedding=unit(1, 0, 0, 0))]),
    ])

    with pytest.raises(EmbeddingMismatch):
        await store.search(base_id, unit(1, 0, 0, 0), EmbeddingSpec(model="other", dimensions=4), AccessScope())
    with pytest.raises(EmbeddingMismatch):
        await store.search(base_id, [0.1, 0.2], SPEC, AccessScope())


@requires_pg
async def test_abandoned_build_never_serves(store, base_id):
    """A failed ingest leaves the previous version serving, not a partial one."""
    await _seed(store, base_id, "v1", [
        (Document(document_id="good", title="Good"),
         [Chunk(document_id="good", ordinal=0, text="good", embedding=unit(1, 0, 0, 0))]),
    ])
    await store.begin_version(base_id, "v2", SPEC)
    await store.add_document(
        base_id, "v2",
        Document(document_id="partial", title="Partial"),
        [Chunk(document_id="partial", ordinal=0, text="partial", embedding=unit(1, 0, 0, 0))],
    )
    await store.abandon_version(base_id, "v2", "ingest failed")

    assert (await store.current_version(base_id)).version == "v1"
    hits = await store.search(base_id, unit(1, 0, 0, 0), SPEC, AccessScope(), limit=10)
    assert {h.document_id for h in hits} == {"good"}


@requires_pg
async def test_published_version_is_immutable(store, base_id):
    """Content cannot appear in a version after it went live."""
    await _seed(store, base_id, "v1", [
        (Document(document_id="d1", title="One"),
         [Chunk(document_id="d1", ordinal=0, text="body", embedding=unit(1, 0, 0, 0))]),
    ])

    with pytest.raises(KnowledgeError):
        await store.add_document(
            base_id, "v1",
            Document(document_id="late", title="Late"),
            [Chunk(document_id="late", ordinal=0, text="late", embedding=unit(0, 1, 0, 0))],
        )


@requires_pg
async def test_search_ranks_by_similarity(store, base_id):
    """Ordering comes from the vectors, not from insertion order."""
    await _seed(store, base_id, "v1", [
        (Document(document_id=name, title=name),
         [Chunk(document_id=name, ordinal=0, text=name, embedding=vector)])
        for name, vector in [
            ("first", unit(1, 0, 0, 0)),
            ("second", unit(0, 1, 0, 0)),
            ("third", unit(0, 0, 1, 0)),
        ]
    ])

    hits = await store.search(base_id, unit(0, 0, 1, 0), SPEC, AccessScope(), limit=3)
    assert hits[0].document_id == "third"
    assert hits[0].score > hits[-1].score


@requires_pg
async def test_an_abandoned_version_is_not_retrievable_even_when_named(store, base_id):
    """Naming a failed build explicitly must not resurrect its partial content.

    Regression: ``search(version=...)`` only checked the embedding spec, so an
    abandoned build's chunks were still served to a caller that asked for it.
    """
    await _seed(store, base_id, "v1", [
        (Document(document_id="good", title="Good"),
         [Chunk(document_id="good", ordinal=0, text="good", embedding=unit(1, 0, 0, 0))]),
    ])
    await store.begin_version(base_id, "v2", SPEC)
    await store.add_document(
        base_id, "v2",
        Document(document_id="partial", title="Partial"),
        [Chunk(document_id="partial", ordinal=0, text="partial", embedding=unit(1, 0, 0, 0))],
    )
    await store.abandon_version(base_id, "v2", "ingest failed")

    hits = await store.search(base_id, unit(1, 0, 0, 0), SPEC, AccessScope(), limit=10, version="v2")
    assert hits == []


@requires_pg
async def test_search_ef_survives_pool_connection_reset(store, base_id, monkeypatch):
    """``set_search_ef`` must reach the retrieval query, not a connection the pool resets.

    Regression: the breadth was issued as a plain ``SET`` on one checkout; asyncpg
    resets session state on release, so every later ``search()`` ran with the
    default ``hnsw.ef_search`` and the setting silently did nothing.
    """
    await _seed(store, base_id, "v1", [
        (Document(document_id="good", title="Good"),
         [Chunk(document_id="good", ordinal=0, text="good", embedding=unit(1, 0, 0, 0))]),
    ])
    await store.set_search_ef(200)

    real_pool = await store._ensure_pool()
    executed: list[str] = []

    class _ConnProxy:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def execute(self, query, *args, **kwargs):
            executed.append(" ".join(str(query).split()))
            return await self._conn.execute(query, *args, **kwargs)

    class _Acquire:
        def __init__(self, ctx):
            self._ctx = ctx

        async def __aenter__(self):
            return _ConnProxy(await self._ctx.__aenter__())

        async def __aexit__(self, *exc):
            return await self._ctx.__aexit__(*exc)

    class _PoolProxy:
        def acquire(self):
            return _Acquire(real_pool.acquire())

    async def _proxied_pool():
        return _PoolProxy()

    monkeypatch.setattr(store, "_ensure_pool", _proxied_pool)
    hits = await store.search(base_id, unit(1, 0, 0, 0), SPEC, AccessScope(), limit=1)

    assert [h.document_id for h in hits] == ["good"]
    assert any(q.startswith("SET LOCAL hnsw.ef_search = 200") for q in executed), executed

    import asyncpg

    other = await asyncpg.connect(_schema_dsn())
    try:
        await other.fetchval("SELECT '[1]'::vector")
        assert await other.fetchval("SHOW hnsw.ef_search") == "40"
    finally:
        await other.close()


@requires_pg
async def test_search_reaches_the_partial_hnsw_index_after_a_bulk_build(store, base_id):
    """Building the ANN index must change the plan of the query the store runs.

    Regression (E2E-0062): a version was bulk-ingested and indexed, yet every
    retrieval still sorted the whole version. The planner had never analysed
    the table after the load, estimated the version at one row, and on that
    estimate a sort is always cheaper than walking the HNSW index. Publishing
    and index creation now refresh the statistics, so a version large enough
    to benefit is served through the index. The plan is read with the
    planner's default settings, on exactly the statement ``search`` runs.
    """
    import random

    random.seed(7)
    await store.begin_version(base_id, "v1", SPEC)
    for d in range(24):
        doc = Document(document_id=f"d{d}", title=f"Doc {d}", owner_id="u1" if d % 2 else "")
        chunks = [
            Chunk(document_id=doc.document_id, ordinal=i, text=f"{d}-{i}",
                  embedding=unit(*[random.gauss(0, 1) for _ in range(4)]))
            for i in range(250)
        ]
        await store.add_document(base_id, "v1", doc, chunks)
    await store.publish_version(base_id, "v1")
    index_name = await store.create_ann_index(dimensions=4, concurrently=False)
    await store.set_search_ef(64)

    pool = await store._ensure_pool()
    query = unit(1, 0, 0, 0)
    for scope in (AccessScope(), AccessScope(owner_id="u1", project_id="p1", groups=["g"])):
        sql, params = store._search_statement(base_id, "v1", 4, query, scope, 5)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL hnsw.ef_search = 64")
                plan = "\n".join(row[0] for row in await conn.fetch("EXPLAIN " + sql, *params))
                # The breadth set for this transaction is what the query runs with.
                assert await conn.fetchval("SHOW hnsw.ef_search") == "64"
        assert f"Index Scan using {index_name}" in plan, plan
        assert "Sort" not in plan, plan

    hits = await store.search(base_id, query, SPEC, AccessScope(owner_id="u1"), limit=5)
    assert len(hits) == 5
    assert all(h.document_id in {f"d{d}" for d in range(24)} for h in hits)


@requires_pg
async def test_concurrent_publishes_of_one_base_are_serialised(base_id):
    """Two instances publishing different versions of one base both succeed.

    Regression: ``publish_version`` locked only its own version row, so two
    concurrent publishes each superseded nothing and the second one failed on
    the one-published unique index with a raw driver error, leaving that
    version stuck in ``building``. Publishes of a base now queue on a
    transaction-scoped advisory lock: the later one sees the earlier commit
    and supersedes it, and exactly one version ends up published.
    """
    import asyncio

    first, second = PostgresKnowledgeStore(_schema_dsn()), PostgresKnowledgeStore(_schema_dsn())
    await first.initialize()
    await second.initialize()
    try:
        for store, version in ((first, "v1"), (second, "v2")):
            await store.begin_version(base_id, version, SPEC)
            await store.add_document(
                base_id, version,
                Document(document_id="d1", title=version),
                [Chunk(document_id="d1", ordinal=0, text=version, embedding=unit(1, 0, 0, 0))],
            )

        results = await asyncio.gather(
            first.publish_version(base_id, "v1"),
            second.publish_version(base_id, "v2"),
            return_exceptions=True,
        )
        assert not any(isinstance(r, Exception) for r in results), results

        pool = await first._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT version, state FROM knowledge_versions WHERE base_id=$1", base_id
            )
        states = sorted(row["state"] for row in rows)
        assert states == ["published", "superseded"], states
    finally:
        await first.close()
        await second.close()
