#!/usr/bin/env python3
"""
Test Memory Manager functionality

Tests the core MemoryManager orchestrator.
"""

import asyncio
import tempfile
from pathlib import Path


async def test_memory_manager():
    """Test memory manager"""
    print("=" * 60)
    print("Testing Memory Manager")
    print("=" * 60)
    
    # Test 1: Import modules
    print("\n[1/7] Testing imports...")
    try:
        from flocks.memory import MemoryManager, MemoryConfig
        from flocks.storage import Storage
        from flocks.provider import Provider
        print("✅ Successfully imported memory manager")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Test singleton pattern
    print("\n[2/7] Testing singleton pattern...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(enabled=True)
            
            manager1 = MemoryManager.get_instance(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            manager2 = MemoryManager.get_instance(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            print(f"   Manager 1: {id(manager1)}")
            print(f"   Manager 2: {id(manager2)}")
            
            assert manager1 is manager2, "Should return same instance for same project"
            
            # Different project should get different instance
            manager3 = MemoryManager.get_instance(
                project_id="other_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            assert manager1 is not manager3, "Different projects should get different instances"
            
            print("✅ Singleton pattern working correctly")
    except Exception as e:
        print(f"❌ Singleton test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Test initialization
    print("\n[3/7] Testing initialization...")
    try:
        await Storage.init()
        await Provider.init()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(
                enabled=True,
                embedding={
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                },
            )
            
            manager = MemoryManager(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            print(f"   Project ID: {manager.project_id}")
            print(f"   Workspace: {manager.workspace_dir}")
            print(f"   Provider: {manager.provider_id}")
            print(f"   Model: {manager.embedding_model}")
            print(f"   Initialized: {manager._initialized}")
            
            # Initialize
            await manager.initialize()
            
            print(f"   After init - Initialized: {manager._initialized}")
            print(f"   Search engine: {manager.search_engine is not None}")
            print(f"   Indexer: {manager.indexer is not None}")
            
            assert manager._initialized, "Should be initialized"
            assert manager.search_engine is not None, "Should have search engine"
            assert manager.indexer is not None, "Should have indexer"
            
            print("✅ Initialization working correctly")
    except Exception as e:
        print(f"❌ Initialization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Test status method
    print("\n[4/7] Testing status method...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(
                enabled=True,
                sources=["memory", "session"],
            )
            
            manager = MemoryManager(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            status = manager.status()
            
            print(f"   Enabled: {status.enabled}")
            print(f"   Provider: {status.provider}")
            print(f"   Model: {status.model}")
            print(f"   Sources: {[s.value for s in status.sources]}")
            print(f"   Dirty: {status.dirty}")
            
            assert status.enabled == True, "Should be enabled"
            assert len(status.sources) == 2, "Should have 2 sources"
            
            print("✅ Status method working correctly")
    except Exception as e:
        print(f"❌ Status test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Test write_memory method
    print("\n[5/7] Testing write_memory method...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = MemoryConfig(enabled=True)
            
            manager = MemoryManager(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            # Write to memory
            content = "# Test Memory\n\nThis is a test."
            path = await manager.write_memory(content, path="test.md", append=False)
            
            print(f"   Written to: {path}")
            
            # Check file exists
            file_path = workspace / path
            assert file_path.exists(), "File should exist"
            
            # Check content
            written_content = file_path.read_text()
            assert content in written_content, "Content should match"
            
            # Test append
            append_content = "More content."
            await manager.write_memory(append_content, path="test.md", append=True)
            
            appended = file_path.read_text()
            assert append_content in appended, "Appended content should be present"
            
            # Check dirty flag
            assert manager._dirty == True, "Should be marked as dirty"
            
            print("✅ Write memory working correctly")
    except Exception as e:
        print(f"❌ Write memory test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Test read_file method
    print("\n[6/7] Testing read_file method...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = MemoryConfig(enabled=True)
            
            manager = MemoryManager(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            # Create test file
            test_file = workspace / "test.md"
            test_content = "\n".join([f"Line {i}" for i in range(1, 11)])
            test_file.write_text(test_content)
            
            # Read entire file
            result = await manager.read_file("test.md")
            print(f"   Full read: {len(result['text'])} chars")
            assert "Line 1" in result["text"], "Should contain first line"
            assert "Line 10" in result["text"], "Should contain last line"
            
            # Read specific range
            result = await manager.read_file("test.md", from_line=3, lines=3)
            print(f"   Range read: {result['text']}")
            assert "Line 3" in result["text"], "Should contain line 3"
            assert "Line 5" in result["text"], "Should contain line 5"
            assert "Line 1" not in result["text"], "Should not contain line 1"
            
            print("✅ Read file working correctly")
    except Exception as e:
        print(f"❌ Read file test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 7: Test mark_dirty method
    print("\n[7/7] Testing mark_dirty method...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = MemoryConfig(enabled=True)
            
            manager = MemoryManager(
                project_id="test_proj",
                workspace_dir=tmpdir,
                config=config,
            )
            
            print(f"   Initial dirty: {manager._dirty}")
            assert manager._dirty == False, "Should start clean"
            
            manager.mark_dirty()
            print(f"   After mark: {manager._dirty}")
            assert manager._dirty == True, "Should be dirty after mark"
            
            print("✅ Mark dirty working correctly")
    except Exception as e:
        print(f"❌ Mark dirty test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 60)
    print("✅ All memory manager tests passed!")
    print("=" * 60)
    print("\n📋 Memory Manager ready:")
    print("   - Singleton pattern per project")
    print("   - Lazy initialization")
    print("   - Search orchestration")
    print("   - File indexing orchestration")
    print("   - Memory file read/write")
    print("   - Status reporting")
    print("   - Dirty tracking for sync")
    
    print("\n⚠️  Note: Full search/sync tests require:")
    print("   - Indexed memory files")
    print("   - API keys for embeddings")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_memory_manager())
    exit(0 if success else 1)
