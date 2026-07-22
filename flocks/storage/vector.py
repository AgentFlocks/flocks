"""
Vector storage and search extension for Storage system

Provides vector similarity search and FTS5 full-text search capabilities
for the memory system.
"""

from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
import aiosqlite
import json
import math
from datetime import datetime

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="storage.vector")


# Database Schema SQL
VECTOR_SCHEMA_SQL = """
-- Memory files index table
CREATE TABLE IF NOT EXISTS memory_files (
    path TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL,  -- 'memory' | 'session'
    hash TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    indexed_at REAL NOT NULL
);

-- Memory chunks table
CREATE TABLE IF NOT EXISTS memory_chunks (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    hash TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB,  -- pickled numpy array or JSON list
    embedding_model TEXT,
    embedding_dims INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (path) REFERENCES memory_files(path) ON DELETE CASCADE
);

-- Embedding cache table (shared across projects)
CREATE TABLE IF NOT EXISTS memory_embedding_cache (
    text_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    dims INTEGER NOT NULL,
    created_at REAL NOT NULL,
    accessed_at REAL NOT NULL,
    PRIMARY KEY (text_hash, provider, model)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_memory_files_project ON memory_files(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_files_source ON memory_files(source);
CREATE INDEX IF NOT EXISTS idx_memory_chunks_project ON memory_chunks(project_id);
CREATE INDEX IF NOT EXISTS idx_memory_chunks_path ON memory_chunks(path);
CREATE INDEX IF NOT EXISTS idx_memory_chunks_source ON memory_chunks(source);
CREATE INDEX IF NOT EXISTS idx_memory_embedding_cache_accessed ON memory_embedding_cache(accessed_at);
"""

# FTS5 Schema (created separately to handle errors gracefully)
FTS5_SCHEMA_SQL = """
-- FTS5 full-text search table
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    text,
    chunk_id UNINDEXED,
    path UNINDEXED,
    source UNINDEXED,
    project_id UNINDEXED,
    start_line UNINDEXED,
    end_line UNINDEXED,
    tokenize = 'porter unicode61'
);
"""


async def ensure_vector_tables(db_path: Path) -> Dict[str, Any]:
    """
    Ensure vector storage tables exist
    
    Returns:
        Status dict with table availability info
    """
    status = {
        "vector_tables": False,
        "fts5": False,
        "fts5_error": None,
    }
    
    try:
        async with Storage.connect(db_path) as db:
            # Create vector tables
            await db.executescript(VECTOR_SCHEMA_SQL)
            await db.commit()
            status["vector_tables"] = True
            log.info("vector.tables.created")
            
            # Try to create FTS5 table
            try:
                await db.executescript(FTS5_SCHEMA_SQL)
                await db.commit()
                status["fts5"] = True
                log.info("vector.fts5.created")
            except Exception as e:
                status["fts5_error"] = str(e)
                log.warn("vector.fts5.failed", {"error": str(e)})
        
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


def bm25_rank_to_score(rank: float) -> float:
    """
    Convert BM25 rank to normalized score (0-1)
    
    Args:
        rank: BM25 rank value
        
    Returns:
        Normalized score
    """
    normalized = max(0, rank) if math.isfinite(rank) else 999
    return 1 / (1 + normalized)


