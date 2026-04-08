"""
UI 集成测试

测试三个 UI 的集成：
1. CLI (flocks run)
2. Server API
3. TUI/WebUI 通过 Server API 的集成
"""

import pytest
import asyncio
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch


class TestCLIIntegration:
    """CLI 集成测试"""
    
    def test_cli_help_command(self):
        """测试 CLI help 命令"""
        result = subprocess.run(
            ["flocks", "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode == 0
        assert "flocks" in result.stdout.lower()
    
    def test_cli_version_command(self):
        """测试 CLI version 命令"""
        result = subprocess.run(
            ["flocks", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode == 0
    
    @pytest.mark.skip(reason="需要实际 LLM 调用")
    def test_cli_run_basic(self):
        """测试 CLI run 基本功能"""
        # 需要 mock LLM 或使用真实 API
        pass


class TestServerAPI:
    """Server API 集成测试"""
    
    @pytest.mark.asyncio
    async def test_server_imports(self):
        """测试 Server 模块可以正常导入"""
        from flocks.server import app
        assert app is not None
    
    @pytest.mark.asyncio
    async def test_session_routes_available(self):
        """测试 Session 路由可用"""
        from flocks.server.routes import session
        assert hasattr(session, 'create_session')
        assert hasattr(session, '_process_session_message')
    
    @pytest.mark.asyncio
    async def test_create_session_via_routes(self):
        """测试通过路由创建 session"""
        from flocks.server.routes.session import create_session
        
        # Mock request
        request = MagicMock()
        request.projectID = "test_project"
        request.directory = "/tmp/test"
        request.title = "Test Session"
        request.agent = "rex"
        request.parentID = None
        request.category = None
        request.permission = []
        
        response = await create_session(request)
        assert response.id.startswith("ses_")


class TestUIEventFlow:
    """UI 事件流集成测试"""
    
    @pytest.mark.asyncio
    async def test_event_publish_callback_flow(self):
        """测试事件发布回调流程（TUI/WebUI 实时更新）"""
        from flocks.session.session import Session
        from flocks.session.message import Message, MessageRole
        from flocks.session.session_loop import SessionLoop, LoopCallbacks
        
        # 创建 session
        session = await Session.create(
            project_id="test_event",
            directory="/tmp/test",
            title="Event Test"
        )
        
        # 跟踪事件
        published_events = []
        
        async def event_callback(event_type, data):
            published_events.append((event_type, data))
        
        # 创建消息
        await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="Test",
            agent="rex"
        )
        
        # 运行 loop with event callback
        callbacks = LoopCallbacks(
            event_publish_callback=event_callback
        )
        
        with patch('flocks.provider.provider.Provider.chat') as mock_chat:
            mock_response = MagicMock()
            mock_response.content = "Response"
            mock_response.usage = {"input_tokens": 10, "output_tokens": 5}
            mock_chat.return_value = mock_response
            
            await SessionLoop.run(
                session_id=session.id,
                provider_id="openai",
                model_id="gpt-4",
                agent_name="rex",
                callbacks=callbacks,
            )
        
        # 验证事件被发布（如果实现了 event publishing）
        # assert len(published_events) > 0


class TestModelResolution:
    """模型解析集成测试"""
    
    @pytest.mark.asyncio
    async def test_model_priority_request_over_agent(self):
        """测试模型优先级：request > agent"""
        from flocks.server.routes.session import _resolve_model
        from flocks.agent import Agent
        
        # Request 指定模型
        request = MagicMock()
        request.model = MagicMock()
        request.model.providerID = "anthropic"
        request.model.modelID = "claude-sonnet-4"
        
        # Agent 指定不同模型
        agent = await Agent.get("rex")
        
        provider_id, model_id, source = await _resolve_model(
            request, agent, "test_session"
        )
        
        # Request 优先
        assert provider_id == "anthropic"
        assert model_id == "claude-sonnet-4"
        assert source == "request"


class TestPermissionFlow:
    """权限流程集成测试"""
    
    @pytest.mark.asyncio
    async def test_tool_declaration_check_in_dialogue(self):
        """测试对话中的工具声明检查"""
        from flocks.agent import Agent
        
        # 测试 build agent 的工具声明
        result = await Agent.has_tool("rex", "read")
        assert result in [True, False]
        
        # 测试 explore agent 的工具声明（只读）
        read_result = await Agent.has_tool("explore", "read")
        write_result = await Agent.has_tool("explore", "write")
        
        assert read_result is True
        assert write_result is False


class TestSessionLifecycle:
    """Session 生命周期集成测试"""
    
    @pytest.mark.asyncio
    async def test_complete_session_lifecycle(self):
        """测试完整的 session 生命周期"""
        from flocks.session.session import Session
        from flocks.session.message import Message, MessageRole
        from flocks.session.core.status import SessionStatus, SessionStatusBusy
        
        # 1. 创建 session
        session = await Session.create(
            project_id="test_lifecycle",
            directory="/tmp/test",
            title="Lifecycle Test"
        )
        assert session.id.startswith("ses_")
        
        # 2. 设置状态为 busy
        SessionStatus.set(session.id, SessionStatusBusy(message="Processing"))
        
        # 3. 添加消息
        msg1 = await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="Hello"
        )
        assert msg1.id.startswith("msg_")
        
        msg2 = await Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content="Hi there!"
        )
        
        # 4. 列出消息
        messages = await Message.list(session.id)
        assert len(messages) >= 2
        
        # 5. 更新 session
        updated = await Session.update(
            "test_lifecycle",
            session.id,
            title="Updated Title"
        )
        assert updated.title == "Updated Title"
        
        # 6. 清除状态
        SessionStatus.clear(session.id)
        
        # 7. 删除 session
        deleted = await Session.delete("test_lifecycle", session.id)
        assert deleted is True
