"""
Hybrid search engine for memory system

Combines vector similarity search and BM25 keyword search.
Based on OpenClaw's hybrid search algorithm.
"""

from typing import List, Optional
import asyncio

from flocks.provider import Provider
from flocks.storage import Storage, vector_search, fts_search
from flocks.memory.types import MemorySearchResult, MemorySource
from flocks.memory.config import MemoryQueryConfig
from flocks.memory.utils.text import extract_snippet
from flocks.utils.log import Log

log = Log.create(service="memory.search")


class HybridSearch:
    """Hybrid search engine combining vector and keyword search"""
    
    def __init__(
        self,
        project_id: str,
        provider_id: Optional[str],
        embedding_model: str,
        config: MemoryQueryConfig,
    ):
        """
        Initialize hybrid search engine
        
        Args:
            project_id: Project ID
            provider_id: Embedding provider ID
            embedding_model: Embedding model name
            config: Query configuration
        """
        self.project_id = project_id
        self.provider_id = provider_id
        self.embedding_model = embedding_model
        self.config = config
    
    async def search(
        self,
        query: str,
        max_results: int,
        min_score: float,
        sources: List[MemorySource],
    ) -> List[MemorySearchResult]:
        """
        Execute hybrid search
        
        Args:
            query: Search query
            max_results: Maximum results to return
            min_score: Minimum similarity score
            sources: Sources to search
            
        Returns:
            List of search results
        """
        log.debug("search.start", {
            "query": query[:100],
            "max_results": max_results,
            "min_score": min_score,
            "sources": [s.value for s in sources],
        })
        
        try:
            if self.provider_id is None:
                results = await self._keyword_search(
                    query=query,
                    max_results=max_results,
                    sources=sources,
                )
                return [
                    result
                    for result in results
                    if result.score >= min_score
                ][:max_results]

            if not self.config.hybrid.enabled:
                try:
                    return await self._vector_search(
                        query=query,
                        max_results=max_results,
                        min_score=min_score,
                        sources=sources,
                    )
                except Exception as exc:
                    log.warn(
                        "search.vector.failed_fts_fallback",
                        {"error": str(exc)},
                    )
                    results = await self._keyword_search(
                        query=query,
                        max_results=max_results,
                        sources=sources,
                    )
                    return [
                        result
                        for result in results
                        if result.score >= min_score
                    ][:max_results]
            
            # Hybrid search: parallel vector + keyword search
            candidate_limit = max_results * self.config.hybrid.candidate_multiplier
            
            vector_results, keyword_results = await asyncio.gather(
                self._vector_search(
                    query=query,
                    max_results=candidate_limit,
                    min_score=0.0,  # Don't filter yet, merge first
                    sources=sources,
                ),
                self._keyword_search(
                    query=query,
                    max_results=candidate_limit,
                    sources=sources,
                ),
                return_exceptions=True,
            )
            
            # Handle exceptions
            vector_failed = isinstance(vector_results, Exception)
            keyword_failed = isinstance(keyword_results, Exception)
            if vector_failed:
                log.warn("search.vector.failed", {"error": str(vector_results)})
                vector_results = []
            
            if keyword_failed:
                log.warn("search.keyword.failed", {"error": str(keyword_results)})
                keyword_results = []

            if vector_failed and keyword_failed:
                raise RuntimeError("Both vector and keyword search failed")

            if keyword_results and not vector_results:
                return [
                    result
                    for result in keyword_results
                    if result.score >= min_score
                ][:max_results]
            if vector_results and not keyword_results:
                return [
                    result
                    for result in vector_results
                    if result.score >= min_score
                ][:max_results]
            
            # Merge results
            merged = self._merge_results(
                vector_results=vector_results,
                keyword_results=keyword_results,
            )
            
            # Filter by min_score and limit
            filtered = [r for r in merged if r.score >= min_score]
            filtered.sort(key=lambda r: r.score, reverse=True)
            
            results = filtered[:max_results]
            
            log.info("search.complete", {
                "query": query[:100],
                "vector_count": len(vector_results),
                "keyword_count": len(keyword_results),
                "merged_count": len(merged),
                "final_count": len(results),
            })
            
            return results
        
        except Exception as e:
            log.error("search.failed", {"error": str(e)})
            raise
    
    async def _vector_search(
        self,
        query: str,
        max_results: int,
        min_score: float,
        sources: List[MemorySource],
    ) -> List[MemorySearchResult]:
        """Execute vector similarity search"""
        try:
            if self.provider_id is None:
                raise RuntimeError("Embedding provider is not configured")

            # Generate query embedding
            query_embedding = await Provider.embed(
                text=query,
                provider_id=self.provider_id,
                model=self.embedding_model,
            )
            
            # Search database
            raw_results = await vector_search(
                db_path=Storage.get_db_path(),
                project_id=self.project_id,
                embedding=query_embedding,
                max_results=max_results,
                min_score=min_score,
                sources=[s.value for s in sources],
            )
            
            # Convert to MemorySearchResult
            results = []
            for r in raw_results:
                results.append(MemorySearchResult(
                    path=r["path"],
                    start_line=r["start_line"],
                    end_line=r["end_line"],
                    score=r["score"],
                    snippet=r["text"][:700],  # Truncate to max snippet length
                    source=MemorySource(r["source"]),
                ))
            
            return results
        
        except Exception as e:
            log.error("search.vector.failed", {"error": str(e)})
            raise
    
    async def _keyword_search(
        self,
        query: str,
        max_results: int,
        sources: List[MemorySource],
    ) -> List[MemorySearchResult]:
        """Execute FTS5 keyword search"""
        try:
            # Search database
            raw_results = await fts_search(
                db_path=Storage.get_db_path(),
                project_id=self.project_id,
                query=query,
                max_results=max_results,
                sources=[s.value for s in sources],
            )
            
            # Convert to MemorySearchResult
            results = []
            for r in raw_results:
                results.append(MemorySearchResult(
                    path=r["path"],
                    start_line=r["start_line"],
                    end_line=r["end_line"],
                    score=r["score"],
                    snippet=r["text"][:700],  # Truncate to max snippet length
                    source=MemorySource(r["source"]),
                ))
            
            return results
        
        except Exception as e:
            log.error("search.keyword.failed", {"error": str(e)})
            raise
    
    def _merge_results(
        self,
        vector_results: List[MemorySearchResult],
        keyword_results: List[MemorySearchResult],
    ) -> List[MemorySearchResult]:
        """
        Merge vector and keyword search results.

        Keyword (BM25 / FTS5) scores are min-max normalised to [0, 1] before
        the weighted combination so that they are on the same scale as vector
        cosine-similarity scores.
        """
        # Normalise keyword scores to 0-1
        if keyword_results:
            raw_scores = [r.score for r in keyword_results]
            kw_min = min(raw_scores)
            kw_max = max(raw_scores)
            kw_range = kw_max - kw_min
            normalised_keyword: List[tuple[MemorySearchResult, float]] = []
            for r in keyword_results:
                norm = (r.score - kw_min) / kw_range if kw_range > 0 else 1.0
                normalised_keyword.append((r, norm))
        else:
            normalised_keyword = []

        by_id: dict[str, dict] = {}
        
        for r in vector_results:
            chunk_id = self._make_chunk_id(r)
            by_id[chunk_id] = {
                "result": r,
                "vector_score": r.score,
                "text_score": 0.0,
            }
        
        for r, norm_score in normalised_keyword:
            chunk_id = self._make_chunk_id(r)
            if chunk_id in by_id:
                by_id[chunk_id]["text_score"] = norm_score
                if r.snippet:
                    by_id[chunk_id]["result"] = by_id[chunk_id]["result"].model_copy(
                        update={"snippet": r.snippet}
                    )
            else:
                by_id[chunk_id] = {
                    "result": r,
                    "vector_score": 0.0,
                    "text_score": norm_score,
                }
        
        merged = []
        for item in by_id.values():
            final_score = (
                self.config.hybrid.vector_weight * item["vector_score"] +
                self.config.hybrid.text_weight * item["text_score"]
            )
            result = item["result"].model_copy(update={"score": final_score})
            merged.append(result)
        
        return merged
    
    def _make_chunk_id(self, result: MemorySearchResult) -> str:
        """Generate unique chunk ID for deduplication"""
        return f"{result.path}:{result.start_line}:{result.end_line}"


def decorate_citations(
    results: List[MemorySearchResult],
    mode: str = "auto",
) -> List[MemorySearchResult]:
    """
    Add citations to search results.

    Only populates the ``citation`` field; ``snippet`` is left untouched
    so consumers always receive the original text content.
    
    Args:
        results: Search results
        mode: Citation mode ('on', 'off', 'auto')
        
    Returns:
        Results with citation field populated
    """
    if mode == "off":
        return results
    
    decorated = []
    for result in results:
        citation = result.citation or format_citation(result)
        decorated.append(result.model_copy(update={"citation": citation}))
    
    return decorated


def format_citation(result: MemorySearchResult) -> str:
    """Format citation for a search result"""
    if result.start_line == result.end_line:
        line_range = f"#L{result.start_line}"
    else:
        line_range = f"#L{result.start_line}-L{result.end_line}"
    
    return f"{result.path}{line_range}"
