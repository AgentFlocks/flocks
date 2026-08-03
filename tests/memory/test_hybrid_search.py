#!/usr/bin/env python3
"""
Test hybrid search functionality

Tests the hybrid search engine combining vector and keyword search.
"""

import asyncio
import tempfile
from pathlib import Path


async def test_hybrid_search():
    """Test hybrid search engine"""
    print("=" * 60)
    print("Testing Hybrid Search Engine")
    print("=" * 60)
    
    # Test 1: Import modules
    print("\n[1/5] Testing imports...")
    try:
        from flocks.memory.search import HybridSearch
        from flocks.memory.search.hybrid import decorate_citations, format_citation
        from flocks.memory import MemoryQueryConfig, MemorySearchResult, MemorySource
        print("✅ Successfully imported search modules")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Test citation formatting
    print("\n[2/5] Testing citation formatting...")
    try:
        result1 = MemorySearchResult(
            path="MEMORY.md",
            start_line=10,
            end_line=15,
            score=0.85,
            snippet="Test snippet",
            source=MemorySource.MEMORY,
        )
        
        result2 = MemorySearchResult(
            path="memory/2024-01-01.md",
            start_line=5,
            end_line=5,
            score=0.92,
            snippet="Single line",
            source=MemorySource.MEMORY,
        )
        
        citation1 = format_citation(result1)
        citation2 = format_citation(result2)
        
        print(f"   Multi-line: {citation1}")
        print(f"   Single-line: {citation2}")
        
        assert citation1 == "MEMORY.md#L10-L15", "Should format multi-line citation"
        assert citation2 == "memory/2024-01-01.md#L5", "Should format single-line citation"
        
        print("✅ Citation formatting working correctly")
    except Exception as e:
        print(f"❌ Citation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Test citation decoration
    print("\n[3/5] Testing citation decoration...")
    try:
        results = [
            MemorySearchResult(
                path="test.md",
                start_line=1,
                end_line=5,
                score=0.9,
                snippet="Original snippet",
                source=MemorySource.MEMORY,
            )
        ]
        
        # Test with citations on
        decorated_on = decorate_citations(results, mode="on")
        print(f"   Mode 'on': citation added")
        assert decorated_on[0].citation is not None, "Should have citation"
        assert "Source:" in decorated_on[0].snippet, "Should have source in snippet"
        
        # Test with citations off
        decorated_off = decorate_citations(results, mode="off")
        print(f"   Mode 'off': citation removed")
        assert decorated_off[0].snippet == "Original snippet", "Should keep original snippet"
        
        print("✅ Citation decoration working correctly")
    except Exception as e:
        print(f"❌ Citation decoration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Test search engine initialization
    print("\n[4/5] Testing search engine initialization...")
    try:
        config = MemoryQueryConfig(
            max_results=10,
            min_score=0.6,
        )
        
        search_engine = HybridSearch(
            project_id="test_proj",
            provider_id="openai",
            embedding_model="text-embedding-3-small",
            config=config,
        )
        
        print(f"   Project ID: {search_engine.project_id}")
        print(f"   Provider: {search_engine.provider_id}")
        print(f"   Model: {search_engine.embedding_model}")
        print(f"   Hybrid enabled: {search_engine.config.hybrid.enabled}")
        print(f"   Vector weight: {search_engine.config.hybrid.vector_weight}")
        print(f"   Text weight: {search_engine.config.hybrid.text_weight}")
        
        assert search_engine.config.hybrid.enabled, "Hybrid should be enabled by default"
        
        print("✅ Search engine initialization working correctly")
    except Exception as e:
        print(f"❌ Search engine init test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Test result merging logic
    print("\n[5/5] Testing result merging logic...")
    try:
        config = MemoryQueryConfig()
        search_engine = HybridSearch(
            project_id="test",
            provider_id="openai",
            embedding_model="test",
            config=config,
        )
        
        # Create test results
        vector_results = [
            MemorySearchResult(
                path="test.md",
                start_line=1,
                end_line=5,
                score=0.9,
                snippet="Vector match",
                source=MemorySource.MEMORY,
            ),
            MemorySearchResult(
                path="test.md",
                start_line=10,
                end_line=15,
                score=0.7,
                snippet="Another vector match",
                source=MemorySource.MEMORY,
            ),
        ]
        
        keyword_results = [
            MemorySearchResult(
                path="test.md",
                start_line=1,
                end_line=5,
                score=0.8,  # Also found by keyword
                snippet="Keyword match (better snippet)",
                source=MemorySource.MEMORY,
            ),
            MemorySearchResult(
                path="other.md",
                start_line=20,
                end_line=25,
                score=0.6,
                snippet="Keyword only match",
                source=MemorySource.MEMORY,
            ),
        ]
        
        merged = search_engine._merge_results(vector_results, keyword_results)
        
        print(f"   Vector results: {len(vector_results)}")
        print(f"   Keyword results: {len(keyword_results)}")
        print(f"   Merged results: {len(merged)}")
        
        # Should have 3 unique chunks
        assert len(merged) == 3, "Should have 3 unique chunks"
        
        # First result should combine both scores
        first = next(r for r in merged if r.path == "test.md" and r.start_line == 1)
        expected_score = 0.7 * 0.9 + 0.3 * 0.8  # vector_weight * v_score + text_weight * k_score
        print(f"   First result score: {first.score:.4f} (expected: {expected_score:.4f})")
        assert abs(first.score - expected_score) < 0.01, "Should combine scores correctly"
        
        # Snippet should prefer keyword match
        assert "Keyword match" in first.snippet, "Should use keyword snippet"
        
        print("✅ Result merging working correctly")
    except Exception as e:
        print(f"❌ Result merging test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 60)
    print("✅ All hybrid search tests passed!")
    print("=" * 60)
    print("\n📋 Hybrid search engine ready:")
    print("   - Vector similarity search")
    print("   - BM25 keyword search")
    print("   - Weighted result merging")
    print("   - Citation formatting")
    print("   - Configurable weights")
    
    print("\n⚠️  Note: Full search test with embeddings requires:")
    print("   - Indexed memory files in database")
    print("   - API keys for embedding generation")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_hybrid_search())
    exit(0 if success else 1)
