"""
File indexer for memory system

Implements incremental indexing of memory files with embedding generation.
"""

from typing import List, Optional, Callable, Dict, Any
from pathlib import Path
from datetime import datetime
import asyncio
import uuid

from flocks.provider import Provider
from flocks.storage import Storage, insert_chunks, get_embedding_from_cache, put_embedding_to_cache
from flocks.memory.types import MemoryFileEntry, MemoryChunk, MemorySyncProgress
from flocks.memory.config import MemoryConfig
from flocks.memory.utils.hash import compute_hash, compute_text_hash
from flocks.memory.utils.text import is_memory_path
from flocks.memory.sync.chunking import TextChunker
from flocks.utils.log import Log

log = Log.create(service="memory.indexer")


class MemoryIndexer:
    """File indexer for memory system"""
    
    def __init__(
        self,
        project_id: str,
        workspace_dir: Path,
        provider_id: str,
        embedding_model: str,
        config: MemoryConfig,
    ):
        """
        Initialize indexer
        
        Args:
            project_id: Project ID
            workspace_dir: Workspace directory
            provider_id: Embedding provider ID
            embedding_model: Embedding model name
            config: Memory configuration
        """
        self.project_id = project_id
        self.workspace_dir = Path(workspace_dir)
        self.provider_id = provider_id
        self.embedding_model = embedding_model
        self.config = config
        self.chunker = TextChunker(config.chunking)
    
    async def sync(
        self,
        force: bool = False,
        progress_callback: Optional[Callable[[MemorySyncProgress], None]] = None,
    ) -> Dict[str, Any]:
        """
        Sync memory files to index
        
        Args:
            force: Force re-index all files
            progress_callback: Optional progress callback
            
        Returns:
            Sync statistics
        """
        log.info("indexer.sync.start", {"project_id": self.project_id, "force": force})
        
        stats = {
            "files_scanned": 0,
            "files_indexed": 0,
            "files_skipped": 0,
            "chunks_created": 0,
            "embeddings_generated": 0,
            "cache_hits": 0,
        }
        
        try:
            content_cache: Dict[str, str] = {}
            memory_files = await self._scan_memory_files(_content_cache=content_cache)
            stats["files_scanned"] = len(memory_files)
            
            if progress_callback:
                progress_callback(MemorySyncProgress(
                    completed=0,
                    total=len(memory_files),
                    label="Scanning files"
                ))
            
            indexed_files = await self._get_indexed_files()
            
            for idx, file_entry in enumerate(memory_files):
                if not force:
                    indexed = indexed_files.get(file_entry.path)
                    if indexed and indexed["hash"] == file_entry.hash:
                        stats["files_skipped"] += 1
                        log.debug("indexer.file.skipped", {"path": file_entry.path})
                        content_cache.pop(file_entry.abs_path, None)
                        continue
                
                result = await self._index_file(file_entry, _content_cache=content_cache)
                stats["files_indexed"] += 1
                stats["chunks_created"] += result["chunks"]
                stats["embeddings_generated"] += result["embeddings"]
                stats["cache_hits"] += result["cache_hits"]
                
                if progress_callback:
                    progress_callback(MemorySyncProgress(
                        completed=idx + 1,
                        total=len(memory_files),
                        label=f"Indexed {file_entry.path}"
                    ))
            
            deleted_count = await self._clean_deleted_files(
                current_files=[f.path for f in memory_files]
            )
            if deleted_count > 0:
                log.info("indexer.cleaned", {"deleted": deleted_count})
            
            log.info("indexer.sync.complete", stats)
            return stats
        
        except Exception as e:
            log.error("indexer.sync.failed", {"error": str(e)})
            raise
    
    async def _scan_memory_files(
        self, *, _content_cache: Optional[Dict[str, str]] = None,
    ) -> List[MemoryFileEntry]:
        """
        Scan workspace for memory files.

        Filesystem I/O (glob, stat, read) is offloaded to a thread to avoid
        blocking the event loop.  When *_content_cache* is passed, file
        contents read during hash calculation are stored there for later
        reuse in ``_index_file``.
        """
        from flocks.config import Config
        
        data_dir = Config.get_data_path()
        memory_root = data_dir / "memory"
        extra_paths = list(self.config.extra_paths)

        def _scan_sync() -> List[MemoryFileEntry]:
            files: List[MemoryFileEntry] = []
            seen: set[str] = set()

            if not memory_root.exists():
                return files

            def _add(fp: Path) -> None:
                resolved = str(fp.resolve())
                if resolved in seen:
                    return
                seen.add(resolved)
                files.append(self._create_file_entry(fp, memory_root, _content_cache=_content_cache))

            for fp in memory_root.glob("**/*.md"):
                if fp.is_file():
                    _add(fp)

            for ep in extra_paths:
                full = memory_root / ep
                if full.exists():
                    if full.is_file():
                        _add(full)
                    elif full.is_dir():
                        for fp in full.glob("**/*.md"):
                            if fp.is_file():
                                _add(fp)
            return files

        files = await asyncio.to_thread(_scan_sync)

        if not files:
            log.debug("indexer.memory_root.not_found", {"path": str(memory_root)})
        else:
            log.debug("indexer.files.scanned", {"count": len(files)})
        return files
    
    def _create_file_entry(
        self, file_path: Path, memory_root: Path, *, _content_cache: Optional[Dict[str, str]] = None,
    ) -> MemoryFileEntry:
        """
        Create file entry from path.

        When *_content_cache* is provided the raw text is stored there keyed
        by absolute path so that ``_index_file`` can reuse it without a second
        disk read (fixes the TOCTOU + double-I/O issue).
        """
        try:
            rel_path = str(file_path.relative_to(memory_root))
        except ValueError:
            rel_path = file_path.name
        
        stat = file_path.stat()
        
        content = file_path.read_text(encoding="utf-8")
        content_hash = compute_text_hash(content)
        
        if _content_cache is not None:
            _content_cache[str(file_path)] = content
        
        return MemoryFileEntry(
            path=rel_path,
            abs_path=str(file_path),
            mtime_ms=stat.st_mtime * 1000,
            size=stat.st_size,
            hash=content_hash,
        )
    
    async def _get_indexed_files(self) -> Dict[str, Dict[str, Any]]:
        """
        Get indexed files from database
        
        Returns:
            Dict mapping path to file info
        """
        import aiosqlite
        
        indexed = {}
        
        try:
            async with Storage.connect(Storage.get_db_path()) as db:
                cursor = await db.execute("""
                    SELECT path, hash, mtime, size
                    FROM memory_files
                    WHERE project_id = ?
                """, (self.project_id,))
                
                rows = await cursor.fetchall()
                for path, hash_val, mtime, size in rows:
                    indexed[path] = {
                        "hash": hash_val,
                        "mtime": mtime,
                        "size": size,
                    }
        except Exception as e:
            log.warn("indexer.get_indexed.failed", {"error": str(e)})
        
        return indexed
    
    async def _index_file(
        self, file_entry: MemoryFileEntry, *, _content_cache: Optional[Dict[str, str]] = None,
    ) -> Dict[str, int]:
        """
        Index a single file.

        If *_content_cache* contains the file content (populated during scan),
        it is reused to avoid a redundant disk read and TOCTOU inconsistency.
        """
        stats = {"chunks": 0, "embeddings": 0, "cache_hits": 0}
        
        try:
            if _content_cache and file_entry.abs_path in _content_cache:
                content = _content_cache.pop(file_entry.abs_path)
            else:
                content = await asyncio.to_thread(
                    Path(file_entry.abs_path).read_text, encoding="utf-8",
                )
            
            # Chunk text
            chunks = self.chunker.chunk_text(content, file_entry.path)
            stats["chunks"] = len(chunks)
            
            if not chunks:
                log.warn("indexer.no_chunks", {"path": file_entry.path})
                return stats
            
            # Generate embeddings for chunks
            chunk_records = []
            
            # Use batch processing if enabled
            if self.config.batch.enabled and len(chunks) > 1:
                chunk_records = await self._generate_embeddings_batch(
                    chunks, file_entry, stats
                )
            else:
                chunk_records = await self._generate_embeddings_sequential(
                    chunks, file_entry, stats
                )
            
            await self._delete_file_chunks(file_entry.path)
            await insert_chunks(Storage.get_db_path(), chunk_records)
            
            # Update file entry in database
            await self._update_file_entry(file_entry)
            
            log.info("indexer.file.indexed", {
                "path": file_entry.path,
                "chunks": stats["chunks"],
                "embeddings": stats["embeddings"],
                "cache_hits": stats["cache_hits"],
            })
            
            return stats
        
        except Exception as e:
            log.error("indexer.file.failed", {"path": file_entry.path, "error": str(e)})
            raise
    
    async def _generate_embeddings_batch(
        self,
        chunks: List[MemoryChunk],
        file_entry: MemoryFileEntry,
        stats: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        """Generate embeddings in batch.

        Uses positional indices instead of text content as map keys to
        avoid collisions when different chunks share the same text (overlap).
        """
        chunk_records = []
        
        texts_to_embed: List[str] = []
        pending_indices: List[int] = []
        
        for idx, chunk in enumerate(chunks):
            text_hash = compute_text_hash(chunk.text)
            
            cached = await self._get_cached_embedding(text_hash)
            if cached:
                embedding, dims = cached
                stats["cache_hits"] += 1
                chunk_records.append(self._create_chunk_record(
                    chunk, file_entry, embedding, dims
                ))
            else:
                texts_to_embed.append(chunk.text)
                pending_indices.append(idx)
        
        if texts_to_embed:
            embeddings = await Provider.embed_batch(
                texts=texts_to_embed,
                provider_id=self.provider_id,
                model=self.embedding_model,
            )
            
            stats["embeddings"] += len(embeddings)
            
            for text, embedding, chunk_idx in zip(texts_to_embed, embeddings, pending_indices):
                chunk = chunks[chunk_idx]
                dims = len(embedding)
                
                chunk_records.append(self._create_chunk_record(
                    chunk, file_entry, embedding, dims
                ))
                
                text_hash = compute_text_hash(text)
                await self._put_cached_embedding(text_hash, embedding, dims)
        
        return chunk_records
    
    async def _generate_embeddings_sequential(
        self,
        chunks: List[MemoryChunk],
        file_entry: MemoryFileEntry,
        stats: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        """Generate embeddings sequentially"""
        chunk_records = []
        
        for chunk in chunks:
            text_hash = compute_text_hash(chunk.text)
            
            # Try cache first
            cached = await self._get_cached_embedding(text_hash)
            if cached:
                embedding, dims = cached
                stats["cache_hits"] += 1
            else:
                # Generate embedding
                embedding = await Provider.embed(
                    text=chunk.text,
                    provider_id=self.provider_id,
                    model=self.embedding_model,
                )
                dims = len(embedding)
                stats["embeddings"] += 1
                
                # Cache it
                await self._put_cached_embedding(text_hash, embedding, dims)
            
            chunk_records.append(self._create_chunk_record(
                chunk, file_entry, embedding, dims
            ))
        
        return chunk_records
    
    def _create_chunk_record(
        self,
        chunk: MemoryChunk,
        file_entry: MemoryFileEntry,
        embedding: List[float],
        dims: int,
    ) -> Dict[str, Any]:
        """Create chunk record for database"""
        return {
            "id": str(uuid.uuid4()),
            "path": file_entry.path,
            "project_id": self.project_id,
            "source": "memory",
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "hash": chunk.hash,
            "text": chunk.text,
            "embedding": embedding,
            "embedding_model": self.embedding_model,
            "embedding_dims": dims,
        }
    
    async def _get_cached_embedding(
        self,
        text_hash: str,
    ) -> Optional[tuple[List[float], int]]:
        """Get embedding from cache"""
        if not self.config.cache.enabled:
            return None
        
        return await get_embedding_from_cache(
            db_path=Storage.get_db_path(),
            text_hash=text_hash,
            provider=self.provider_id,
            model=self.embedding_model,
        )
    
    async def _put_cached_embedding(
        self,
        text_hash: str,
        embedding: List[float],
        dims: int,
    ) -> None:
        """Put embedding to cache"""
        if not self.config.cache.enabled:
            return
        
        await put_embedding_to_cache(
            db_path=Storage.get_db_path(),
            text_hash=text_hash,
            provider=self.provider_id,
            model=self.embedding_model,
            embedding=embedding,
            dims=dims,
        )
    
    async def _delete_file_chunks(self, path: str) -> None:
        """Delete all existing chunks for a file before re-indexing."""
        import aiosqlite
        
        try:
            async with Storage.connect(Storage.get_db_path()) as db:
                await db.execute(
                    "DELETE FROM memory_chunks WHERE project_id = ? AND path = ?",
                    (self.project_id, path),
                )
                await db.commit()
        except Exception as e:
            log.error("indexer.delete_chunks.failed", {"path": path, "error": str(e)})
    
    async def _update_file_entry(self, file_entry: MemoryFileEntry) -> None:
        """Update file entry in database"""
        import aiosqlite
        
        now = datetime.now().timestamp()
        
        try:
            async with Storage.connect(Storage.get_db_path()) as db:
                await db.execute("""
                    INSERT OR REPLACE INTO memory_files
                    (path, project_id, source, hash, mtime, size, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    file_entry.path,
                    self.project_id,
                    "memory",
                    file_entry.hash,
                    file_entry.mtime_ms / 1000,
                    file_entry.size,
                    now,
                ))
                await db.commit()
        except Exception as e:
            log.error("indexer.update_file.failed", {"path": file_entry.path, "error": str(e)})
    
    async def _clean_deleted_files(self, current_files: List[str]) -> int:
        """
        Clean up deleted files from database
        
        Args:
            current_files: List of current file paths
            
        Returns:
            Number of deleted files
        """
        import aiosqlite
        
        try:
            async with Storage.connect(Storage.get_db_path()) as db:
                cursor = await db.execute("""
                    SELECT path FROM memory_files WHERE project_id = ?
                """, (self.project_id,))
                
                indexed_paths = [row[0] for row in await cursor.fetchall()]
                
                # Find deleted files
                deleted = [p for p in indexed_paths if p not in current_files]
                
                if not deleted:
                    return 0
                
                placeholders = ",".join("?" * len(deleted))
                params = (self.project_id, *deleted)
                
                await db.execute(f"""
                    DELETE FROM memory_chunks
                    WHERE project_id = ? AND path IN ({placeholders})
                """, params)
                
                await db.execute(f"""
                    DELETE FROM memory_files
                    WHERE project_id = ? AND path IN ({placeholders})
                """, params)
                
                await db.commit()
                
                return len(deleted)
        
        except Exception as e:
            log.error("indexer.clean.failed", {"error": str(e)})
            return 0
