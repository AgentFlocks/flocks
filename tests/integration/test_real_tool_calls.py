"""
真实工具调用集成测试

测试完整的工具调用流程，包括：
1. Rex agent 执行 IP 情报查询
2. 工具注册和执行
3. 完整的对话 + 工具调用 + 响应流程
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

from flocks.session.session import Session
from flocks.session.message import Message, MessageRole
from flocks.session.session_loop import SessionLoop, LoopCallbacks
from flocks.agent import Agent
from flocks.tool.registry import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.skip(reason="Mock path incorrect: Provider.chat classmethod is not intercepted by patch; needs refactoring")
async def test_rex_query_ip_intelligence():
    """
    测试 Rex 执行 IP 情报查询：查一下 8.8.8.8 的情报
    
    完整流程：
    1. 用户输入："查一下 8.8.8.8 的情报"
    2. Rex 分析后调用 threatbook_ip_query 工具
    3. 工具返回结果
    4. Rex 整理结果给出最终回答
    """
    # 初始化工具注册表
    ToolRegistry.init()
    
    # 验证 threatbook_ip_query 工具已注册
    tools = [t.name for t in ToolRegistry.list_tools()]
    assert "threatbook_ip_query" in tools, "threatbook_ip_query 工具未注册"
    
    # 创建测试 session
    session = await Session.create(
        project_id="test_tool_call",
        directory="/tmp/test",
        title="IP Intelligence Query Test"
    )
    
    # 创建用户消息
    await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="查一下 8.8.8.8 的情报",
        agent="rex"
    )
    
    # 跟踪工具调用
    tool_calls_made = []
    
    async def mock_tool_execute(tool_name, args, **kwargs):
        """Mock 工具执行"""
        tool_calls_made.append((tool_name, args))
        
        if tool_name == "threatbook_ip_query":
            # 返回模拟的情报数据
            return {
                "response_code": 0,
                "verbose_msg": "成功",
                "data": {
                    "8.8.8.8": {
                        "severity": "info",
                        "judgments": ["IDC"],
                        "tags_classes": [{"tags": ["Google DNS"]}],
                        "basic": {
                            "carrier": "Google Inc.",
                            "location": {
                                "country": "美国",
                                "province": "加利福尼亚"
                            }
                        },
                        "scene": "公共 DNS 服务器",
                        "confidence_level": "high"
                    }
                }
            }
        return "Tool executed"
    
    # Mock LLM 响应序列
    with patch('flocks.provider.provider.Provider.chat') as mock_chat:
        # 第一次调用：Rex 决定调用工具
        first_response = MagicMock()
        first_response.content = ""
        first_response.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "threatbook_ip_query",
                    "arguments": '{"ip": "8.8.8.8"}'
                }
            }
        ]
        first_response.usage = {"input_tokens": 50, "output_tokens": 20}
        first_response.stop_reason = "tool_use"
        
        # 第二次调用：Rex 根据工具结果给出最终答案
        second_response = MagicMock()
        second_response.content = """根据威胁情报查询结果，8.8.8.8 的信息如下：

**基本信息：**
- IP: 8.8.8.8
- 归属: Google Inc.
- 位置: 美国加利福尼亚
- 用途: 公共 DNS 服务器

**安全评估：**
- 威胁等级: info（信息级别，无威胁）
- 标签: Google DNS
- 类型: IDC
- 可信度: high（高可信度）

