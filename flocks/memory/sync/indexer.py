"""
File indexer for memory system

Implements incremental indexing of memory files with embedding generation.
"""

from typing import List, Optional, Callable, Dict, Any
from pathlib import Path
import asyncio
import math
import uuid

from flocks.db.retry import retry_deadlock_once
from flocks.provider import Provider
from flocks.storage import (
    Storage,
    get_embedding_from_cache,
    put_embedding_to_cache,
    replace_memory_file_index,
)
from flocks.memory.types import MemoryFileEntry, MemoryChunk, MemorySyncProgress
from flocks.memory.config import MemoryConfig
from flocks.memory.paths import classify_memory_path
from flocks.memory.utils.hash import compute_text_hash
from flocks.memory.sync.chunking import TextChunker
from flocks.memory.sync.ownership import MemoryIndexOwnership, MemoryOwnershipConflict
from flocks.utils.log import Log

log = Log.create(service="memory.indexer")

#: How long a sync waits for another instance's indexing of the shared tree.
_INDEX_LOCK_WAIT_S = 15.0


class MemoryIndexer:
    """File indexer for memory system"""
    
    def __init__(
        self,
        project_id: str,
        workspace_dir: Path,
        provider_id: Optional[str],
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
        self._ownership: MemoryIndexOwnership | None = None
    
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
            from flocks.config import Config
            from flocks.db.singleton import cluster_lock
            from flocks.memory.sync.mirror import reconcile_all

            memory_root = Config.get_data_path() / "memory"
            # The trees are copies of one shared tree, indexed under one owner:
            # one instance indexes at a time, the others leave it to it.
            async with cluster_lock("memory.index", wait_s=_INDEX_LOCK_WAIT_S) as held:
                if not held:
                    stats["skipped"] = "another_instance_indexing"
                    log.info("indexer.sync.skipped", {"reason": "another_instance_indexing"})
                    return stats
                # Notes written through other instances first, so the scan
                # below indexes the tree every instance has -- settled now,
                # after the lock: a run that waited for another instance's
                # must index what that one left, never what was here before.
                settled = await reconcile_all(memory_root)
                if settled is None:
                    # The store could not be reached: this tree may be behind
                    # the other instances', and the index is shared -- leave
                    # it as it is rather than publish a stale tree into it.
                    stats["skipped"] = "memory_store_unreachable"
                    log.info("indexer.sync.skipped", {"reason": "memory_store_unreachable"})
                    return stats
                # Notes the settling could not align (changed in the store
                # meanwhile): this copy is not the shared one, so it neither
                # replaces their index rows nor has them cleaned.
                unsettled = set(self._unsettled_keys(memory_root, settled, deferred_only=True))
                self._ownership = await MemoryIndexOwnership.begin(memory_root, shared=True)
                content_cache: Dict[str, str] = {}
                indexed_files = await self._get_indexed_files()
                memory_files = await self._scan_memory_files(
                    _content_cache=content_cache,
                    _indexed_files=None if force else indexed_files,
                )
                if memory_files and self._ownership is None:
                    raise MemoryOwnershipConflict("Memory root appeared during this scan; retry with a stable root")
                stats["files_scanned"] = len(memory_files)
            
                if progress_callback:
                    progress_callback(MemorySyncProgress(
                        completed=0,
                        total=len(memory_files),
                        label="Scanning files"
                    ))
            
                for idx, file_entry in enumerate(memory_files):
                    if (file_entry.scope.value, file_entry.scope_id, file_entry.path) in unsettled:
                        stats["files_skipped"] += 1
                        stats["files_unsettled"] = stats.get("files_unsettled", 0) + 1
                        content_cache.pop(file_entry.abs_path, None)
                        continue
                    if not force:
                        indexed = indexed_files.get(
                            (
                                file_entry.scope.value,
                                file_entry.scope_id,
                                file_entry.path,
                            )
                        )
                        if indexed and indexed["hash"] == file_entry.hash:
                            if not self._metadata_matches(file_entry, indexed):
                                await self._update_file_metadata(file_entry)
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
            
                # Only a tree settled with the store this time is complete
                # enough to take a missing note for a deleted one -- and not
                # the notes it could not settle, or that sit behind a link
                # here: those may exist elsewhere.
                deleted_count = await self._clean_deleted_files(
                    current_files=[
                        (file.scope.value, file.scope_id, file.path)
                        for file in memory_files
                    ] + self._unsettled_keys(memory_root, settled)
                )
                if deleted_count > 0:
                    log.info("indexer.cleaned", {"deleted": deleted_count})
            
                log.info("indexer.sync.complete", stats)
                return stats
        
        except Exception as e:
            log.error("indexer.sync.failed", {"error": str(e)})
            raise
    
    async def _scan_memory_files(
        self,
        *,
        _content_cache: Optional[Dict[str, str]] = None,
        _indexed_files: Optional[
            Dict[tuple[str, str, str], Dict[str, Any]]
        ] = None,
    ) -> List[MemoryFileEntry]:
        """
        Scan workspace for memory files.

        Filesystem I/O is offloaded to a thread to avoid blocking the event
        loop. When *_indexed_files* is passed, files whose mtime and size still
        match the stored manifest reuse the stored content hash without being
        read. New or changed files are read once, hashed, and cached for
        ``_index_file``.
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
                classified = classify_memory_path(memory_root, fp)
                if classified is None:
                    return
                seen.add(resolved)
                scope, scope_id, rel_path = classified
                indexed_file = (
                    _indexed_files.get((scope.value, scope_id, rel_path))
                    if _indexed_files is not None
                    else None
                )
                files.append(
                    self._create_file_entry(
                        fp,
                        memory_root,
                        _content_cache=_content_cache,
                        _indexed_file=indexed_file,
                    )
                )

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
        self,
        file_path: Path,
        memory_root: Path,
        *,
        _content_cache: Optional[Dict[str, str]] = None,
        _indexed_file: Optional[Dict[str, Any]] = None,
    ) -> MemoryFileEntry:
        """
        Create file entry from path.

        An unchanged indexed file reuses its stored hash based on mtime and
        size. Otherwise the raw text is read and optionally cached by absolute
        path so that ``_index_file`` can reuse it without a second disk read.
        """
        classified = classify_memory_path(memory_root, file_path)
        if classified is None:
            raise ValueError(f"Unsupported Memory index path: {file_path}")
        scope, scope_id, rel_path = classified
        
        stat = file_path.stat()
        if (
            _indexed_file is not None
            and math.isclose(
                _indexed_file["mtime"],
                stat.st_mtime,
                rel_tol=0,
                abs_tol=1e-6,
            )
            and _indexed_file["size"] == stat.st_size
        ):
            content_hash = str(_indexed_file["hash"])
        else:
            content = file_path.read_text(encoding="utf-8")
            content_hash = compute_text_hash(content)
            if _content_cache is not None:
                _content_cache[str(file_path)] = content
        
        return MemoryFileEntry(
            scope=scope,
            scope_id=scope_id,
            path=rel_path,
            abs_path=str(file_path),
            mtime_ms=stat.st_mtime * 1000,
            size=stat.st_size,
            hash=content_hash,
        )

    @staticmethod
    def _metadata_matches(
        file_entry: MemoryFileEntry,
        indexed_file: Dict[str, Any],
    ) -> bool:
        """Return whether current file metadata matches the stored manifest."""
        return (
            math.isclose(
                indexed_file["mtime"],
                file_entry.mtime_ms / 1000,
                rel_tol=0,
                abs_tol=1e-6,
            )
            and indexed_file["size"] == file_entry.size
        )

    async def _update_file_metadata(self, file_entry: MemoryFileEntry) -> None:
        """Refresh metadata after a content-preserving filesystem change."""
        await retry_deadlock_once(
            lambda: self._update_file_metadata_once(file_entry), name="memory.update_file_metadata",
        )

    async def _update_file_metadata_once(self, file_entry: MemoryFileEntry) -> None:
        async with Storage.transaction(Storage.get_db_path()) as db:
            await db.execute("SELECT pg_advisory_xact_lock(hashtext(?))",
                             (f"memory:{file_entry.scope.value}:{file_entry.scope_id}:{file_entry.path}",))
            if self._ownership is not None:
                await self._ownership.claim(db, {
                    "scope": file_entry.scope.value, "scope_id": file_entry.scope_id,
                    "path": file_entry.path, "hash": file_entry.hash,
                    "mtime": file_entry.mtime_ms / 1000, "size": file_entry.size,
                })
            await db.execute(
                """
                UPDATE memory_files
                SET mtime = ?, size = ?
                WHERE scope = ? AND scope_id = ? AND path = ?
                    AND source = 'memory' AND hash = ?
                """,
                (
                    file_entry.mtime_ms / 1000,
                    file_entry.size,
                    file_entry.scope.value,
                    file_entry.scope_id,
                    file_entry.path,
                    file_entry.hash,
                ),
            )
            await db.commit()
    
    async def _get_indexed_files(
        self,
    ) -> Dict[tuple[str, str, str], Dict[str, Any]]:
        """
        Get indexed files from database
        
        Returns:
            Dict mapping path to file info
        """
        indexed: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        if self._ownership is None:
            return indexed
        
        try:
            async with Storage.transaction(Storage.get_db_path()) as db:
                cursor = await db.execute("""
                    SELECT f.scope, f.scope_id, f.path, f.hash, o.mtime, o.size
                    FROM memory_files f JOIN memory_index_owners o USING(scope,scope_id,path)
                    WHERE f.source = 'memory' AND o.root_id=? AND o.hash=f.hash
                """, (self._ownership.root_id,))
                
                rows = await cursor.fetchall()
                for scope, scope_id, path, hash_val, mtime, size in rows:
                    indexed[(scope, scope_id, path)] = {
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

            # FTS indexing is always available. Embeddings are optional.
            chunk_records: List[Dict[str, Any]] = []
            if not chunks:
                log.debug("indexer.no_chunks", {"path": file_entry.path})
            elif self.provider_id is None:
                chunk_records = [
                    self._create_chunk_record(chunk, file_entry, None, None)
                    for chunk in chunks
                ]
            else:
                try:
                    if self.config.batch.enabled and len(chunks) > 1:
                        chunk_records = await self._generate_embeddings_batch(
                            chunks, file_entry, stats
                        )
                    else:
                        chunk_records = await self._generate_embeddings_sequential(
                            chunks, file_entry, stats
                        )
                except Exception as exc:
                    log.warn(
                        "indexer.embedding.failed_fts_fallback",
                        {"path": file_entry.path, "error": str(exc)},
                    )
                    chunk_records = [
                        self._create_chunk_record(chunk, file_entry, None, None)
                        for chunk in chunks
                    ]
            
            await replace_memory_file_index(
                Storage.get_db_path(),
                file_entry={
                    "scope": file_entry.scope.value,
                    "scope_id": file_entry.scope_id,
                    "path": file_entry.path,
                    "hash": file_entry.hash,
                    "mtime": file_entry.mtime_ms / 1000,
                    "size": file_entry.size,
                },
                chunks=chunk_records,
                before_replace=self._ownership.claim if self._ownership is not None else None,
            )
            
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
            from flocks.memory.embedding import embed_many

            embeddings = await embed_many(
                texts_to_embed,
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
                from flocks.memory.embedding import embed_one

                embedding = await embed_one(
                    chunk.text,
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
        embedding: Optional[List[float]],
        dims: Optional[int],
    ) -> Dict[str, Any]:
        """Create chunk record for database"""
        return {
            "id": str(uuid.uuid4()),
            "scope": file_entry.scope.value,
            "scope_id": file_entry.scope_id,
            "path": file_entry.path,
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
        if not self.config.cache.enabled or self.provider_id is None:
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
        if not self.config.cache.enabled or self.provider_id is None:
            return
        
        await put_embedding_to_cache(
            db_path=Storage.get_db_path(),
            text_hash=text_hash,
            provider=self.provider_id,
            model=self.embedding_model,
            embedding=embedding,
            dims=dims,
        )
    
    @staticmethod
    def _unsettled_keys(memory_root: Path, settled, *, deferred_only: bool = False) -> List[tuple[str, str, str]]:
        """Index keys of the notes the last settling left alone (deferred, or behind a link)."""
        keys: List[tuple[str, str, str]] = []
        for relative in [*settled.deferred, *([] if deferred_only else settled.linked)]:
            classified = classify_memory_path(memory_root, memory_root / relative)
            if classified is not None:
                scope, scope_id, rel_path = classified
                keys.append((scope.value, scope_id, rel_path))
        return keys

    async def _clean_deleted_files(
        self,
        current_files: List[tuple[str, str, str]],
    ) -> int:
        """
        Clean up deleted files from database
        
        Args:
            current_files: List of current file paths
            
        Returns:
            Number of deleted files
        """
        if self._ownership is None:
            return 0
        return await self._ownership.clean(current_files)
