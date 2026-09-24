"""
Vector storage and search extension for Storage system

Provides vector similarity and PostgreSQL substring search capabilities
for the memory system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional, Dict, Any, Tuple
from pathlib import Path
import json
import asyncio
import heapq
import math
import re
from uuid import uuid4
from datetime import datetime

from flocks.db.params import Params, in_clause
from flocks.db.search import (
    MEMORY_COLUMNS,
    RANK_ALIAS,
    MEMORY_MIN_TERM_CHARS,
    memory_search_index_for,
)
from flocks.storage.storage import Storage
from flocks.utils.log import Log

if TYPE_CHECKING:
    from flocks.memory.types import MemoryTimeRange

log = Log.create(service="storage.vector")


def _vector_schema(dialect) -> list[str]:
    """PostgreSQL memory metadata and UTF-8 JSON embeddings stored as BYTEA."""
    text = dialect.text_type()
    num = dialect.integer_type()
    real = dialect.double_type()
    blob = dialect.blob_type()
    return [
        f"""
        CREATE TABLE IF NOT EXISTS memory_files (
            scope      {text} NOT NULL,
            scope_id   {text} NOT NULL,
            path       {text} NOT NULL,
            source     {text} NOT NULL,
            hash       {text} NOT NULL,
            mtime      {real} NOT NULL,
            size       {num} NOT NULL,
            indexed_at {real} NOT NULL,
            PRIMARY KEY (scope, scope_id, path)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS memory_chunks (
            id              {text} PRIMARY KEY,
            scope           {text} NOT NULL,
            scope_id        {text} NOT NULL,
            path            {text} NOT NULL,
            source          {text} NOT NULL,
            start_line      {num} NOT NULL,
            end_line        {num} NOT NULL,
            hash            {text} NOT NULL,
            text            {text} NOT NULL,
            embedding       {blob},
            embedding_model {text},
            embedding_dims  {num},
            created_at      {real} NOT NULL,
            updated_at      {real} NOT NULL,
            FOREIGN KEY (scope, scope_id, path)
                REFERENCES memory_files(scope, scope_id, path) ON DELETE CASCADE
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS memory_embedding_cache (
            text_hash   {text} NOT NULL,
            provider    {text} NOT NULL,
            model       {text} NOT NULL,
            embedding   {blob} NOT NULL,
            dims        {num} NOT NULL,
            created_at  {real} NOT NULL,
            accessed_at {real} NOT NULL,
            PRIMARY KEY (text_hash, provider, model)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_memory_files_scope ON memory_files(scope, scope_id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_files_source ON memory_files(source)",
        "CREATE INDEX IF NOT EXISTS idx_memory_chunks_scope ON memory_chunks(scope, scope_id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_chunks_path ON memory_chunks(scope, scope_id, path)",
        "CREATE INDEX IF NOT EXISTS idx_memory_chunks_source ON memory_chunks(source)",
        (
            "CREATE INDEX IF NOT EXISTS idx_memory_embedding_cache_accessed "
            "ON memory_embedding_cache(accessed_at)"
        ),
    ]


# trigram 分词器把任何文字按 3 字符滑窗建索引，中文不用分词也能命中（porter unicode61 会把
# "告警处理约定" 整段当一个 token，中文查询恒 0 结果）。代价：短于 3 个字符的词 trigram
# 查不到，fts_search 对这类词走 LIKE 兜底。表结构本身由 flocks.db.search 的索引实现给出，
# PostgreSQL 上换成 pg_trgm——同样按三字符窗口、同样是子串语义。
FTS_TOKENIZER = "pg_trgm"
FTS_MIN_TERM_CHARS = MEMORY_MIN_TERM_CHARS
FTS_COLUMNS = ", ".join(("text",) + tuple(c for c in MEMORY_COLUMNS if c != "text"))

async def ensure_vector_tables(db_path: Path) -> Dict[str, Any]:
    """
    Ensure vector storage tables exist
    
    Returns:
        Status dict with table availability info
    """
    # Keep the legacy status keys for callers; the implementation is PG-only.
    status: Dict[str, Any] = {
        "vector_tables": False,
        "fts5": False,
        "fts5_error": None,
    }
    
    try:
        # ensure_init=False: this runs from inside Storage.init(); asking init
        # to finish from within it recurses until the stack runs out.
        async with Storage.transaction(db_path, ensure_init=False) as db:
            d = db.dialect
            await db.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema()), hashtext('flocks.memory.schema'))")
            columns_of = lambda table: db.fetch_all(  # noqa: E731
                d.columns_query().format(table=table), d.columns_query_params(table)
            )
            name_at = d.column_name_index()
            file_columns = {row[name_at] for row in await columns_of("memory_files")}
            chunk_columns = {row[name_at] for row in await columns_of("memory_chunks")}
            fts_columns = {row[name_at] for row in await columns_of("memory_fts")}
            old_scope_schema = any(
                columns
                and not {"scope", "scope_id"}.issubset(columns)
                for columns in (file_columns, chunk_columns)
            )
            old_fts_schema = bool(fts_columns and not {"scope", "scope_id"}.issubset(fts_columns))
            if old_scope_schema or old_fts_schema:
                # Tables from before scope/scope_id existed. Renaming them
                # aside leaves the rows on disk and lets the CREATE statements
                # below build the current schema next to them; dropping would
                # destroy an index that can only be rebuilt by re-reading every
                # memory file, and would do it without asking.
                stamp = uuid4().hex[:12]
                present = {"memory_fts": fts_columns, "memory_chunks": chunk_columns, "memory_files": file_columns}
                retained = [table for table in present if present[table] and (old_scope_schema or table == "memory_fts")]
                indexes = await db.fetch_all(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = ANY(?::text[])",
                    (retained,),
                )
                # PostgreSQL index names live in the schema namespace. Retain
                # them under new names so the new tables receive real indexes.
                statements = [f"ALTER INDEX {d.quote(name)} RENAME TO {d.quote(name[:35] + '_legacy_' + stamp)}"
                              for (name,) in indexes]
                statements.extend(f"ALTER TABLE {d.quote(table)} RENAME TO {d.quote(table + '_legacy_prescope_' + stamp)}"
                                  for table in retained)
                if statements:
                    await db.executescript(";\n".join(statements))
                log.info(
                    "vector.memory_scope_schema.rebuilt",
                    {"legacy_suffix": f"legacy_prescope_{stamp}"},
                )

            await db.executescript(";\n".join(_vector_schema(d)))
            status["vector_tables"] = True
            log.info("vector.tables.created")

            index = memory_search_index_for(d.name)
            if index is None:
                status["fts5_error"] = f"no full-text index for backend {d.name!r}"
                log.warn("vector.fts5.unsupported", {"backend": d.name})
                return status
            try:
                await db.executescript(";\n".join(index.schema_statements()))
                if old_fts_schema and not old_scope_schema:
                    await db.execute("""INSERT INTO memory_fts(chunk_id, path, source, scope, scope_id, start_line, end_line, text)
                        SELECT id, path, source, scope, scope_id, start_line, end_line, text FROM memory_chunks
                        ON CONFLICT (chunk_id) DO NOTHING""")
                status["fts5"] = True
                log.info("vector.fts5.created")
            except Exception as e:
                status["fts5_error"] = str(e)
                log.warn("vector.fts5.failed", {"error": str(e)})
                raise

            # Matching is correct without these; they only make it fast, and
            # CREATE EXTENSION needs privileges a deployment may withhold.
            for statement in index.accelerator_statements():
                await db.execute("SAVEPOINT memory_accelerator")
                try:
                    await db.execute(statement)
                except Exception as e:
                    await db.execute("ROLLBACK TO SAVEPOINT memory_accelerator")
                    log.warn("vector.fts5.accelerator_skipped", {"error": str(e)})
                finally:
                    await db.execute("RELEASE SAVEPOINT memory_accelerator")

        return status
    except Exception as e:
        log.error("vector.tables.failed", {"error": str(e)})
        raise


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors
    
    Args:
        a: First vector
        b: Second vector
        
    Returns:
        Similarity score (0-1)
    """
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")
    
    dot_product = sum(x * y for x, y in zip(a, b))
    magnitude_a = math.sqrt(sum(x * x for x in a))
    magnitude_b = math.sqrt(sum(y * y for y in b))
    
    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0
    
    return dot_product / (magnitude_a * magnitude_b)


def _embedding_bytes(value: Any) -> bytes | None:
    """The BYTEA representation is UTF-8 JSON, including migrated text blobs."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _embedding_values(value: Any) -> list[float]:
    if isinstance(value, (bytearray, memoryview)):
        value = bytes(value)
    decoded = json.loads(value) if isinstance(value, (str, bytes)) else value
    if not isinstance(decoded, list):
        raise ValueError("Embedding must be a JSON array")
    return decoded


def bm25_rank_to_score(rank: float) -> float:
    """
    Convert BM25 rank to normalized score (0-1)
    
    Args:
        rank: BM25 rank value
        
    Returns:
        Normalized score
    """
    normalized = abs(rank) if math.isfinite(rank) else 999
    return 1 / (1 + normalized)


async def vector_search(
    db_path: Path,
    project_id: str,
    embedding: List[float],
    max_results: int = 10,
    min_score: float = 0.0,
    sources: Optional[List[str]] = None,
    time_range: Optional[MemoryTimeRange] = None,
) -> List[Dict[str, Any]]:
    """
    Perform vector similarity search
    
    Exact cosine scoring uses bounded pages and runs outside the event loop.
    
    Args:
        db_path: Database path
        project_id: Current Session project ID
        embedding: Query embedding vector
        max_results: Maximum results to return
        min_score: Minimum similarity score
        sources: Optional list of sources to filter ('memory', 'session')
        time_range: Optional Daily filename date filter
        
    Returns:
        List of search results
    """
    results = []
    max_results = max(0, int(max_results))
    if max_results == 0:
        return results
    
    try:
        async with Storage.transaction(db_path) as db:
            query = """
                SELECT id, path, source, start_line, end_line, text, embedding
                FROM memory_chunks
                WHERE embedding IS NOT NULL
                    AND (
                        scope = 'global'
                        OR (scope = 'project' AND scope_id = ?)
                    )
            """
            params: list[Any] = [project_id]

            if time_range is not None:
                # Daily notes are named by date; PostgreSQL matches that shape
                # with a regular expression plus an indexed path range.
                daily = memory_search_index_for(db.dialect.name)
                query += " AND scope = 'global'"
                if daily is not None:
                    query += f" AND {daily.daily_path_clause('path')}"
                if time_range.daily_start_date is not None:
                    query += " AND path >= ?"
                    params.append(f"daily/{time_range.daily_start_date}.md")
                if time_range.daily_end_date is not None:
                    query += " AND path < ?"
                    params.append(f"daily/{time_range.daily_end_date}.md")
            
            if sources:
                placeholders = ",".join("?" * len(sources))
                query += f" AND source IN ({placeholders})"
                params.extend(sources)
            
            cursor_id = None
            while True:
                page_sql = query + (" AND id > ?" if cursor_id is not None else "") + " ORDER BY id LIMIT 256"
                page_params = [*params, cursor_id] if cursor_id is not None else params
                rows = await db.fetch_all(page_sql, page_params)
                if not rows:
                    break
                scored = await asyncio.to_thread(_score_vector_rows, rows, embedding, min_score)
                results = heapq.nlargest(max_results, [*results, *scored], key=lambda item: item["score"])
                cursor_id = rows[-1][0]
                if len(rows) < 256:
                    break
            return results
    
    except Exception as e:
        log.error("vector.search.failed", {"error": str(e)})
        raise


def _score_vector_rows(rows, embedding, min_score):
    results = []
    for chunk_id, path, source, start_line, end_line, text, stored in rows:
        try:
            score = cosine_similarity(embedding, _embedding_values(stored))
            if math.isfinite(score) and score >= min_score:
                results.append({"id": chunk_id, "path": path, "source": source,
                                "start_line": start_line, "end_line": end_line,
                                "text": text, "score": score})
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            log.warn("vector.search.embedding_error", {"chunk_id": chunk_id, "error": str(exc)})
    return results


def split_fts_terms(raw: str) -> Tuple[List[str], List[str]]:
    """Split raw text into (MATCH-able terms, short terms).

    trigram 只能匹配 ≥3 个字符的词，"告警"、"IP" 这类短词由调用方走 LIKE 兜底。
    """
    tokens = [t for t in re.findall(r"\w+", raw, flags=re.UNICODE) if t.strip()]
    long_terms = [t for t in tokens if len(t) >= FTS_MIN_TERM_CHARS]
    short_terms = [t for t in tokens if len(t) < FTS_MIN_TERM_CHARS]
    return long_terms, short_terms


def build_fts_query(raw: str) -> Optional[str]:
    """
    Build FTS5 query string from raw text
    
    Extracts Unicode word tokens and combines them with AND.
    
    Args:
        raw: Raw query text
        
    Returns:
        FTS5 query string or None if no valid tokens
    """
    # Python's Unicode-aware ``\w`` keeps CJK and other scripts intact while
    # quoting prevents user input from becoming FTS5 syntax.
    long_terms, _short = split_fts_terms(raw)
    if not long_terms:
        return None
    
    # Quote and combine with AND
    quoted = ['"' + t.replace('"', "") + '"' for t in long_terms]
    return ' AND '.join(quoted)


def _like_pattern(term: str) -> str:
    """LIKE 兜底用的模式：转义 % _ \\ 后包成 %term%。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def fts_search(
    db_path: Path,
    project_id: str,
    query: str,
    max_results: int = 10,
    sources: Optional[List[str]] = None,
    time_range: Optional[MemoryTimeRange] = None,
) -> List[Dict[str, Any]]:
    """
    Perform PostgreSQL substring search
    
    Args:
        db_path: Database path
        project_id: Current Session project ID
        query: Plain keyword query
        max_results: Maximum results to return
        sources: Optional list of sources to filter
        time_range: Optional Daily filename date filter
        
    Returns:
        List of search results with normalized relevance scores
    """
    results = []
    
    try:
        async with Storage.transaction(db_path) as db:
            index = memory_search_index_for(db.dialect.name)
            if index is None:
                # No full-text index on this backend. Returning nothing is the
                # honest answer; a query that always matches is not.
                return results

            # ≥3 字符走索引匹配；更短的词 trigram 查不到，用子串兜底。
            # 查询串长什么样由索引自己决定：FTS5 要它自己的语法，pg_trgm 要的是词本身。
            fts_query = index.prepare_query(query)
            _long_terms, short_terms = split_fts_terms(query)

            params = Params(db.dialect)
            # Assembled in the order the markers appear, because a backend that
            # numbers its parameters cannot have them added out of order.
            trigram = await db.fetch_one("SELECT to_regprocedure('similarity(text,text)') IS NOT NULL") if fts_query else None
            rank_expression = index.rank_expression(params, fts_query, use_trigram=bool(trigram and trigram[0]))
            sql = f"""
                SELECT
                    f.chunk_id,
                    f.path,
                    f.source,
                    f.start_line,
                    f.end_line,
                    f.text,
                    {rank_expression} AS {RANK_ALIAS}
                FROM {index.table} f
                WHERE (
                        f.scope = 'global'
                        OR (f.scope = 'project' AND f.scope_id = {params.add(project_id)})
                    )
            """
            if fts_query:
                sql += f" AND {index.match_clause(params, 'f.text', fts_query)}"
            for term in short_terms:
                sql += f" AND {index.contains_clause(params, 'f.text', _like_pattern(term))}"

            if time_range is not None:
                sql += f" AND f.scope = 'global' AND {index.daily_path_clause('f.path')}"
                if time_range.daily_start_date is not None:
                    sql += f" AND f.path >= {params.add(f'daily/{time_range.daily_start_date}.md')}"
                if time_range.daily_end_date is not None:
                    sql += f" AND f.path < {params.add(f'daily/{time_range.daily_end_date}.md')}"

            if sources:
                sql += f" AND {in_clause(params, 'f.source', list(sources))}"

            if fts_query:
                sql += f" ORDER BY {index.rank_order()}"
            else:
                sql += " ORDER BY f.path DESC, CAST(f.start_line AS INTEGER)"
            sql += f" LIMIT {params.add(max_results)}"

            rows = await db.fetch_all(sql, params.values)

            # Convert ranks to scores
            for row in rows:
                chunk_id, path, source, start_line, end_line, text, rank = row
                # Every returned row contains all requested terms. Give that
                # exact substring match the same 0.5 floor as the short-term
                # fallback; whole-document trigram similarity alone approaches
                # zero for long notes and defeats the default keyword fallback.
                # Keep higher PG ranks better, including during hybrid merging.
                score = 0.5 + 0.5 * max(0.0, min(float(rank), 1.0)) if fts_query else 1.0
                if not fts_query and short_terms:
                    # Short terms have no trigram relevance score.
                    score = 0.5
                
                results.append({
                    "id": chunk_id,
                    "path": path,
                    "source": source,
                    "start_line": start_line,
                    "end_line": end_line,
                    "text": text,
                    "score": score,
                })
            
            return results
    
    except Exception as e:
        log.error("fts.search.failed", {"error": str(e)})
        raise


async def insert_chunks(
    db_path: Path,
    chunks: List[Dict[str, Any]],
) -> int:
    """
    Batch insert text chunks with embeddings
    
    Args:
        db_path: Database path
        chunks: List of chunk dicts with keys:
            - id, scope, scope_id, path, source, start_line, end_line,
              hash, text, embedding, embedding_model, embedding_dims
        
    Returns:
        Number of chunks inserted
    """
    try:
        async with Storage.transaction(db_path) as db:
            now = datetime.now().timestamp()
            
            # Insert into chunks table
            chunk_columns = (
                "id", "scope", "scope_id", "path", "source", "start_line", "end_line",
                "hash", "text", "embedding", "embedding_model", "embedding_dims",
                "created_at", "updated_at",
            )
            await db.execute_many(
                db.dialect.upsert(
                    "memory_chunks",
                    chunk_columns,
                    key_columns=("id",),
                    update_columns=tuple(c for c in chunk_columns if c != "id"),
                ),
                [
                (
                    chunk["id"],
                    chunk["scope"],
                    chunk["scope_id"],
                    chunk["path"],
                    chunk["source"],
                    chunk["start_line"],
                    chunk["end_line"],
                    chunk["hash"],
                    chunk["text"],
                    _embedding_bytes(chunk.get("embedding")),
                    chunk.get("embedding_model"),
                    chunk.get("embedding_dims"),
                    now,
                    now,
                )
                for chunk in chunks
                ],
            )

            # Insert into the full-text index (if it exists)
            await db.execute("SAVEPOINT memory_chunk_search")
            try:
                chunk_ids = [chunk["id"] for chunk in chunks]
                if chunk_ids:
                    placeholders = ",".join("?" for _ in chunk_ids)
                    await db.execute(
                        f"DELETE FROM memory_fts WHERE chunk_id IN ({placeholders})",
                        tuple(chunk_ids),
                    )
                fts_columns = (
                    "chunk_id", "path", "source", "scope", "scope_id",
                    "start_line", "end_line", "text",
                )
                await db.execute_many(
                    db.dialect.upsert(
                        "memory_fts",
                        fts_columns,
                        key_columns=("chunk_id",),
                        update_columns=tuple(c for c in fts_columns if c != "chunk_id"),
                    ),
                    [
                    (
                        chunk["id"],
                        chunk["path"],
                        chunk["source"],
                        chunk["scope"],
                        chunk["scope_id"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["text"],
                    )
                    for chunk in chunks
                    ],
                )
            except Exception as e:
                await db.execute("ROLLBACK TO SAVEPOINT memory_chunk_search")
                log.warn("fts.insert.failed", {"error": str(e)})
            finally:
                await db.execute("RELEASE SAVEPOINT memory_chunk_search")
            
            await db.commit()
            
            log.info("chunks.inserted", {"count": len(chunks)})
            return len(chunks)
    
    except Exception as e:
        log.error("chunks.insert.failed", {"error": str(e)})
        raise


async def replace_memory_file_index(
    db_path: Path,
    *,
    file_entry: Dict[str, Any],
    chunks: List[Dict[str, Any]],
    before_replace: Optional[Callable[[Any, Dict[str, Any]], Awaitable[None]]] = None,
) -> int:
    """Atomically replace one Memory file's metadata, chunks, and FTS rows."""
    scope = file_entry["scope"]
    scope_id = file_entry["scope_id"]
    path = file_entry["path"]
    now = datetime.now().timestamp()
    async with Storage.transaction(db_path) as db:
        try:
            # Serialize replacements of the same logical memory file.
            await db.execute("SELECT pg_advisory_xact_lock(hashtext(?))",
                             (f"memory:{scope}:{scope_id}:{path}",))
            if before_replace is not None:
                await before_replace(db, file_entry)
            await db.execute(
                """
                DELETE FROM memory_fts
                WHERE scope = ? AND scope_id = ?
                    AND source = 'memory' AND path = ?
                """,
                (scope, scope_id, path),
            )
            await db.execute(
                """
                DELETE FROM memory_chunks
                WHERE scope = ? AND scope_id = ?
                    AND source = 'memory' AND path = ?
                """,
                (scope, scope_id, path),
            )
            file_columns = (
                "scope", "scope_id", "path", "source", "hash", "mtime", "size",
                "indexed_at",
            )
            await db.execute(
                db.dialect.upsert(
                    "memory_files",
                    file_columns,
                    key_columns=("scope", "scope_id", "path"),
                    update_columns=("source", "hash", "mtime", "size", "indexed_at"),
                ),
                (
                    scope,
                    scope_id,
                    path,
                    "memory",
                    file_entry["hash"],
                    file_entry["mtime"],
                    file_entry["size"],
                    now,
                ),
            )
            await db.execute_many(
                """
                INSERT INTO memory_chunks (
                    id, scope, scope_id, path, source, start_line, end_line, hash,
                    text, embedding, embedding_model, embedding_dims,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk["id"],
                        chunk["scope"],
                        chunk["scope_id"],
                        chunk["path"],
                        chunk["source"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["hash"],
                        chunk["text"],
                        _embedding_bytes(chunk.get("embedding")),
                        chunk.get("embedding_model"),
                        chunk.get("embedding_dims"),
                        now,
                        now,
                    )
                    for chunk in chunks
                ],
            )
            await db.execute_many(
                """
                INSERT INTO memory_fts (
                    chunk_id, path, source, scope, scope_id, start_line, end_line,
                    text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk["id"],
                        chunk["path"],
                        chunk["source"],
                        chunk["scope"],
                        chunk["scope_id"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["text"],
                    )
                    for chunk in chunks
                ],
            )
            await db.commit()
            return len(chunks)
        except BaseException:
            await db.rollback()
            raise


async def get_embedding_from_cache(
    db_path: Path,
    text_hash: str,
    provider: str,
    model: str,
) -> Optional[Tuple[List[float], int]]:
    """
    Get embedding from cache
    
    Returns:
        Tuple of (embedding, dims) or None if not found
    """
    try:
        async with Storage.transaction(db_path) as db:
            cursor = await db.execute("""
                SELECT embedding, dims
                FROM memory_embedding_cache
                WHERE text_hash = ? AND provider = ? AND model = ?
            """, (text_hash, provider, model))
            
            row = await cursor.fetchone()
            if row:
                embedding_blob, dims = row
                embedding = _embedding_values(embedding_blob)
                
                # Update accessed_at
                now = datetime.now().timestamp()
                await db.execute("""
                    UPDATE memory_embedding_cache
                    SET accessed_at = ?
                    WHERE text_hash = ? AND provider = ? AND model = ?
                """, (now, text_hash, provider, model))
                await db.commit()
                
                return embedding, dims
            
            return None
    except Exception as e:
        log.warn("cache.get.failed", {"error": str(e)})
        return None


async def put_embedding_to_cache(
    db_path: Path,
    text_hash: str,
    provider: str,
    model: str,
    embedding: List[float],
    dims: int,
) -> None:
    """
    Put embedding to cache
    """
    try:
        async with Storage.transaction(db_path) as db:
            now = datetime.now().timestamp()
            cache_columns = (
                "text_hash", "provider", "model", "embedding", "dims",
                "created_at", "accessed_at",
            )
            await db.execute(
                db.dialect.upsert(
                    "memory_embedding_cache",
                    cache_columns,
                    key_columns=("text_hash", "provider", "model"),
                    update_columns=("embedding", "dims", "created_at", "accessed_at"),
                ),
                (
                text_hash,
                provider,
                model,
                _embedding_bytes(embedding),
                dims,
                now,
                now,
                ),
            )
            await db.commit()
    except Exception as e:
        log.warn("cache.put.failed", {"error": str(e)})
