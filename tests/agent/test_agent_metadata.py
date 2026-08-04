"""
测试 Agent 元数据查询功能

验证：
1. is_delegatable / get_agent_mode / is_hidden 等查询函数正常
2. delegatable 逻辑正确（primary/hidden agents 不可委派）
3. 不存在循环依赖

注意：模块级同步函数（is_delegatable 等）依赖 _agents_ref，
后者在首次 Agent.state() 调用后才会被填充。
因此本测试文件使用 asyncio fixtures 提前触发加载。
"""

import asyncio
import pytest
from flocks.agent.registry import (
    is_delegatable,
    get_agent_mode,
    is_hidden,
    list_delegatable_agents,
    list_primary_agents,
    list_subagents,
    Agent,
)


def setup_module(_module):
    """在所有测试开始前同步加载 agents，确保 _agents_ref 已填充。"""
    asyncio.run(Agent.state())


class TestAgentMetadata:
    """测试 Agent 元数据查询函数（_agents_ref 已加载后）"""

    def test_is_delegatable_primary_agents(self):
        """Primary agents 不应该被委派"""
        assert is_delegatable("rex") is False

    def test_is_delegatable_subagents(self):
        """常规 subagents 应该返回 True"""
        assert is_delegatable("explore") is True
        assert is_delegatable("hephaestus") is True
        assert is_delegatable("oracle") is True
        assert is_delegatable("librarian") is True

    def test_is_delegatable_special_agents(self):
        """特殊 agents（plan, rex-junior）不应该被委派"""
        assert is_delegatable("plan") is False
        assert is_delegatable("rex-junior") is False

    def test_is_delegatable_unknown_agent(self):
        """Unknown agents must be rejected instead of falling back at runtime."""
        assert is_delegatable("unknown-agent") is False
        assert is_delegatable("custom-agent-123") is False

    def test_is_delegatable_hidden_agent(self, monkeypatch):
        """Hidden agents must not be exposed as delegation targets."""
        from flocks.agent import registry
        from flocks.agent.agent import AgentInfo

        hidden_agent = AgentInfo(
            name="hidden-agent",
            mode="subagent",
            hidden=True,
            delegatable=True,
        )
        monkeypatch.setattr(registry, "_agents_ref", {"hidden-agent": hidden_agent})

        assert is_delegatable("hidden-agent") is False

    def test_is_delegatable_resolves_legacy_aliases(self):
        """Known aliases should use the target agent's delegation policy."""
        assert is_delegatable("sisyphus") is False

    def test_get_agent_mode(self):
        """测试获取 agent 模式"""
        assert get_agent_mode("rex") == "primary"
        assert get_agent_mode("explore") == "subagent"
        assert get_agent_mode("hephaestus") == "subagent"
        assert get_agent_mode("nonexistent") is None

    def test_is_hidden(self):
        """测试 hidden 属性"""
        assert is_hidden("plan") is True
        assert is_hidden("explore") is False
        assert is_hidden("rex") is False
        assert is_hidden("nonexistent") is False

    def test_list_delegatable_agents(self):
        """测试列出可委派的 agents"""
        delegatable = list_delegatable_agents()
        assert isinstance(delegatable, list)
        assert "explore" in delegatable
        assert "hephaestus" in delegatable
        assert "oracle" in delegatable
        assert "rex" not in delegatable
        assert "plan" not in delegatable
        assert "rex-junior" not in delegatable

    def test_list_primary_agents(self):
        """测试列出 primary agents"""
        primary = list_primary_agents()
        assert isinstance(primary, list)
        assert "rex" in primary
        assert "explore" not in primary

    def test_list_subagents(self):
        """测试列出 subagents"""
        subs = list_subagents()
        assert isinstance(subs, list)
        assert "explore" in subs
        assert "hephaestus" in subs
        assert "rex" not in subs


class TestNoCircularDependency:
    """验证关键模块的导入不产生循环依赖"""

    def test_no_circular_dependency(self):
        from flocks.agent.registry import is_delegatable as _is_delegatable
        from flocks.agent.registry import Agent
        from flocks.tool.registry import ToolRegistry
        from flocks.tool.agent.delegate_task import delegate_task_tool as delegate_task_tool

        assert callable(_is_delegatable)
        assert Agent is not None
        assert ToolRegistry is not None
        assert delegate_task_tool is not None


class TestDelegateTaskIntegration:
    """验证 delegate_task 使用 registry 而不是旧的 metadata 模块"""

    def test_delegate_task_uses_registry(self):
        import inspect
        import flocks.tool.agent.delegate_task as delegate_task_module

        # 检查模块源码（import 在模块级别，不在函数体内）
        source = inspect.getsource(delegate_task_module)
        assert "from flocks.agent.registry import is_delegatable" in source
        assert "from flocks.agent.metadata import is_delegatable" not in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
