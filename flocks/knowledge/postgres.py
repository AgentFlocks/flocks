"""
PostgreSQL + pgvector implementation of the knowledge store.

This is the backend for a deployment where two application instances share one
database. Everything a retrieval needs -- the vectors, the access scope, and
which version is live -- is in SQL, so both instances see the same knowledge
without shipping files between them.

Access filtering happens inside the query rather than after it. Filtering
afterwards would spend the similarity search on rows the caller cannot read and
then return fewer results than asked for; filtering inside means the top N are
the top N *of what this caller may see*.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from flocks.knowledge.models import (
    AccessScope,
    Chunk,
    Document,
    EmbeddingMismatch,
    EmbeddingSpec,
    KnowledgeError,
    KnowledgeVersion,
    SearchHit,
    VersionState,
)
from flocks.utils.log import Log

log = Log.create(service="knowledge.postgres")

#: Managed ANN indexes carry this prefix, so operator-built ones stay separate.
ANN_INDEX_PREFIX = "knowledge_chunks_hnsw_"

SCHEMA_STATEMENTS: List[str] = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS knowledge_versions (
        base_id          TEXT        NOT NULL,
        version          TEXT        NOT NULL,
        state            TEXT        NOT NULL,
        embedding_model  TEXT        NOT NULL,
        embedding_dims   INTEGER     NOT NULL,
        embedding_provider TEXT      NOT NULL DEFAULT '',
        document_count   INTEGER     NOT NULL DEFAULT 0,
        chunk_count      INTEGER     NOT NULL DEFAULT 0,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        published_at     TIMESTAMPTZ,
        failure_reason   TEXT,
        PRIMARY KEY (base_id, version)
    )
    """,
    # At most one published version per base, enforced by the database rather
    # than by application discipline: two instances publish concurrently, and
    # "check then insert" races between them.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS knowledge_versions_one_published
        ON knowledge_versions (base_id) WHERE state = 'published'
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_documents (
        base_id      TEXT        NOT NULL,
        version      TEXT        NOT NULL,
        document_id  TEXT        NOT NULL,
        title        TEXT        NOT NULL DEFAULT '',
        source_uri   TEXT        NOT NULL DEFAULT '',
        media_type   TEXT        NOT NULL DEFAULT 'text/markdown',
        owner_id     TEXT        NOT NULL DEFAULT '',
        project_id   TEXT        NOT NULL DEFAULT '',
        groups       TEXT[]      NOT NULL DEFAULT '{}',
        metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb,
        revoked_at   TIMESTAMPTZ,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (base_id, version, document_id),
        FOREIGN KEY (base_id, version)
            REFERENCES knowledge_versions (base_id, version) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_chunks (
        chunk_id     TEXT        PRIMARY KEY,
        base_id      TEXT        NOT NULL,
        version      TEXT        NOT NULL,
        document_id  TEXT        NOT NULL,
        ordinal      INTEGER     NOT NULL,
        content      TEXT        NOT NULL,
        embedding    vector      NOT NULL,
        -- The vector's width, stored rather than derived. A partial ANN index
        -- needs a predicate the planner can filter rows on *before* evaluating
        -- the index expression; a function call in the predicate does not give
        -- that guarantee, and the build fails on the first row of another
        -- width.
        dims         INTEGER     NOT NULL,
        metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (base_id, version, document_id)
            REFERENCES knowledge_documents (base_id, version, document_id) ON DELETE CASCADE
    )
    """,
    "ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS dims INTEGER",
    # Backfill for rows written before the column existed, then make it required.
    "UPDATE knowledge_chunks SET dims = vector_dims(embedding) WHERE dims IS NULL",
    "CREATE INDEX IF NOT EXISTS knowledge_chunks_version ON knowledge_chunks (base_id, version)",
    "CREATE INDEX IF NOT EXISTS knowledge_chunks_dims ON knowledge_chunks (dims)",
    "CREATE INDEX IF NOT EXISTS knowledge_documents_scope ON knowledge_documents (base_id, version, owner_id, project_id)",
    # Revocation is a query-time exclusion, so it needs to be cheap to test.
    "CREATE INDEX IF NOT EXISTS knowledge_documents_revoked ON knowledge_documents (base_id, document_id) WHERE revoked_at IS NOT NULL",
]


def _vector_literal(values: Sequence[float]) -> str:
    """pgvector accepts its own text form: '[1,2,3]'.

    repr() rather than str(): it round-trips a float exactly, so a vector
    written and read back compares identically instead of drifting in the last
    digit and shifting similarity scores.
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


class PostgresKnowledgeStore:
    """Knowledge storage backed by PostgreSQL and pgvector."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 8) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None
        self._ef_search: Optional[int] = None

    async def _ensure_pool(self):
        from flocks.db.connect import pool_for
        self._pool = await pool_for(self._dsn, min_size=self._min_size, max_size=self._max_size)
        return self._pool

    async def initialize(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "flocks.knowledge.schema")
                await conn.execute(";\n".join(SCHEMA_STATEMENTS))
        log.info("knowledge.postgres.ready", {})

    async def close(self) -> None:
        self._pool = None

    # -- version lifecycle --------------------------------------------------

    async def begin_version(self, base_id: str, version: str, embedding: EmbeddingSpec) -> KnowledgeVersion:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO knowledge_versions
                    (base_id, version, state, embedding_model, embedding_dims, embedding_provider)
                VALUES ($1, $2, 'building', $3, $4, $5)
                ON CONFLICT (base_id, version) DO NOTHING
                RETURNING created_at
                """,
                base_id, version, embedding.model, embedding.dimensions, embedding.provider,
            )
            if row is None:
                raise KnowledgeError(f"版本已存在: {base_id}@{version}")
        return KnowledgeVersion(
            base_id=base_id, version=version, state=VersionState.BUILDING,
            embedding=embedding, created_at=row["created_at"],
        )

    async def add_document(self, base_id: str, version: str, document: Document, chunks: Sequence[Chunk]) -> None:
        pool = await self._ensure_pool()
        spec = await self._version_embedding(base_id, version)

        for chunk in chunks:
            if chunk.embedding is None:
                raise KnowledgeError(f"分块缺少向量: {document.document_id}#{chunk.ordinal}")
            if len(chunk.embedding) != spec.dimensions:
                raise EmbeddingMismatch(
                    f"向量维度不符: 版本为 {spec.dimensions}, 分块为 {len(chunk.embedding)}"
                )

        async with pool.acquire() as conn:
            # One transaction per document: a document is either fully indexed
            # or not present, so a retry cannot leave half its chunks behind.
            async with conn.transaction():
                state = await conn.fetchval(
                    "SELECT state FROM knowledge_versions WHERE base_id=$1 AND version=$2",
                    base_id, version,
                )
                if state != VersionState.BUILDING.value:
                    raise KnowledgeError(f"版本不可写入 (state={state}): {base_id}@{version}")

                await conn.execute(
                    """
                    INSERT INTO knowledge_documents
                        (base_id, version, document_id, title, source_uri, media_type,
                         owner_id, project_id, groups, metadata)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                    ON CONFLICT (base_id, version, document_id) DO UPDATE SET
                        title=EXCLUDED.title, source_uri=EXCLUDED.source_uri,
                        media_type=EXCLUDED.media_type, owner_id=EXCLUDED.owner_id,
                        project_id=EXCLUDED.project_id, groups=EXCLUDED.groups,
                        metadata=EXCLUDED.metadata
                    """,
                    base_id, version, document.document_id, document.title,
                    document.source_uri, document.media_type, document.owner_id,
                    document.project_id, list(document.groups), json.dumps(document.metadata),
                )
                await conn.execute(
                    "DELETE FROM knowledge_chunks WHERE base_id=$1 AND version=$2 AND document_id=$3",
                    base_id, version, document.document_id,
                )
                # One round trip for the whole document rather than one per
                # chunk: a long document chunks into hundreds of rows, and at
                # one statement each the latency dominates the ingest.
                rows = [
                    (
                        chunk.chunk_id
                        or f"{base_id}:{version}:{document.document_id}:{chunk.ordinal}",
                        base_id,
                        version,
                        document.document_id,
                        chunk.ordinal,
                        chunk.text,
                        _vector_literal(chunk.embedding),
                        len(chunk.embedding),
                        json.dumps(chunk.metadata),
                    )
                    for chunk in chunks
                ]
                if rows:
                    await conn.executemany(
                        """
                        INSERT INTO knowledge_chunks
                            (chunk_id, base_id, version, document_id, ordinal, content,
                             embedding, dims, metadata)
                        VALUES ($1,$2,$3,$4,$5,$6,$7::vector,$8,$9::jsonb)
                        """,
                        rows,
                    )
                await conn.execute(
                    """
                    UPDATE knowledge_versions
                    SET document_count = (
                            SELECT count(*) FROM knowledge_documents
                            WHERE base_id=$1 AND version=$2),
                        chunk_count = (
                            SELECT count(*) FROM knowledge_chunks
                            WHERE base_id=$1 AND version=$2)
                    WHERE base_id=$1 AND version=$2
                    """,
                    base_id, version,
                )

    async def publish_version(self, base_id: str, version: str) -> KnowledgeVersion:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            # Supersede and publish in one transaction, so no window exists in
            # which a base has two published versions or none.
            async with conn.transaction():
                # One publish per base at a time. Locking only the target row
                # does not order two instances publishing *different* versions:
                # each supersedes nothing (the other's publish is uncommitted),
                # then the second trips the one-published index and fails
                # instead of superseding the first. The lock is released with
                # the transaction, so the later publisher then sees the earlier
                # one committed and supersedes it.
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", base_id)
                state = await conn.fetchval(
                    "SELECT state FROM knowledge_versions WHERE base_id=$1 AND version=$2 FOR UPDATE",
                    base_id, version,
                )
                if state is None:
                    raise KnowledgeError(f"版本不存在: {base_id}@{version}")
                if state != VersionState.BUILDING.value:
                    raise KnowledgeError(f"只有构建中的版本可发布 (state={state})")

                await conn.execute(
                    "UPDATE knowledge_versions SET state='superseded' WHERE base_id=$1 AND state='published'",
                    base_id,
                )
                row = await conn.fetchrow(
                    """
                    UPDATE knowledge_versions
                    SET state='published', published_at=now()
                    WHERE base_id=$1 AND version=$2
                    RETURNING embedding_model, embedding_dims, embedding_provider,
                              document_count, chunk_count, created_at, published_at
                    """,
                    base_id, version,
                )
            await self._refresh_statistics(conn)
        return self._version_from_row(base_id, version, VersionState.PUBLISHED, row)

    @staticmethod
    async def _refresh_statistics(conn: Any) -> None:
        """Refresh planner statistics after a version's worth of rows landed.

        A build inserts its chunks in bulk, and until autovacuum gets round to
        analysing the table the planner still believes the new version holds
        about one row. On that estimate it always sorts the version instead of
        walking the HNSW index, so retrieval stays exact and slow no matter
        how large the version is (regression: 10k chunks, index built, plan
        unchanged). ANALYZE samples rather than scans, so it is cheap at the
        one moment the corpus actually changed. Best effort: it needs table
        ownership, and a store running as a lesser role must still publish.
        """
        for table in ("knowledge_chunks", "knowledge_documents"):
            try:
                await conn.execute(f"ANALYZE {table}")
            except Exception as exc:
                log.warn("knowledge.postgres.analyze_failed", {"table": table, "error": str(exc)})
                return

    async def abandon_version(self, base_id: str, version: str, reason: str = "") -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE knowledge_versions SET state='failed', failure_reason=$3
                WHERE base_id=$1 AND version=$2 AND state='building'
                """,
                base_id, version, reason,
            )

    async def current_version(self, base_id: str) -> Optional[KnowledgeVersion]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT version, embedding_model, embedding_dims, embedding_provider,
                       document_count, chunk_count, created_at, published_at
                FROM knowledge_versions
                WHERE base_id=$1 AND state='published'
                """,
                base_id,
            )
        if row is None:
            return None
        return self._version_from_row(base_id, row["version"], VersionState.PUBLISHED, row)

    # -- retrieval ----------------------------------------------------------

    async def search(
        self,
        base_id: str,
        query_embedding: Sequence[float],
        embedding: EmbeddingSpec,
        scope: AccessScope,
        *,
        limit: int = 8,
        version: Optional[str] = None,
    ) -> List[SearchHit]:
        pool = await self._ensure_pool()

        target = version
        if target is None:
            current = await self.current_version(base_id)
            if current is None:
                return []
            target = current.version
            stored = current.embedding
        else:
            stored = await self._version_embedding(base_id, target)
            # An abandoned build holds whatever was ingested before it failed;
            # naming it explicitly must not resurrect that partial content.
            if await self._version_state(base_id, target) == VersionState.FAILED.value:
                return []

        if not stored.matches(embedding):
            # Vectors from different models are not comparable, so a number
            # would be produced but it would not mean similarity.
            raise EmbeddingMismatch(
                f"检索模型与版本不符: 版本 {stored.model}/{stored.dimensions}, "
                f"查询 {embedding.model}/{embedding.dimensions}"
            )
        if len(query_embedding) != stored.dimensions:
            raise EmbeddingMismatch(
                f"查询向量维度 {len(query_embedding)} 与版本 {stored.dimensions} 不符"
            )

        sql, params = self._search_statement(
            base_id, target, stored.dimensions, query_embedding, scope, limit
        )

        async with pool.acquire() as conn:
            if self._ef_search is None:
                rows = await conn.fetch(sql, *params)
            else:
                # SET LOCAL scopes the breadth to this retrieval; the pool resets
                # session state on release, so a plain SET on another checkout
                # would never reach the query.
                async with conn.transaction():
                    await conn.execute(f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}")
                    rows = await conn.fetch(sql, *params)

        return [
            SearchHit(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                title=row["title"],
                text=row["content"],
                score=float(row["score"]),
                ordinal=row["ordinal"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )
            for row in rows
        ]

    @staticmethod
    def _search_statement(
        base_id: str,
        version: str,
        dims: int,
        query_embedding: Sequence[float],
        scope: AccessScope,
        limit: int,
    ) -> tuple[str, List[Any]]:
        """The retrieval query and its parameters.

        Kept separate from :meth:`search` so a test can EXPLAIN exactly the
        statement the store runs: whether the planner reaches the partial
        HNSW index depends on the query's shape (the ``dims`` predicate and
        the fixed-width casts below), and that is only provable on the plan.
        """
        clauses = [
            "c.base_id = $1",
            "c.version = $2",
            # Revocation takes effect here, not when an index is next rebuilt.
            "d.revoked_at IS NULL",
            # States the vector width. This both keeps a mixed-width table from
            # being cast row by row and lets the planner match the partial ANN
            # index, which is restricted to exactly this width.
            f"c.dims = {int(dims)}",
        ]
        params: List[Any] = [base_id, version]

        if not scope.is_unrestricted:
            # A row is visible if it is unowned, or owned by this caller, or in
            # their project, or shared with one of their groups.
            params.append(scope.owner_id)
            owner_param = len(params)
            params.append(scope.project_id)
            project_param = len(params)
            params.append(list(scope.groups))
            groups_param = len(params)
            clauses.append(
                "("
                f"(d.owner_id = '' AND d.project_id = '' AND cardinality(d.groups) = 0)"
                f" OR (d.owner_id <> '' AND d.owner_id = ${owner_param})"
                f" OR (d.project_id <> '' AND d.project_id = ${project_param})"
                f" OR (cardinality(d.groups) > 0 AND d.groups && ${groups_param}::text[])"
                ")"
            )

        params.append(_vector_literal(query_embedding))
        vector_param = len(params)
        params.append(int(limit))
        limit_param = len(params)

        # Both sides cast to the fixed width, so the ordering expression is
        # the one the ANN index was built on. An un-cast comparison still ranks
        # correctly but never touches the index.
        distance = f"(c.embedding::vector({int(dims)}) <=> ${vector_param}::vector({int(dims)}))"
        sql = f"""
            SELECT c.chunk_id, c.document_id, c.ordinal, c.content, c.metadata,
                   d.title,
                   1 - {distance} AS score
            FROM knowledge_chunks c
            JOIN knowledge_documents d
              ON d.base_id = c.base_id AND d.version = c.version AND d.document_id = c.document_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {distance}
            LIMIT ${limit_param}
        """
        return sql, params

    # -- index management ---------------------------------------------------

    async def create_ann_index(
        self,
        *,
        dimensions: int,
        m: int = 16,
        ef_construction: int = 64,
        concurrently: bool = True,
    ) -> str:
        """Build an approximate-nearest-neighbour index over the chunk vectors.

        Not created by default. Exact search returns the true top-N and is fast
        enough until the corpus is large; an ANN index trades recall for speed,
        and giving up correct results before that trade is needed is a bad
        deal. Build it once the row count justifies it, and measure recall
        against exact search afterwards rather than assuming it.

        The embedding column is a bare ``vector`` so one table can hold
        versions of different widths, but an index needs a fixed width. The
        index is therefore *partial*, restricted to rows of that width by the
        stored ``dims`` column. Two details decide whether it gets used at all,
        and both fail silently when wrong: the operator class must match the
        query's distance operator (cosine, ``<=>``), and the query must imply
        the partial predicate -- which :meth:`search` does by filtering on the
        same width.
        """
        index_name = f"{ANN_INDEX_PREFIX}{int(dimensions)}"
        pool = await self._ensure_pool()
        statement = (
            f"CREATE INDEX {'CONCURRENTLY ' if concurrently else ''}IF NOT EXISTS {index_name} "
            f"ON knowledge_chunks USING hnsw ((embedding::vector({int(dimensions)})) vector_cosine_ops) "
            f"WITH (m = {int(m)}, ef_construction = {int(ef_construction)}) "
            f"WHERE dims = {int(dimensions)}"
        )
        async with pool.acquire() as conn:
            # CONCURRENTLY keeps writes flowing during the build but cannot run
            # inside a transaction; asyncpg only opens one when asked, so a
            # plain execute is safe either way.
            await conn.execute(statement)
            # The planner only prefers the new index over sorting once it knows
            # how many rows the versions actually hold.
            await self._refresh_statistics(conn)
        log.info(
            "knowledge.postgres.ann_index_created",
            {"index": index_name, "dimensions": dimensions, "m": m},
        )
        return index_name

    @staticmethod
    def ann_index_removal_statement(*, dimensions: int) -> str:
        """The statement that removes an ANN index, for an operator to run.

        Returned rather than executed. Removing an index is an operational
        decision -- it changes query plans for every concurrent request and,
        on a large table, rebuilding it later takes time the deployment has to
        schedule. The application creates indexes because that is additive and
        reversible; taking one away belongs with whoever is watching the load.
        """
        return (
            f"DROP INDEX CONCURRENTLY IF EXISTS {ANN_INDEX_PREFIX}{int(dimensions)};"
        )

    async def ann_indexes(self) -> List[str]:
        """ANN indexes this store manages.

        Scoped to the managed naming convention so a hand-built index an
        operator added for their own reasons is not reported as ours, and not
        mistaken for something the application will maintain.
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'knowledge_chunks'
                  AND indexname LIKE $1
                ORDER BY indexname
                """,
                ANN_INDEX_PREFIX + "%",
            )
        return [row["indexname"] for row in rows]

    async def set_search_ef(self, ef_search: int) -> None:
        """Set the ANN search breadth for this store's retrievals.

        Higher recovers recall an ANN index gave up, at the cost of latency.
        Scoped to this store rather than the database so a retrieval that
        needs accuracy can pay for it without slowing everything else down.
        Applied with ``SET LOCAL`` inside each search: pooled connections have
        their session state reset on release, so a ``SET`` issued on one
        checkout would be gone before the next query ran.
        """
        self._ef_search = int(ef_search)

    async def revoke_document(self, base_id: str, document_id: str) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            # Every version, not just the published one: a document whose
            # access was withdrawn must not resurface if an older version is
            # queried explicitly or republished.
            result = await conn.execute(
                """
                UPDATE knowledge_documents SET revoked_at = now()
                WHERE base_id=$1 AND document_id=$2 AND revoked_at IS NULL
                """,
                base_id, document_id,
            )
        return int(result.split()[-1]) if result else 0

    # -- helpers ------------------------------------------------------------

    async def _version_embedding(self, base_id: str, version: str) -> EmbeddingSpec:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT embedding_model, embedding_dims, embedding_provider
                FROM knowledge_versions WHERE base_id=$1 AND version=$2
                """,
                base_id, version,
            )
        if row is None:
            raise KnowledgeError(f"版本不存在: {base_id}@{version}")
        return EmbeddingSpec(
            model=row["embedding_model"],
            dimensions=row["embedding_dims"],
            provider=row["embedding_provider"],
        )

    async def _version_state(self, base_id: str, version: str) -> Optional[str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT state FROM knowledge_versions WHERE base_id=$1 AND version=$2",
                base_id, version,
            )

    @staticmethod
    def _version_from_row(base_id: str, version: str, state: VersionState, row: Any) -> KnowledgeVersion:
        return KnowledgeVersion(
            base_id=base_id,
            version=version,
            state=state,
            embedding=EmbeddingSpec(
                model=row["embedding_model"],
                dimensions=row["embedding_dims"],
                provider=row["embedding_provider"],
            ),
            created_at=row["created_at"],
            published_at=row["published_at"],
            document_count=row["document_count"],
            chunk_count=row["chunk_count"],
        )


__all__ = ["ANN_INDEX_PREFIX", "PostgresKnowledgeStore", "SCHEMA_STATEMENTS"]
