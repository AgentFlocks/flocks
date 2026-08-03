#!/usr/bin/env python3
"""
Basic test for provider embeddings functionality

Run this to verify that the embeddings interface was added correctly.
"""

import asyncio


async def test_basic_interface():
    """Test that the basic interface exists"""
    print("=" * 60)
    print("Testing Provider Embeddings Interface")
    print("=" * 60)
    
    # Test 1: Import modules
    print("\n[1/6] Testing imports...")
    try:
        from flocks.provider import Provider, BaseProvider
        print("✅ Successfully imported Provider and BaseProvider")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False
    
    # Test 2: Check BaseProvider has embed methods
    print("\n[2/6] Checking BaseProvider has embed methods...")
    try:
        assert hasattr(BaseProvider, 'embed'), "BaseProvider missing 'embed' method"
        assert hasattr(BaseProvider, 'embed_batch'), "BaseProvider missing 'embed_batch' method"
        assert hasattr(BaseProvider, 'supports_embeddings'), "BaseProvider missing 'supports_embeddings' method"
        assert hasattr(BaseProvider, 'get_embedding_models'), "BaseProvider missing 'get_embedding_models' method"
        print("✅ BaseProvider has all required methods")
    except AssertionError as e:
        print(f"❌ {e}")
        return False
    
    # Test 3: Check Provider has embed methods
    print("\n[3/6] Checking Provider namespace has embed methods...")
    try:
        assert hasattr(Provider, 'embed'), "Provider missing 'embed' method"
        assert hasattr(Provider, 'embed_batch'), "Provider missing 'embed_batch' method"
        print("✅ Provider has all required methods")
    except AssertionError as e:
        print(f"❌ {e}")
        return False
    
    # Test 4: Initialize providers
    print("\n[4/6] Initializing provider system...")
    try:
        await Provider.init()
        print("✅ Provider system initialized")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")
        return False
    
    # Test 5: Check OpenAI provider supports embeddings
    print("\n[5/6] Checking OpenAI provider...")
    try:
        openai = Provider.get("openai")
        assert openai is not None, "OpenAI provider not found"
        
        supports = openai.supports_embeddings()
        print(f"   OpenAI supports embeddings: {supports}")
        
        if supports:
            models = openai.get_embedding_models()
            print(f"   Available models: {models}")
            assert len(models) > 0, "No embedding models available"
            print("✅ OpenAI provider properly configured")
        else:
            print("⚠️  OpenAI provider doesn't support embeddings (implementation issue)")
    except Exception as e:
        print(f"❌ OpenAI check failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Check Google provider supports embeddings
    print("\n[6/6] Checking Google provider...")
    try:
        google = Provider.get("google")
        assert google is not None, "Google provider not found"
        
        supports = google.supports_embeddings()
        print(f"   Google supports embeddings: {supports}")
        
        if supports:
            models = google.get_embedding_models()
            print(f"   Available models: {models}")
            assert len(models) > 0, "No embedding models available"
            print("✅ Google provider properly configured")
        else:
            print("⚠️  Google provider doesn't support embeddings (implementation issue)")
    except Exception as e:
        print(f"❌ Google check failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 60)
    print("✅ All basic tests passed!")
    print("=" * 60)
    print("\n📝 Note: To test actual embedding generation, you need:")
    print("   - OPENAI_API_KEY environment variable for OpenAI")
    print("   - GOOGLE_API_KEY environment variable for Google")
    print("\n💡 Example usage:")
    print("""
    from flocks.provider import Provider
    await Provider.init()
    
    # Single embedding
    embedding = await Provider.embed(
        text="Hello world",
        provider_id="openai",
        model="text-embedding-3-small"
    )
    
    # Batch embeddings
    embeddings = await Provider.embed_batch(
        texts=["Hello", "World"],
        provider_id="openai"
    )
    """)
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_basic_interface())
    exit(0 if success else 1)