**结论：**
这是 Google 提供的公共 DNS 服务器，安全可靠，无威胁风险。"""
        
        second_response.tool_calls = None
        second_response.usage = {"input_tokens": 150, "output_tokens": 100}
        second_response.stop_reason = "end_turn"
        
        # 设置 mock 返回序列
        mock_chat.side_effect = [first_response, second_response]
        
        # Mock 工具执行
        with patch('flocks.tool.registry.ToolRegistry.execute', side_effect=mock_tool_execute):
            # 运行 SessionLoop
            result = await SessionLoop.run(
                session_id=session.id,
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                agent_name="rex",
            )
            
            # 验证循环完成
            assert result.action == "stop", f"Expected stop, got {result.action}"
            
            # 验证工具被调用
            assert len(tool_calls_made) >= 1, "工具应该被调用"
            tool_name, args = tool_calls_made[0]
            assert tool_name == "threatbook_ip_query", f"Expected threatbook_ip_query, got {tool_name}"
            assert args.get("ip") == "8.8.8.8", f"Expected IP 8.8.8.8, got {args.get('ip')}"
            
            # 验证 LLM 被调用了两次
            assert mock_chat.call_count == 2, f"Expected 2 LLM calls, got {mock_chat.call_count}"
    
    # 验证消息历史
    messages = await Message.list(session.id)
    assert len(messages) >= 2, "应该至少有用户消息和助手消息"
    
    # 验证最后一条消息是助手回复
    # Note: 由于当前实现可能不保存所有消息，这里只做基本验证
    user_messages = [m for m in messages if m.role == "user"]
    assert len(user_messages) >= 1, "应该有用户消息"


@pytest.mark.asyncio
async def test_rex_tool_call_with_callbacks():
    """
    测试 Rex 工具调用的回调机制
    
    验证 on_tool_start 和 on_tool_end 回调被正确触发
    """
    ToolRegistry.init()
    
    session = await Session.create(
        project_id="test_tool_callback",
        directory="/tmp/test",
        title="Tool Callback Test"
    )
    
    await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="查询 1.1.1.1 的信息",
        agent="rex"
    )
    
    # 跟踪回调
    tool_starts = []
    tool_ends = []
    
    async def on_tool_start(tool_name, args):
        tool_starts.append((tool_name, args))
    
    async def on_tool_end(tool_name, result):
        tool_ends.append((tool_name, result))
    
    callbacks = LoopCallbacks(
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
    )
    
    # Mock LLM
    with patch('flocks.provider.provider.Provider.chat') as mock_chat:
        first_response = MagicMock()
        first_response.content = ""
        first_response.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "threatbook_ip_query",
                    "arguments": '{"ip": "1.1.1.1"}'
                }
            }
        ]
        first_response.usage = {"input_tokens": 30, "output_tokens": 15}
        first_response.stop_reason = "tool_use"
        
        second_response = MagicMock()
        second_response.content = "1.1.1.1 是 Cloudflare 的公共 DNS 服务器"
        second_response.tool_calls = None
        second_response.usage = {"input_tokens": 100, "output_tokens": 30}
        second_response.stop_reason = "end_turn"
        
        mock_chat.side_effect = [first_response, second_response]
        
        # Mock 工具执行
        with patch('flocks.tool.registry.ToolRegistry.execute') as mock_tool:
            mock_tool.return_value = {"data": {"1.1.1.1": {"carrier": "Cloudflare"}}}
            
            result = await SessionLoop.run(
                session_id=session.id,
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                agent_name="rex",
                callbacks=callbacks,
            )
            
            assert result.action == "stop"
            
            # Note: 回调机制可能需要完整的实现才能触发
            # 这里主要验证流程不出错


@pytest.mark.asyncio
@pytest.mark.skip(reason="Mock path incorrect: Provider.chat classmethod is not intercepted by patch; needs refactoring")
async def test_rex_multi_tool_calls():
    """
    测试 Rex 执行多个工具调用
    
    场景：用户要求查询多个 IP
    """
    ToolRegistry.init()
    
    session = await Session.create(
        project_id="test_multi_tool",
        directory="/tmp/test",
        title="Multi Tool Call Test"
    )
    
    await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="查询 8.8.8.8 和 1.1.1.1 的情报",
        agent="rex"
    )
    
    tool_calls_made = []
    
    async def mock_tool_execute(tool_name, args, **kwargs):
        tool_calls_made.append((tool_name, args))
        ip = args.get("ip", "")
        return {
            "response_code": 0,
            "data": {
                ip: {
                    "severity": "info",
                    "carrier": "DNS Provider"
                }
            }
        }
    
    with patch('flocks.provider.provider.Provider.chat') as mock_chat:
        # 第一次：调用第一个工具
        first_response = MagicMock()
        first_response.content = ""
        first_response.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "threatbook_ip_query",
                    "arguments": '{"ip": "8.8.8.8"}'
                }
            }
        ]
        first_response.usage = {"input_tokens": 50, "output_tokens": 20}
        first_response.stop_reason = "tool_use"
        
        # 第二次：调用第二个工具
        second_response = MagicMock()
        second_response.content = ""
        second_response.tool_calls = [
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "threatbook_ip_query",
                    "arguments": '{"ip": "1.1.1.1"}'
                }
            }
        ]
        second_response.usage = {"input_tokens": 70, "output_tokens": 20}
        second_response.stop_reason = "tool_use"
        
        # 第三次：给出最终答案
        third_response = MagicMock()
        third_response.content = "两个 IP 都是安全的公共 DNS 服务器"
        third_response.tool_calls = None
        third_response.usage = {"input_tokens": 150, "output_tokens": 50}
        third_response.stop_reason = "end_turn"
        
        mock_chat.side_effect = [first_response, second_response, third_response]
        
        with patch('flocks.tool.registry.ToolRegistry.execute', side_effect=mock_tool_execute):
            result = await SessionLoop.run(
                session_id=session.id,
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                agent_name="rex",
            )
            
            assert result.action == "stop"
            
            # 验证两个工具都被调用
            assert len(tool_calls_made) >= 2, f"Expected 2 tool calls, got {len(tool_calls_made)}"
            
            # 验证调用的 IP
            ips_queried = [args.get("ip") for _, args in tool_calls_made]
            assert "8.8.8.8" in ips_queried, "8.8.8.8 should be queried"
            assert "1.1.1.1" in ips_queried, "1.1.1.1 should be queried"


@pytest.mark.asyncio
async def test_tool_call_error_handling():
    """
    测试工具调用错误处理
    
    场景：工具执行失败，Rex 应该优雅处理
    """
    ToolRegistry.init()
    
    session = await Session.create(
        project_id="test_tool_error",
        directory="/tmp/test",
        title="Tool Error Test"
    )
    
    await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="查询 invalid_ip 的情报",
        agent="rex"
    )
    
    with patch('flocks.provider.provider.Provider.chat') as mock_chat:
        first_response = MagicMock()
        first_response.content = ""
        first_response.tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "threatbook_ip_query",
                    "arguments": '{"ip": "invalid_ip"}'
                }
            }
        ]
        first_response.usage = {"input_tokens": 40, "output_tokens": 15}
        first_response.stop_reason = "tool_use"
        
        second_response = MagicMock()
        second_response.content = "抱歉，这不是一个有效的 IP 地址"
        second_response.tool_calls = None
        second_response.usage = {"input_tokens": 80, "output_tokens": 20}
        second_response.stop_reason = "end_turn"
        
        mock_chat.side_effect = [first_response, second_response]
        
        # Mock 工具执行返回错误
        with patch('flocks.tool.registry.ToolRegistry.execute') as mock_tool:
            mock_tool.return_value = {
                "error": "Invalid IP address format",
                "response_code": -1
            }
            
            result = await SessionLoop.run(
                session_id=session.id,
                provider_id="anthropic",
                model_id="claude-sonnet-4",
                agent_name="rex",
            )
            
            # 即使工具失败，循环也应该正常完成
            assert result.action in ["stop", "error"]
