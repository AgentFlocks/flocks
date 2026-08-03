#!/usr/bin/env python3
"""
Test memory system basics

Tests the basic memory system structure and configuration.
"""

import asyncio
import tempfile
from pathlib import Path


async def test_memory_basics():
    """Test memory system basic functionality"""
    print("=" * 60)
    print("Testing Memory System Basics")
    print("=" * 60)
    
    # Test 1: Import memory types
    print("\n[1/8] Testing type imports...")
    try:
        from flocks.memory import (
            MemorySource,
            MemorySearchResult,
            MemorySyncProgress,
            MemoryProviderStatus,
            MemoryFileEntry,
            MemoryChunk,
            EmbeddingResult,
        )
        print("✅ Successfully imported all memory types")
    except Exception as e:
        print(f"❌ Type import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Import memory config
    print("\n[2/8] Testing config imports...")
    try:
        from flocks.memory import (
            MemoryConfig,
            MemoryEmbeddingConfig,
            MemoryChunkingConfig,
            MemorySyncConfig,
            MemoryQueryConfig,
            MemoryCacheConfig,
            MemoryBatchConfig,
            MemoryAutoFlushConfig,
        )
        print("✅ Successfully imported all memory config types")
    except Exception as e:
        print(f"❌ Config import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Import memory utils
    print("\n[3/8] Testing utils imports...")
    try:
        from flocks.memory import (
            compute_hash,
            compute_text_hash,
            truncate_text,
            extract_snippet,
            normalize_path,
        )
        print("✅ Successfully imported all memory utils")
    except Exception as e:
        print(f"❌ Utils import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Create memory config instance
    print("\n[4/8] Testing MemoryConfig instantiation...")
    try:
        config = MemoryConfig(
            enabled=True,
            sources=["memory", "session"],
            citations="auto",
        )
        print(f"   Enabled: {config.enabled}")
        print(f"   Sources: {config.sources}")
        print(f"   Citations: {config.citations}")
        print(f"   Embedding provider: {config.embedding.provider}")
        print(f"   Embedding model: {config.embedding.model}")
        print(f"   Chunk tokens: {config.chunking.tokens}")
        print(f"   Chunk overlap: {config.chunking.overlap}")
        print("✅ MemoryConfig instantiation working")
    except Exception as e:
        print(f"❌ Config instantiation failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Test hash functions
    print("\n[5/8] Testing hash functions...")
    try:
        text = "Hello, world!"
        text_hash = compute_text_hash(text)
        print(f"   Text hash: {text_hash}")
        assert len(text_hash) == 32, "Text hash should be 32 chars"
        
        # Test file hash
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(text)
            temp_path = Path(f.name)
        
        file_hash = compute_hash(temp_path)
        print(f"   File hash: {file_hash[:16]}...")
        assert len(file_hash) == 64, "File hash should be 64 chars (SHA256)"
        
        temp_path.unlink()  # Clean up
        print("✅ Hash functions working correctly")
    except Exception as e:
        print(f"❌ Hash test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Test text functions
    print("\n[6/8] Testing text functions...")
    try:
        long_text = "A" * 1000
        truncated = truncate_text(long_text, max_length=100)
        print(f"   Original length: {len(long_text)}")
        print(f"   Truncated length: {len(truncated)}")
        assert len(truncated) == 100, "Truncated text should be 100 chars"
        assert truncated.endswith("..."), "Truncated text should end with ..."
        
        # Test snippet extraction
        multi_line = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        snippet = extract_snippet(multi_line, start_line=2, end_line=4)
        print(f"   Snippet: {snippet}")
        assert snippet == "Line 2\nLine 3\nLine 4", "Snippet should extract correct lines"
        
        print("✅ Text functions working correctly")
    except Exception as e:
        print(f"❌ Text functions test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 7: Test path normalization
    print("\n[7/8] Testing path normalization...")
    try:
        from flocks.memory.utils.text import is_memory_path
        
        paths = [
            ("MEMORY.md", True),
            ("memory.md", True),
            ("./MEMORY.md", True),
            ("memory/2024-01-01.md", True),
            ("./memory/notes.md", True),
            ("docs/README.md", False),
            ("test.py", False),
        ]
        
        for path, expected in paths:
            result = is_memory_path(path)
            status = "✓" if result == expected else "✗"
            print(f"   {status} {path}: {result} (expected: {expected})")
            assert result == expected, f"Path {path} should be {expected}"
        
        print("✅ Path normalization working correctly")
    except Exception as e:
        print(f"❌ Path normalization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 8: Test ConfigInfo integration
    print("\n[8/8] Testing ConfigInfo integration...")
    try:
        from flocks.config import Config
        
        # Test that ConfigInfo has memory field
        from flocks.config.config import ConfigInfo
        import inspect
        
        fields = [f for f in dir(ConfigInfo) if not f.startswith('_')]
        has_memory = 'memory' in fields or any('memory' in str(f) for f in inspect.signature(ConfigInfo.__init__).parameters)
        
        print(f"   ConfigInfo has memory field: {has_memory}")
        
        # Create a config with memory
        config_dict = {
            "memory": {
                "enabled": True,
                "sources": ["memory"],
            }
        }
        
        config_info = ConfigInfo(**config_dict)
        print(f"   Memory config in ConfigInfo: {config_info.memory is not None}")
        
        print("✅ ConfigInfo integration working")
    except Exception as e:
        print(f"❌ ConfigInfo integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 60)
    print("✅ All memory basics tests passed!")
    print("=" * 60)
    print("\n📋 Memory system structure ready:")
    print("   - Types defined (MemorySearchResult, MemoryConfig, etc.)")
    print("   - Config models created (MemoryEmbeddingConfig, etc.)")
    print("   - Utility functions implemented (hash, text utils)")
    print("   - ConfigInfo extended with memory field")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_memory_basics())
    exit(0 if success else 1)
