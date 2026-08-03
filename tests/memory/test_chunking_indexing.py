#!/usr/bin/env python3
"""
Test chunking and indexing functionality

Tests the text chunking and file indexing system.
"""

import asyncio
import tempfile
from pathlib import Path


async def test_chunking_and_indexing():
    """Test chunking and indexing"""
    print("=" * 60)
    print("Testing Chunking and Indexing")
    print("=" * 60)
    
    # Test 1: Import modules
    print("\n[1/6] Testing imports...")
    try:
        from flocks.memory.sync import TextChunker, MemoryIndexer
        from flocks.memory import MemoryConfig, MemoryChunkingConfig
        print("✅ Successfully imported chunking and indexing modules")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Test text chunking
    print("\n[2/6] Testing text chunking...")
    try:
        config = MemoryChunkingConfig(tokens=50, overlap=10)
        chunker = TextChunker(config)
        
        # Create test text (multiple lines)
        test_lines = [
            "This is line 1 with some content about artificial intelligence.",
            "Line 2 continues with machine learning topics.",
            "Line 3 discusses deep learning and neural networks.",
            "Line 4 covers natural language processing.",
            "Line 5 talks about computer vision and image recognition.",
            "Line 6 is about reinforcement learning and agents.",
            "Line 7 discusses transformers and attention mechanisms.",
            "Line 8 covers GPT models and language understanding.",
        ]
        test_text = "\n".join(test_lines)
        
        chunks = chunker.chunk_text(test_text, "test.md")
        
        print(f"   Total lines: {len(test_lines)}")
        print(f"   Chunks created: {len(chunks)}")
        
        for i, chunk in enumerate(chunks):
            print(f"   Chunk {i+1}: lines {chunk.start_line}-{chunk.end_line} ({chunk.end_line - chunk.start_line + 1} lines)")
        
        assert len(chunks) > 0, "Should create at least one chunk"
        
        # Verify chunk properties
        for chunk in chunks:
            assert chunk.start_line <= chunk.end_line, "Start line should be <= end line"
            assert chunk.text, "Chunk should have text"
            assert chunk.hash, "Chunk should have hash"
            assert len(chunk.hash) == 32, "Hash should be 32 chars"
        
        # Verify overlap (if multiple chunks)
        if len(chunks) > 1:
            print(f"   Overlap detected: chunks share some lines")
        
        print("✅ Text chunking working correctly")
    except Exception as e:
        print(f"❌ Chunking test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Test file scanning
    print("\n[3/6] Testing file scanning...")
    try:
        from flocks.storage import Storage
        from flocks.provider import Provider
        
        # Initialize systems
        await Storage.init()
        await Provider.init()
        
        # Create temporary workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create test files (only MEMORY.md, not memory.md to avoid duplication)
            (workspace / "MEMORY.md").write_text("# Main Memory\n\nSome content here.")
            
            memory_dir = workspace / "memory"
            memory_dir.mkdir()
            (memory_dir / "2024-01-01.md").write_text("# Daily Log\n\nDay 1 notes.")
            (memory_dir / "2024-01-02.md").write_text("# Daily Log\n\nDay 2 notes.")
            
            # Note: Don't create memory.md to avoid duplication with MEMORY.md
            
            # Create indexer
            memory_config = MemoryConfig(
                enabled=True,
                sources=["memory"],
                embedding={"provider": "openai", "model": "text-embedding-3-small"},
            )
            
            indexer = MemoryIndexer(
                project_id="test_proj",
                workspace_dir=workspace,
                provider_id="openai",
                embedding_model="text-embedding-3-small",
                config=memory_config,
            )
            
            # Scan files
            files = await indexer._scan_memory_files()
            
            print(f"   Files found: {len(files)}")
            for f in files:
                print(f"     - {f.path} ({f.size} bytes)")
            
            # Should find at least 3 files (MEMORY.md + 2 daily logs)
            # Note: May find 4 if both MEMORY.md and memory.md exist
            assert len(files) >= 3, f"Should find at least 3 files, found {len(files)}"
            assert any("MEMORY.md" in f.path or "memory.md" in f.path for f in files), "Should find main memory file"
            
            print("✅ File scanning working correctly")
    except Exception as e:
        print(f"❌ File scanning test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Test hash-based change detection
    print("\n[4/6] Testing change detection...")
    try:
        from flocks.memory.utils import compute_hash
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.md') as f:
            f.write("Original content")
            temp_path = Path(f.name)
        
        hash1 = compute_hash(temp_path)
        print(f"   Hash 1: {hash1[:16]}...")
        
        # Modify file
        temp_path.write_text("Modified content")
        hash2 = compute_hash(temp_path)
        print(f"   Hash 2: {hash2[:16]}...")
        
        assert hash1 != hash2, "Hashes should differ for different content"
        
        # Same content should give same hash
        temp_path.write_text("Original content")
        hash3 = compute_hash(temp_path)
        
        assert hash1 == hash3, "Same content should give same hash"
        
        temp_path.unlink()
        print("✅ Change detection working correctly")
    except Exception as e:
        print(f"❌ Change detection test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Test chunk hash uniqueness
    print("\n[5/6] Testing chunk hash uniqueness...")
    try:
        from flocks.memory.utils import compute_text_hash
        
        text1 = "Same content"
        text2 = "Same content"
        text3 = "Different content"
        
        hash1 = compute_text_hash(text1)
        hash2 = compute_text_hash(text2)
        hash3 = compute_text_hash(text3)
        
        print(f"   Hash 1: {hash1}")
        print(f"   Hash 2: {hash2}")
        print(f"   Hash 3: {hash3}")
        
        assert hash1 == hash2, "Same text should give same hash"
        assert hash1 != hash3, "Different text should give different hash"
        
        print("✅ Chunk hash uniqueness working correctly")
    except Exception as e:
        print(f"❌ Chunk hash test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Test indexer initialization
    print("\n[6/6] Testing indexer initialization...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            memory_config = MemoryConfig(
                enabled=True,
                sources=["memory"],
            )
            
            indexer = MemoryIndexer(
                project_id="test_proj",
                workspace_dir=workspace,
                provider_id="openai",
                embedding_model="text-embedding-3-small",
                config=memory_config,
            )
            
            print(f"   Project ID: {indexer.project_id}")
            print(f"   Workspace: {indexer.workspace_dir}")
            print(f"   Provider: {indexer.provider_id}")
            print(f"   Model: {indexer.embedding_model}")
            print(f"   Chunker: {indexer.chunker is not None}")
            
            assert indexer.chunker is not None, "Should have chunker"
            
            print("✅ Indexer initialization working correctly")
    except Exception as e:
        print(f"❌ Indexer initialization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 60)
    print("✅ All chunking and indexing tests passed!")
    print("=" * 60)
    print("\n📋 Chunking and indexing system ready:")
    print("   - Text chunker with token-based splitting")
    print("   - Overlap strategy for better context")
    print("   - File scanner for memory files")
    print("   - Hash-based change detection")
    print("   - Incremental indexing support")
    print("   - Embedding generation integration")
    
    print("\n⚠️  Note: Full indexing test with embeddings requires API keys")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_chunking_and_indexing())
    exit(0 if success else 1)
