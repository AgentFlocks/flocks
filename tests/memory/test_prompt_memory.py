#!/usr/bin/env python3
"""
Test Prompt System Memory Integration

Tests memory injection into system prompts.
"""

import asyncio


async def test_prompt_memory():
    """Test prompt memory integration"""
    print("=" * 70)
    print("Testing Prompt Memory Integration")
    print("=" * 70)
    
    # Test 1: Import
    print("\n[1/4] Testing imports...")
    try:
        from flocks.session import SessionPrompt, SessionMemory
        print("✅ Successfully imported prompt and memory modules")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return False
    
    # Test 2: Test build_memory_context with disabled memory
    print("\n[2/4] Testing build_memory_context (disabled)...")
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create disabled memory
            memory = SessionMemory(
                session_id="test",
                project_id="proj",
                workspace_dir=tmpdir,
                enabled=False,
            )
            
            # Should return None
            context = await SessionPrompt.build_memory_context(
                session_memory=memory,
                user_message="test query",
            )
            
            print(f"   Context (disabled): {context}")
            assert context is None, "Should return None when disabled"
            
            print("✅ Disabled memory handling correct")
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False
    
    # Test 3: Test runtime system prompt builder without memory bootstrap
    print("\n[3/4] Testing build_system_prompts without memory bootstrap...")
    try:
        prompt_parts = await SessionPrompt.build_system_prompts(
            session_id="test",
            session_directory=None,
            agent_name="test_agent",
            agent_prompt="agent prompt",
            provider_id="test-provider",
            model_id="test-model",
        )
        prompt = "\n\n".join(prompt_parts)
        
        print(f"   Prompt length: {len(prompt)} chars")
        assert len(prompt) > 0, "Should generate prompt"
        assert "agent prompt" in prompt, "Should include agent prompt"
        
        print("✅ System prompt generation working")
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Test runtime system prompt builder with disabled memory bootstrap injection
    print("\n[4/4] Testing build_system_prompts with memory bootstrap disabled...")
    try:
        prompt_parts = await SessionPrompt.build_system_prompts(
            session_id="test",
            session_directory=None,
            agent_name="test_agent",
            agent_prompt="agent prompt",
            provider_id="test-provider",
            model_id="test-model",
            memory_bootstrap_data={
                "instructions": "memory guidance",
                "main_memory": {
                    "path": "MEMORY.md",
                    "content": "remembered context",
                    "inject": False,
                },
            },
            prompt_tool_names=("read",),
        )
        prompt = "\n\n".join(prompt_parts)

        print(f"   Prompt length: {len(prompt)} chars")
        assert len(prompt) > 0, "Should generate prompt"
        assert "Relevant Memory" not in prompt, "Should not include memory section when disabled"
        assert "remembered context" not in prompt, "Should not inject disabled memory snapshot"

        print("✅ Memory integration working correctly")
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 70)
    print("✅ All Prompt Memory integration tests passed!")
    print("=" * 70)
    
    print("\n📋 Prompt Memory Integration Ready:")
    print("   ✅ build_memory_context() method")
    print("   ✅ build_system_prompts() runtime prompt builder")
    print("   ✅ Memory bootstrap injection control")
    print("   ✅ Graceful disabled handling")
    
    print("\n🎯 Usage Example:")
    print("   prompt_parts = await SessionPrompt.build_system_prompts(")
    print("       session_id=session.id,")
    print("       session_directory=session.directory,")
    print("       agent_name=agent.name,")
    print("       agent_prompt=agent.prompt,")
    print("       provider_id=provider_id,")
    print("       model_id=model_id,")
    print("   )")
    
    return True


if __name__ == "__main__":
    success = asyncio.run(test_prompt_memory())
    exit(0 if success else 1)
