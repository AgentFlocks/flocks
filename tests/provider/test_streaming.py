"""
测试模型 API 流式输出功能

验证 Anthropic Claude API 是否支持逐字流式返回答案。
"""

import asyncio
import os
import time

import pytest
from anthropic import AsyncAnthropic


@pytest.mark.requires_anthropic_key
async def test_anthropic_streaming():
    """测试 Anthropic API 流式输出"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    # 从环境变量获取 API base URL
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = "claude-sonnet-4-5-20250929"
    
    print("=" * 80)
    print("🧪 测试 Anthropic Claude API 流式输出")
    print("=" * 80)
    print(f"API Base URL: {base_url}")
    print(f"Model: {model}")
    print(f"API Key: {api_key[:10]}..." if len(api_key) > 10 else "***")
    print("=" * 80)
    print()
    
    try:
        # 创建客户端
        client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
        )
        
        # 测试提示词
        test_prompt = "请用一段话介绍什么是AI安全运营平台（SecOps），大约100字。"
        
        print(f"📝 提示词: {test_prompt}")
        print()
        print("🔄 开始流式输出:")
        print("-" * 80)
        
        # 记录开始时间
        start_time = time.time()
        first_chunk_time = None
        chunk_count = 0
        total_chars = 0
        
        # 使用流式 API
        async with client.messages.stream(
            model=model,
            max_tokens=1024,
            messages=[
                {"role": "user", "content": test_prompt}
            ],
        ) as stream:
            async for text in stream.text_stream:
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                    time_to_first_chunk = first_chunk_time - start_time
                    print(f"\n⏱️  首个chunk延迟: {time_to_first_chunk:.3f}秒\n")
                
                # 打印每个chunk（实时显示）
                print(text, end="", flush=True)
                
                chunk_count += 1
                total_chars += len(text)
                
                # 短暂延迟，让输出更明显
                await asyncio.sleep(0.01)
        
        # 统计信息
        end_time = time.time()
        total_time = end_time - start_time
        
        print()
        print("-" * 80)
        print()
        print("✅ 流式输出测试完成!")
        print()
        print("📊 统计信息:")
        print(f"  - 总chunk数: {chunk_count}")
        print(f"  - 总字符数: {total_chars}")
        print(f"  - 总耗时: {total_time:.3f}秒")
        print(f"  - 首个chunk延迟: {time_to_first_chunk:.3f}秒")
        print(f"  - 平均速度: {total_chars / total_time:.1f} 字符/秒")
        print()
        
        if chunk_count > 1:
            print("🎉 结论: API 支持流式输出，内容逐步返回！")
            return True
        else:
            print("⚠️  警告: 只收到1个chunk，可能不是真正的流式输出")
            return False
            
    except Exception as e:
        print()
        print("=" * 80)
        print(f"❌ 错误: {type(e).__name__}")
        print(f"详细信息: {str(e)}")
        print("=" * 80)
        import traceback
        traceback.print_exc()
        return False


@pytest.mark.requires_anthropic_key
async def test_with_openai_sdk():
    """使用 OpenAI SDK 兼容模式测试流式输出"""

    api_key = os.getenv("ANTHROPIC_API_KEY")
    
    print()
    print("=" * 80)
    print("🧪 测试 OpenAI 兼容 SDK 流式输出")
    print("=" * 80)
    
    try:
        from openai import AsyncOpenAI
        
        # 创建客户端（使用 OpenAI SDK 连接 Anthropic API）
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://apidekey.xyz/v1",
        )
        
        test_prompt = "简单介绍一下Python语言的特点，50字以内。"
        
        print(f"📝 提示词: {test_prompt}")
        print()
        print("🔄 开始流式输出:")
        print("-" * 80)
        
        start_time = time.time()
        first_chunk_time = None
        chunk_count = 0
        total_chars = 0
        
        # 使用流式 API
        stream = await client.chat.completions.create(
            model="claude-sonnet-4-5-20250929",
            messages=[
                {"role": "user", "content": test_prompt}
            ],
            stream=True,
        )
        
        async for chunk in stream:
            if first_chunk_time is None:
                first_chunk_time = time.time()
                time_to_first_chunk = first_chunk_time - start_time
                print(f"\n⏱️  首个chunk延迟: {time_to_first_chunk:.3f}秒\n")
            
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta.content:
                    print(delta.content, end="", flush=True)
                    chunk_count += 1
                    total_chars += len(delta.content)
                    await asyncio.sleep(0.01)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        print()
        print("-" * 80)
        print()
        print("✅ OpenAI SDK 流式输出测试完成!")
        print()
        print("📊 统计信息:")
        print(f"  - 总chunk数: {chunk_count}")
        print(f"  - 总字符数: {total_chars}")
        print(f"  - 总耗时: {total_time:.3f}秒")
        print(f"  - 平均速度: {total_chars / total_time:.1f} 字符/秒")
        print()
        
        return chunk_count > 1
        
    except ImportError:
        print("⚠️  未安装 openai 包，跳过此测试")
        print("   可通过 'uv pip install openai' 安装")
        return None
    except Exception as e:
        print()
        print(f"❌ 错误: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    print()
    print("🚀 开始测试模型 API 流式输出能力")
    print()
    
    # 测试1: 使用 Anthropic SDK
    result1 = await test_anthropic_streaming()
    
    # 测试2: 使用 OpenAI 兼容 SDK
    result2 = await test_with_openai_sdk()
    
    print()
    print("=" * 80)
    print("📋 测试总结")
    print("=" * 80)
    print(f"Anthropic SDK 测试: {'✅ 通过' if result1 else '❌ 失败'}")
    if result2 is not None:
        print(f"OpenAI SDK 测试: {'✅ 通过' if result2 else '❌ 失败'}")
    else:
        print("OpenAI SDK 测试: ⚠️  未执行")
    print("=" * 80)
    print()
    
    if result1:
        print("✅ 结论: 模型 API 支持流式输出！")
        print()
        print("💡 建议检查:")
        print("  1. WebUI 前端是否正确处理 SSE 事件流")
        print("  2. 后端路由 /api/session/{sessionID}/message 是否返回流式响应")
        print("  3. 前端是否订阅了 /api/event 的 SSE 连接")
        print("  4. 检查浏览器 Network 面板，查看事件流是否分段传输")
    else:
        print("❌ 问题: 模型 API 不支持流式输出，或配置有误")
        print()
        print("🔍 排查方向:")
        print("  1. 检查 ANTHROPIC_API_KEY 是否正确")
        print("  2. 检查 API base URL 是否正确")
        print("  3. 检查网络连接是否正常")


if __name__ == "__main__":
    # 检查是否在项目根目录
    if not Path("flocks").exists():
        print("⚠️  警告: 请在项目根目录运行此脚本")
        print("   cd 到项目根目录后执行: uv run scripts/test_streaming.py")
        sys.exit(1)
    
    # 运行测试
    asyncio.run(main())
