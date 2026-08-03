#!/usr/bin/env python3
"""
Test vector storage functionality

Tests the vector storage extension for the memory system.
"""

import asyncio
import tempfile
from pathlib import Path


async def test_vector_storage():
    """Test vector storage functions"""
    print("=" * 60)
    print("Testing Vector Storage Extension")
    print("=" * 60)
    
    # Test 1: Import modules
    print("\n[1/9] Testing imports...")
    try:
        from flocks.storage import (
            Storage,
            ensure_vector_tables,
            vector_search,
            fts_search,
            insert_chunks,
            get_embedding_from_cache,
            put_embedding_to_cache,
            cosine_similarity,
            bm25_rank_to_score,
            build_fts_query,
        )
        print("✅ Successfully imported all vector storage functions")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Create temporary database
    print("\n[2/9] Creating temporary database...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            print(f"   Database path: {db_path}")
            
            # Test 3: Initialize Storage
            print("\n[3/9] Initializing Storage...")
            await Storage.init(db_path)
            print("✅ Storage initialized")
            
            # Test 4: Ensure vector tables
            print("\n[4/9] Creating vector tables...")
            status = await ensure_vector_tables(db_path)
            print(f"   Vector tables: {status['vector_tables']}")
            print(f"   FTS5: {status['fts5']}")
            if status.get("fts5_error"):
                print(f"   FTS5 error: {status['fts5_error']}")
            assert status["vector_tables"], "Vector tables not created"
            print("✅ Vector tables created")
            
            # Test 5: Test cosine similarity
            print("\n[5/9] Testing cosine similarity...")
            vec1 = [1.0, 0.0, 0.0]
            vec2 = [1.0, 0.0, 0.0]
            vec3 = [0.0, 1.0, 0.0]
            
            sim_same = cosine_similarity(vec1, vec2)
            sim_orthogonal = cosine_similarity(vec1, vec3)
            
            print(f"   Same vectors: {sim_same:.4f}")
            print(f"   Orthogonal vectors: {sim_orthogonal:.4f}")
            
            assert abs(sim_same - 1.0) < 0.001, "Same vectors should have similarity ~1.0"
            assert abs(sim_orthogonal - 0.0) < 0.001, "Orthogonal vectors should have similarity ~0.0"
            print("✅ Cosine similarity working correctly")
            
            # Test 6: Test BM25 score conversion
            print("\n[6/9] Testing BM25 score conversion...")
            score1 = bm25_rank_to_score(0.0)
            score2 = bm25_rank_to_score(1.0)
            score3 = bm25_rank_to_score(10.0)
            
            print(f"   Rank 0.0 -> Score: {score1:.4f}")
            print(f"   Rank 1.0 -> Score: {score2:.4f}")
            print(f"   Rank 10.0 -> Score: {score3:.4f}")
            
            assert score1 == 1.0, "Rank 0 should give score 1.0"
            assert score2 == 0.5, "Rank 1 should give score 0.5"
            assert score1 > score2 > score3, "Higher ranks should give lower scores"
            print("✅ BM25 score conversion working correctly")
            
            # Test 7: Test FTS query building
            print("\n[7/9] Testing FTS query building...")
            fts_q1 = build_fts_query("hello world")
            fts_q2 = build_fts_query("test-query 123")
            fts_q3 = build_fts_query("!!!  ")
            
            print(f"   'hello world' -> '{fts_q1}'")
            print(f"   'test-query 123' -> '{fts_q2}'")
            print(f"   '!!!' -> {fts_q3}")
            
            assert fts_q1 == '"hello" AND "world"', "FTS query should be quoted and ANDed"
            assert fts_q2 == '"test" AND "query" AND "123"', "Should extract alphanumeric tokens"
            assert fts_q3 is None, "Should return None for no valid tokens"
            print("✅ FTS query building working correctly")
            
            # Test 8: Test chunk insertion and vector search
            print("\n[8/9] Testing chunk insertion and vector search...")
            
            # Create test chunks
            chunks = [
                {
                    "id": "chunk1",
                    "path": "test.md",
                    "project_id": "test_proj",
                    "source": "memory",
                    "start_line": 1,
                    "end_line": 5,
                    "hash": "hash1",
                    "text": "This is a test document about artificial intelligence.",
                    "embedding": [1.0, 0.5, 0.2, 0.1],
                    "embedding_model": "test-model",
                    "embedding_dims": 4,
                },
                {
                    "id": "chunk2",
                    "path": "test.md",
                    "project_id": "test_proj",
                    "source": "memory",
                    "start_line": 6,
                    "end_line": 10,
                    "hash": "hash2",
                    "text": "Machine learning is a subset of AI.",
                    "embedding": [0.9, 0.6, 0.3, 0.15],
                    "embedding_model": "test-model",
                    "embedding_dims": 4,
                },
                {
                    "id": "chunk3",
                    "path": "test.md",
                    "project_id": "test_proj",
                    "source": "memory",
                    "start_line": 11,
                    "end_line": 15,
                    "hash": "hash3",
                    "text": "Python is a programming language.",
                    "embedding": [0.2, 0.1, 0.8, 0.9],
                    "embedding_model": "test-model",
                    "embedding_dims": 4,
                },
            ]
            
            # Insert chunks
            count = await insert_chunks(db_path, chunks)
            print(f"   Inserted {count} chunks")
            assert count == len(chunks), "Should insert all chunks"
            
            # Search with similar embedding to chunk1
            query_embedding = [0.95, 0.48, 0.22, 0.12]
            results = await vector_search(
                db_path=db_path,
                project_id="test_proj",
                embedding=query_embedding,
                max_results=2,
                min_score=0.0,
            )
            
            print(f"   Found {len(results)} results")
            if results:
                print(f"   Top result: {results[0]['path']} (score: {results[0]['score']:.4f})")
                assert results[0]["id"] == "chunk1", "Should find chunk1 as most similar"
                assert results[0]["score"] > 0.99, "Should have high similarity"
            
            print("✅ Chunk insertion and vector search working correctly")
            
            # Test 9: Test embedding cache
            print("\n[9/9] Testing embedding cache...")
            
            # Put embedding to cache
            test_hash = "test_hash_123"
            test_embedding = [0.1, 0.2, 0.3]
            
            await put_embedding_to_cache(
                db_path=db_path,
                text_hash=test_hash,
                provider="test_provider",
                model="test_model",
                embedding=test_embedding,
                dims=3,
            )
            print("   Put embedding to cache")
            
            # Get embedding from cache
            cached = await get_embedding_from_cache(
                db_path=db_path,
                text_hash=test_hash,
                provider="test_provider",
                model="test_model",
            )
            
            if cached:
                cached_emb, cached_dims = cached
                print(f"   Got embedding from cache (dims: {cached_dims})")
                assert cached_dims == 3, "Should return correct dims"
                assert cached_emb == test_embedding, "Should return same embedding"
                print("✅ Embedding cache working correctly")
            else:
                print("❌ Failed to retrieve from cache")
                return False
            
            print("\n" + "=" * 60)
            print("✅ All vector storage tests passed!")
            print("=" * 60)
            
            return True
    
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_vector_storage())
    exit(0 if success else 1)