async def vector_search(
    db_path: Path,
    project_id: str,
    embedding: List[float],
    max_results: int = 10,
    min_score: float = 0.0,
    sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Perform vector similarity search
    
    Note: This is a basic implementation using Python.
    For production, consider using sqlite-vec extension for better performance.
    
    Args:
        db_path: Database path
        project_id: Project ID to filter
        embedding: Query embedding vector
        max_results: Maximum results to return
        min_score: Minimum similarity score
        sources: Optional list of sources to filter ('memory', 'session')
        
    Returns:
        List of search results
    """
    results = []
    
    try:
        async with Storage.connect(db_path) as db:
            # Build query
            query = """
                SELECT id, path, source, start_line, end_line, text, embedding
                FROM memory_chunks
                WHERE project_id = ? AND embedding IS NOT NULL
            """
            params = [project_id]
            
            if sources:
                placeholders = ",".join("?" * len(sources))
                query += f" AND source IN ({placeholders})"
                params.extend(sources)
            
            # Fetch all chunks with embeddings
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            
            # Calculate similarities
            for row in rows:
                chunk_id, path, source, start_line, end_line, text, embedding_blob = row
                
                # Deserialize embedding (assuming JSON for now)
                try:
                    if isinstance(embedding_blob, str):
                        chunk_embedding = json.loads(embedding_blob)
                    elif isinstance(embedding_blob, bytes):
                        chunk_embedding = json.loads(embedding_blob.decode())
                    else:
                        continue
                    
                    # Calculate cosine similarity
                    score = cosine_similarity(embedding, chunk_embedding)
                    
                    if score >= min_score:
                        results.append({
                            "id": chunk_id,
                            "path": path,
                            "source": source,
                            "start_line": start_line,
                            "end_line": end_line,
                            "text": text,
                            "score": score,
                        })
                except Exception as e:
                    log.warn("vector.search.embedding_error", {
                        "chunk_id": chunk_id,
                        "error": str(e)
                    })
                    continue
            
            # Sort by score descending
            results.sort(key=lambda x: x["score"], reverse=True)
            
            return results[:max_results]
    
    except Exception as e:
        log.error("vector.search.failed", {"error": str(e)})
        raise


def build_fts_query(raw: str) -> Optional[str]:
    """
    Build FTS5 query string from raw text
    
    Extracts alphanumeric tokens and combines with AND.
    
    Args:
        raw: Raw query text
        
    Returns:
        FTS5 query string or None if no valid tokens
    """
    import re
    
    # Extract alphanumeric tokens
    tokens = re.findall(r'[A-Za-z0-9_]+', raw)
    tokens = [t.strip() for t in tokens if t.strip()]
    
    if not tokens:
        return None
    
    # Quote and combine with AND
    quoted = ['"' + t.replace('"', "") + '"' for t in tokens]
    return ' AND '.join(quoted)


async def fts_search(
    db_path: Path,
    project_id: str,
    query: str,
    max_results: int = 10,
    sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Perform FTS5 full-text search
    
    Args:
        db_path: Database path
        project_id: Project ID to filter
        query: Search query (FTS5 format)
        max_results: Maximum results to return
        sources: Optional list of sources to filter
        
    Returns:
        List of search results with BM25 scores
    """
    results = []
    
    try:
        async with Storage.connect(db_path) as db:
            # Build FTS query
            fts_query = build_fts_query(query)
            if not fts_query:
                return []
            
            # Build SQL query
            sql = """
                SELECT 
                    f.chunk_id,
                    f.path,
                    f.source,
                    f.start_line,
                    f.end_line,
                    f.text,
                    rank
                FROM memory_fts f
                WHERE f.text MATCH ?
                    AND f.project_id = ?
            """
            params = [fts_query, project_id]
            
            if sources:
                placeholders = ",".join("?" * len(sources))
                sql += f" AND f.source IN ({placeholders})"
                params.extend(sources)
            
            sql += f" ORDER BY rank LIMIT {max_results}"
            
            # Execute query
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            
            # Convert ranks to scores
            for row in rows:
                chunk_id, path, source, start_line, end_line, text, rank = row
                score = bm25_rank_to_score(rank)
                
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
            - id, path, project_id, source, start_line, end_line,
              hash, text, embedding, embedding_model, embedding_dims
        
    Returns:
        Number of chunks inserted
    """
    try:
        async with Storage.connect(db_path) as db:
            now = datetime.now().timestamp()
            
            # Insert into chunks table
            await db.executemany("""
                INSERT OR REPLACE INTO memory_chunks
                (id, path, project_id, source, start_line, end_line, hash, text,
                 embedding, embedding_model, embedding_dims, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                (
                    chunk["id"],
                    chunk["path"],
                    chunk["project_id"],
                    chunk["source"],
                    chunk["start_line"],
                    chunk["end_line"],
                    chunk["hash"],
                    chunk["text"],
                    json.dumps(chunk["embedding"]) if chunk.get("embedding") else None,
                    chunk.get("embedding_model"),
                    chunk.get("embedding_dims"),
                    now,
                    now,
                )
                for chunk in chunks
            ])
            
            # Insert into FTS5 table (if exists)
            try:
                await db.executemany("""
                    INSERT OR REPLACE INTO memory_fts
                    (chunk_id, path, source, project_id, start_line, end_line, text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        chunk["id"],
                        chunk["path"],
                        chunk["source"],
                        chunk["project_id"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["text"],
                    )
                    for chunk in chunks
                ])
            except Exception as e:
                log.warn("fts.insert.failed", {"error": str(e)})
            
            await db.commit()
            
            log.info("chunks.inserted", {"count": len(chunks)})
            return len(chunks)
    
    except Exception as e:
        log.error("chunks.insert.failed", {"error": str(e)})
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
        async with Storage.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT embedding, dims
                FROM memory_embedding_cache
                WHERE text_hash = ? AND provider = ? AND model = ?
            """, (text_hash, provider, model))
            
            row = await cursor.fetchone()
            if row:
                embedding_blob, dims = row
                embedding = json.loads(embedding_blob) if isinstance(embedding_blob, (str, bytes)) else embedding_blob
                
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
        async with Storage.connect(db_path) as db:
            now = datetime.now().timestamp()
            await db.execute("""
                INSERT OR REPLACE INTO memory_embedding_cache
                (text_hash, provider, model, embedding, dims, created_at, accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                text_hash,
                provider,
                model,
                json.dumps(embedding),
                dims,
                now,
                now,
            ))
            await db.commit()
    except Exception as e:
        log.warn("cache.put.failed", {"error": str(e)})
