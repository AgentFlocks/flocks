"""Full-text search, where the backends are allowed to differ.

SQLite keeps transcripts in an FTS5 virtual table addressed by rowid.
PostgreSQL keeps them in an ordinary table with a ``tsvector`` column and a GIN
index. The two schemas have nothing in common, and pretending otherwise would
mean writing the worse half of both. So the difference lives here, behind one
interface, and the caller in ``session_search`` stays the same on either.

On Chinese text this is where honesty matters more than coverage. SQLite's
``unicode61`` tokeniser does not segment Chinese, and PostgreSQL's ``simple``
configuration does not either; both index a run of Han characters as one
token, so only a whole-phrase match finds it. A deployment that needs real
Chinese search installs a segmenter (zhparser or pg_jieba on PostgreSQL) and
sets ``FLOCKS_DB_FTS_CONFIG`` to its text-search configuration. What this
module will not do is report success while indexing text nothing can find.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

from flocks.db.store import Transaction

# The PostgreSQL text-search configuration. 'simple' is the safe default: it
# never fails, it just cannot segment Chinese. Deployments with zhparser or
# pg_jieba point this at their configuration instead.
FTS_CONFIG_ENV = "FLOCKS_DB_FTS_CONFIG"
DEFAULT_PG_FTS_CONFIG = "simple"


def pg_fts_config() -> str:
    value = (os.environ.get(FTS_CONFIG_ENV) or "").strip()
    return value or DEFAULT_PG_FTS_CONFIG




class PostgresSearchIndex:
    """A tsvector column with a GIN index, in a table of its own."""

    name = "postgres"

    table = "session_transcript_search"
    join_column = "row_id"

    def __init__(self, configuration: Optional[str] = None) -> None:
        self._configuration = configuration or pg_fts_config()

    @property
    def segments_chinese(self) -> bool:
        # Only true once a deployment has pointed us at a segmenting
        # configuration; 'simple' cannot do it.
        return self._configuration != DEFAULT_PG_FTS_CONFIG

    def schema_statements(self) -> list[str]:
        config = self._configuration
        return [
            """
            CREATE TABLE IF NOT EXISTS session_transcript_search (
                row_id BIGINT PRIMARY KEY,
                text   TEXT   NOT NULL
            )
            """,
            # A stored generated column keeps the vector in step with the text
            # without a trigger, so an UPDATE cannot leave them disagreeing.
            f"""
            ALTER TABLE session_transcript_search
                ADD COLUMN IF NOT EXISTS text_search tsvector
                GENERATED ALWAYS AS (to_tsvector('{config}', text)) STORED
            """,
            """
            CREATE INDEX IF NOT EXISTS session_transcript_search_vector
                ON session_transcript_search USING GIN (text_search)
            """,
        ]

    async def upsert_text(self, transaction: Transaction, row_id: int, text: str) -> None:
        sql = transaction.dialect.upsert(
            "session_transcript_search",
            ("row_id", "text"),
            key_columns=("row_id",),
            update_columns=("text",),
        )
        await transaction.execute(sql, (row_id, text))

    async def read_text(self, transaction: Transaction, row_id: int) -> Optional[str]:
        mark = transaction.dialect.placeholder
        row = await transaction.fetch_one(
            f"SELECT text FROM session_transcript_search WHERE row_id = {mark(1)}", (row_id,)
        )
        return row[0] if row is not None else None

    async def delete_rows(self, transaction: Transaction, row_ids: Sequence[int]) -> None:
        if not row_ids:
            return
        mark = transaction.dialect.placeholder
        await transaction.execute(
            f"DELETE FROM session_transcript_search WHERE row_id = ANY({mark(1)}::bigint[])",
            (list(row_ids),),
        )

    def prepare_query(self, raw: str) -> Optional[str]:
        """``plainto_tsquery`` parses ordinary text, so it is passed through."""
        cleaned = raw.strip()
        return cleaned or None

    def snippet_expression(self, params, query: Optional[str]) -> str:
        if not query:
            return f"{self.table}.text"
        # ts_headline marks the matched terms; StartSel/StopSel are emptied so
        # the caller gets the same bare excerpt SQLite's snippet() returns.
        marker = params.add(query)
        return (
            f"ts_headline('{self._configuration}', {self.table}.text, "
            f"plainto_tsquery('{self._configuration}', {marker}), "
            f"'StartSel=\"\", StopSel=\"\", MaxWords=24, MinWords=8, FragmentDelimiter=\" … \"')"
        )

    def match_clause(self, params, query: str) -> str:
        marker = params.add(query)
        return f"{self.table}.text_search @@ plainto_tsquery('{self._configuration}', {marker})"

    def rank_clause(self, params, query: str) -> str:
        # bm25() on SQLite sorts ascending (better first); ts_rank is the other
        # way round, so the caller's ORDER BY gets DESC written in here.
        marker = params.add(query)
        return (
            f"ts_rank({self.table}.text_search, "
            f"plainto_tsquery('{self._configuration}', {marker})) DESC"
        )


def search_index_for(backend: str):
    """The search index implementation for *backend*, or None if it has none."""
    name = backend.strip().lower()
    if name in {"postgres", "postgresql"}:
        return PostgresSearchIndex()
    # MySQL: InnoDB full-text needs an ngram parser before it tokenises CJK at
    # all. Returning None makes the caller degrade to "no search" rather than
    # to "search that silently finds nothing".
    return None




# ---------------------------------------------------------------------------
# The memory index
#
# Memory search is a different shape from transcript search: one table holds
# the text and every piece of metadata a result needs, and matching is by
# substring rather than by word. SQLite gets that from FTS5's trigram
# tokeniser -- chosen because unicode61 treats a run of Chinese as a single
# token, so a Chinese query matched nothing. PostgreSQL's equivalent is
# pg_trgm: the same three-character windows, the same substring semantics,
# and the same indifference to word boundaries, which is what makes it the
# honest counterpart rather than a convenient one.
# ---------------------------------------------------------------------------

MEMORY_TABLE = "memory_fts"
MEMORY_COLUMNS = (
    "chunk_id",
    "path",
    "source",
    "scope",
    "scope_id",
    "start_line",
    "end_line",
    "text",
)
#: Below this, a trigram index has nothing to match on and the caller falls
#: back to a plain substring scan.
MEMORY_MIN_TERM_CHARS = 3
#: The name the ranking expression is selected as, so ORDER BY can name it
#: rather than count columns.
RANK_ALIAS = "rank_score"




class PostgresMemoryIndex:
    """An ordinary table with a pg_trgm index over the text."""

    name = "postgres"
    table = MEMORY_TABLE

    def schema_statements(self) -> list[str]:
        return [
            f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                chunk_id   TEXT PRIMARY KEY,
                path       TEXT,
                source     TEXT,
                scope      TEXT,
                scope_id   TEXT,
                start_line INTEGER,
                end_line   INTEGER,
                text       TEXT NOT NULL
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS {self.table}_scope
                ON {self.table} (scope, scope_id, source, path)
            """,
        ]

    def accelerator_statements(self) -> list[str]:
        """Best-effort: matching is correct without these, just unindexed.

        CREATE EXTENSION needs privileges a hardened deployment may withhold,
        so a failure here must not take memory search down with it.
        """
        return [
            "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public",
            f"""
            CREATE INDEX IF NOT EXISTS {self.table}_text_trgm
                ON {self.table} USING GIN (text gin_trgm_ops)
            """,
        ]

    def plain(self, expression: str) -> str:
        # Nothing takes over LIKE here, so the expression stands as written.
        return expression

    def prepare_query(self, raw: str):
        """The search terms, which is what this backend's matching consumes.

        FTS5 wants its own query language; here the same request is a set of
        substrings to require, so the terms are kept as terms.
        """
        from flocks.storage.vector import split_fts_terms

        long_terms, _short = split_fts_terms(raw)
        return long_terms or None

    def match_clause(self, params, column: str, prepared) -> str:
        # The trigram tokeniser matches substrings and so does ILIKE; requiring
        # every term is what FTS5's implicit AND does on the other side.
        return " AND ".join(
            f"{column} ILIKE {params.add(f'%{term}%')}" for term in prepared
        )

    def contains_clause(self, params, column: str, pattern: str) -> str:
        # ILIKE, not LIKE: SQLite's LIKE ignores ASCII case and PostgreSQL's
        # does not, and a fallback that answers differently per backend is
        # worse than one that is merely slower.
        return f"{column} ILIKE {params.add(pattern)} ESCAPE '\\'"

    def daily_path_clause(self, column: str) -> str:
        return f"{column} ~ '^daily/[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}\\.md$'"

    def rank_expression(self, params, prepared, *, use_trigram: bool = True) -> str:
        if not prepared:
            return "0.0"
        if not use_trigram:
            # Substring matching must still work when pg_trgm is unavailable
            # or outside the connection's search_path.
            return f"1.0 / (1.0 + GREATEST(length(f.text) - length({params.add(' '.join(prepared))}::text), 0))"
        return f"similarity(f.text, {params.add(' '.join(prepared))})"

    def rank_order(self) -> str:
        # similarity() is better when higher; SQLite's bm25 rank is better when
        # lower, so the direction is part of what each backend answers.
        return f"{RANK_ALIAS} DESC"


def memory_search_index_for(backend: str):
    """The memory index for *backend*, or None if it has none."""
    name = backend.strip().lower()
    if name in {"postgres", "postgresql"}:
        return PostgresMemoryIndex()
    return None

__all__ = [
    "DEFAULT_PG_FTS_CONFIG",
    "FTS_CONFIG_ENV",
    "MEMORY_COLUMNS",
    "MEMORY_MIN_TERM_CHARS",
    "MEMORY_TABLE",
    "RANK_ALIAS",
    "PostgresMemoryIndex",
    "PostgresSearchIndex",
    "memory_search_index_for",
    "pg_fts_config",
    "search_index_for",
]
