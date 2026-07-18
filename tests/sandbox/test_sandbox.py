"""
Sandbox 系统测试

覆盖:
1. 配置解析 (config resolution)
2. 运行时状态判定 (runtime status)
3. 路径安全 (path security)
4. 工具策略 (tool policy)
5. Docker 参数构建 (docker args)
6. 共享工具函数 (shared utils)
7. 配置哈希 (config hash)
8. 环境变量安全 (env security)
"""

import os
import tempfile

import pytest


# ==================== 1. 配置解析测试 ====================


class TestSandboxConfig:
    """沙箱配置解析测试."""

    def test_default_config(self):
        """默认配置: mode=off."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        cfg = resolve_sandbox_config_for_agent()
        assert cfg.mode == "off"
        assert cfg.scope == "agent"
        assert cfg.workspace_access == "none"
        assert cfg.docker.image == "python:slim"
        assert cfg.docker.read_only_root is True
        assert cfg.docker.network == "none"
        assert cfg.docker.cap_drop == ["ALL"]

    def test_global_sandbox_config(self):
        """全局 sandbox 配置."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "mode": "on",
                "scope": "session",
                "workspace_access": "rw",
                "docker": {
                    "image": "custom:latest",
                    "network": "bridge",
                },
            }
        }
        cfg = resolve_sandbox_config_for_agent(config_data)
        assert cfg.mode == "on"
        assert cfg.scope == "session"
        assert cfg.workspace_access == "rw"
        assert cfg.docker.image == "custom:latest"
        assert cfg.docker.network == "bridge"

    def test_agent_override(self):
        """Agent 级覆写."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "mode": "on",
                "docker": {"image": "global:latest"},
            },
            "agent": {
                "rex": {
                    "sandbox": {
                        "mode": "on",
                        "docker": {"image": "agent:latest"},
                    }
                }
            },
        }
        cfg = resolve_sandbox_config_for_agent(config_data, agent_id="rex")
        assert cfg.mode == "on"
        assert cfg.docker.image == "agent:latest"

    def test_elevated_config_resolution(self):
        """提升执行配置解析."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "mode": "on",
                "elevated": {"enabled": True, "tools": ["bash"]},
            }
        }
        cfg = resolve_sandbox_config_for_agent(config_data, agent_id="rex")
        assert cfg.elevated.enabled is True
        assert cfg.elevated.tools == ["bash"]

    def test_legacy_modes_are_mapped_to_on(self):
        """历史模式值 all/non-main 兼容映射到 on."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        cfg_all = resolve_sandbox_config_for_agent({"sandbox": {"mode": "all"}})
        cfg_non_main = resolve_sandbox_config_for_agent(
            {"sandbox": {"mode": "non-main"}}
        )
        assert cfg_all.mode == "on"
        assert cfg_non_main.mode == "on"

    def test_shared_scope_ignores_agent_docker(self):
        """scope=shared 时忽略 agent 级 docker 覆写."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "mode": "on",
                "scope": "shared",
                "docker": {"image": "global:latest"},
            },
            "agent": {
                "rex": {
                    "sandbox": {
                        "docker": {"image": "agent:latest"},
                    }
                }
            },
        }
        cfg = resolve_sandbox_config_for_agent(config_data, agent_id="rex")
        assert cfg.scope == "shared"
        # agent docker override should be ignored for shared scope
        assert cfg.docker.image == "global:latest"

    def test_scope_resolution(self):
        """scope 解析."""
        from flocks.sandbox.config import resolve_sandbox_scope

        assert resolve_sandbox_scope("session") == "session"
        assert resolve_sandbox_scope("agent") == "agent"
        assert resolve_sandbox_scope("shared") == "shared"
        assert resolve_sandbox_scope(None) == "agent"
        assert resolve_sandbox_scope("invalid") == "agent"

    def test_agent_empty_tool_lists_override_global(self):
        """agent 显式空工具列表应覆盖 global 配置."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "tools": {
                    "allow": ["bash"],
                    "deny": ["read"],
                }
            },
            "agent": {
                "rex": {
                    "sandbox": {
                        "tools": {
                            "allow": [],
                            "deny": [],
                        }
                    }
                }
            },
        }

        cfg = resolve_sandbox_config_for_agent(config_data, agent_id="rex")
        assert cfg.tools.allow == []
        assert cfg.tools.deny == []


# ==================== 2. 运行时状态判定测试 ====================


class TestRuntimeStatus:
    """运行时状态判定测试."""

    def test_mode_off(self):
        """mode=off 不沙箱化."""
        from flocks.sandbox.runtime_status import resolve_sandbox_runtime_status

        status = resolve_sandbox_runtime_status(
            config_data={"sandbox": {"mode": "off"}},
            session_key="test-session",
            main_session_key="main",
        )
        assert status.sandboxed is False

    def test_mode_on(self):
        """mode=on 总是沙箱化."""
        from flocks.sandbox.runtime_status import resolve_sandbox_runtime_status

        status = resolve_sandbox_runtime_status(
            config_data={"sandbox": {"mode": "on"}},
            session_key="test-session",
            main_session_key="main",
        )
        assert status.sandboxed is True

    def test_mode_on_different_session(self):
        """mode=on 非主会话沙箱化."""
        from flocks.sandbox.runtime_status import resolve_sandbox_runtime_status

        status = resolve_sandbox_runtime_status(
            config_data={"sandbox": {"mode": "on"}},
            session_key="other-session",
            main_session_key="main",
        )
        assert status.sandboxed is True

    def test_mode_on_same_session(self):
        """mode=on 主会话同样沙箱化."""
        from flocks.sandbox.runtime_status import resolve_sandbox_runtime_status

        status = resolve_sandbox_runtime_status(
            config_data={"sandbox": {"mode": "on"}},
            session_key="main",
            main_session_key="main",
        )
        assert status.sandboxed is True

    def test_empty_session_key(self):
        """空 session key 不沙箱化."""
        from flocks.sandbox.runtime_status import resolve_sandbox_runtime_status

        status = resolve_sandbox_runtime_status(
            config_data={"sandbox": {"mode": "on"}},
            session_key="",
            main_session_key="main",
        )
        assert status.sandboxed is False


# ==================== 3. 路径安全测试 ====================


class TestPaths:
    """路径安全测试."""

    def test_resolve_within_root(self):
        """正常路径解析."""
        from flocks.sandbox.paths import resolve_sandbox_path

        result = resolve_sandbox_path(
            file_path="/sandbox/root/subdir/file.txt",
            cwd="/sandbox/root",
            root="/sandbox/root",
        )
        assert result.resolved == "/sandbox/root/subdir/file.txt"
        assert result.relative == "subdir/file.txt"

    def test_resolve_root_itself(self):
        """根目录自身."""
        from flocks.sandbox.paths import resolve_sandbox_path

        result = resolve_sandbox_path(
            file_path="/sandbox/root",
            cwd="/sandbox/root",
            root="/sandbox/root",
        )
        assert result.relative == ""

    def test_escape_detection(self):
        """路径逃逸检测."""
        from flocks.sandbox.paths import resolve_sandbox_path

        with pytest.raises(ValueError, match="escapes sandbox root"):
            resolve_sandbox_path(
                file_path="/sandbox/root/../outside",
                cwd="/sandbox/root",
                root="/sandbox/root",
            )

    def test_relative_path(self):
        """相对路径解析."""
        from flocks.sandbox.paths import resolve_sandbox_path

        result = resolve_sandbox_path(
            file_path="subdir/file.txt",
            cwd="/sandbox/root",
            root="/sandbox/root",
        )
        assert result.resolved == "/sandbox/root/subdir/file.txt"

    def test_tilde_expansion(self):
        """~ 路径展开."""
        from flocks.sandbox.paths import resolve_sandbox_path

        home = str(os.path.expanduser("~"))
        # 以 home 目录作为 root
        result = resolve_sandbox_path(
            file_path="~/subdir",
            cwd=home,
            root=home,
        )
        assert result.resolved == os.path.join(home, "subdir")

    def test_data_url_rejection(self):
        """data: URL 拒绝."""
        from flocks.sandbox.paths import assert_media_not_data_url

        with pytest.raises(ValueError, match="data: URLs"):
            assert_media_not_data_url("data:text/plain;base64,abc")


# ==================== 4. 工具策略测试 ====================


class TestToolPolicy:
    """工具策略测试."""

    def test_default_policy_allows_bash(self):
        """默认策略允许 bash."""
        from flocks.sandbox.tool_policy import is_tool_allowed, resolve_tool_policy

        policy = resolve_tool_policy()
        assert is_tool_allowed(policy, "bash") is True

    def test_default_policy_allows_delegate_task(self):
        """未配置策略时默认允许所有工具."""
        from flocks.sandbox.tool_policy import is_tool_allowed, resolve_tool_policy

        policy = resolve_tool_policy()
        assert is_tool_allowed(policy, "delegate_task") is True

    def test_deny_overrides_allow(self):
        """deny 优先于 allow."""
        from flocks.sandbox.tool_policy import is_tool_allowed
        from flocks.sandbox.types import SandboxToolPolicy

        policy = SandboxToolPolicy(allow=["*"], deny=["bash"])
        assert is_tool_allowed(policy, "bash") is False
        assert is_tool_allowed(policy, "read") is True

    def test_wildcard_allow(self):
        """通配符允许."""
        from flocks.sandbox.tool_policy import is_tool_allowed
        from flocks.sandbox.types import SandboxToolPolicy

        policy = SandboxToolPolicy(allow=["*"], deny=[])
        assert is_tool_allowed(policy, "anything") is True

    def test_empty_allow_allows_all(self):
        """空 allow 列表允许所有."""
        from flocks.sandbox.tool_policy import is_tool_allowed
        from flocks.sandbox.types import SandboxToolPolicy

        policy = SandboxToolPolicy(allow=None, deny=None)
        assert is_tool_allowed(policy, "bash") is True

    def test_glob_pattern(self):
        """通配符模式匹配."""
        from flocks.sandbox.tool_policy import is_tool_allowed
        from flocks.sandbox.types import SandboxToolPolicy

        policy = SandboxToolPolicy(allow=["session*"], deny=[])
        assert is_tool_allowed(policy, "sessions_list") is True
        assert is_tool_allowed(policy, "bash") is False

    def test_agent_overrides_global(self):
        """Agent 覆盖 global 策略."""
        from flocks.sandbox.tool_policy import resolve_tool_policy

        policy = resolve_tool_policy(
            global_allow=["bash", "read"],
            agent_allow=["bash"],
        )
        assert policy.allow == ["bash"]


# ==================== 5. Docker 参数构建测试 ====================


class TestDockerArgs:
    """Docker 参数构建测试."""

    def test_build_create_args_basic(self):
        """基本容器创建参数."""
        from flocks.sandbox.docker import build_sandbox_create_args
        from flocks.sandbox.types import SandboxDockerConfig

        cfg = SandboxDockerConfig()
        args = build_sandbox_create_args(
            name="test-container",
            cfg=cfg,
            scope_key="test",
        )
        assert "create" in args
        assert "--name" in args
        assert "test-container" in args
        assert "--read-only" in args
        assert "--network" in args
        assert "none" in args
        assert "--cap-drop" in args
        assert "ALL" in args
        assert "--security-opt" in args
        assert "no-new-privileges" in args

    def test_build_create_args_with_limits(self):
        """资源限制参数."""
        from flocks.sandbox.docker import build_sandbox_create_args
        from flocks.sandbox.types import SandboxDockerConfig

        cfg = SandboxDockerConfig(
            memory="512m",
            cpus=1.5,
            pids_limit=100,
        )
        args = build_sandbox_create_args(
            name="test",
            cfg=cfg,
            scope_key="test",
        )
        assert "--memory" in args
        assert "512m" in args
        assert "--cpus" in args
        assert "1.5" in args
        assert "--pids-limit" in args
        assert "100" in args

    def test_build_exec_args(self):
        """docker exec 参数构建."""
        from flocks.sandbox.docker import build_docker_exec_args

        args = build_docker_exec_args(
            container_name="my-container",
            command="echo hello",
            workdir="/workspace",
            env={"FOO": "bar"},
        )
        assert "exec" in args
        assert "-i" in args
        assert "-w" in args
        assert "/workspace" in args
        assert "my-container" in args
        # 命令通过 sh -lc 执行
        assert "sh" in args
        assert "-lc" in args

    def test_build_exec_args_with_custom_path(self):
        """docker exec PATH 注入."""
        from flocks.sandbox.docker import build_docker_exec_args

        args = build_docker_exec_args(
            container_name="my-container",
            command="my-cmd",
            env={"PATH": "/custom/bin"},
        )
        # 应该包含 FLOCKS_PREPEND_PATH
        args_str = " ".join(args)
        assert "FLOCKS_PREPEND_PATH" in args_str

    def test_build_sandbox_env(self):
        """沙箱环境变量构建."""
        from flocks.sandbox.docker import build_sandbox_env

        env = build_sandbox_env(
            default_path="/usr/bin:/bin",
            sandbox_env={"LANG": "C.UTF-8"},
            params_env={"MY_VAR": "value"},
            container_workdir="/workspace",
        )
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/workspace"
        assert env["LANG"] == "C.UTF-8"
        assert env["MY_VAR"] == "value"


# ==================== 6. 共享工具函数测试 ====================


class TestShared:
    """共享工具函数测试."""

    def test_slugify_session_key(self):
        """Session key slug 化."""
        from flocks.sandbox.shared import slugify_session_key

        slug = slugify_session_key("my-session")
        assert "my-session" in slug
        assert len(slug) > len("my-session")  # 包含哈希

    def test_slugify_empty(self):
        """空值 slug 化."""
        from flocks.sandbox.shared import slugify_session_key

        slug = slugify_session_key("")
        assert "session" in slug

    def test_scope_key_session(self):
        """session scope key."""
        from flocks.sandbox.shared import resolve_sandbox_scope_key

        key = resolve_sandbox_scope_key("session", "my-session")
        assert key == "my-session"

    def test_scope_key_shared(self):
        """shared scope key."""
        from flocks.sandbox.shared import resolve_sandbox_scope_key

        key = resolve_sandbox_scope_key("shared", "my-session")
        assert key == "shared"

    def test_scope_key_agent(self):
        """agent scope key."""
        from flocks.sandbox.shared import resolve_sandbox_scope_key

        key = resolve_sandbox_scope_key("agent", "rex:session-123")
        assert key == "agent:rex"

    def test_workspace_dir(self):
        """工作区目录解析."""
        from flocks.sandbox.shared import resolve_sandbox_workspace_dir

        path = resolve_sandbox_workspace_dir("/tmp/sandboxes", "my-session")
        assert path.startswith("/tmp/sandboxes/")
        assert "my-session" in path


# ==================== 7. 配置哈希测试 ====================


class TestConfigHash:
    """配置哈希测试."""

    def test_same_config_same_hash(self):
        """相同配置产生相同哈希."""
        from flocks.sandbox.config_hash import compute_sandbox_config_hash
        from flocks.sandbox.types import SandboxDockerConfig

        cfg = SandboxDockerConfig()
        hash1 = compute_sandbox_config_hash(cfg, "none", "/workspace", "/agent")
        hash2 = compute_sandbox_config_hash(cfg, "none", "/workspace", "/agent")
        assert hash1 == hash2

    def test_different_config_different_hash(self):
        """不同配置产生不同哈希."""
        from flocks.sandbox.config_hash import compute_sandbox_config_hash
        from flocks.sandbox.types import SandboxDockerConfig

        cfg1 = SandboxDockerConfig()
        cfg2 = SandboxDockerConfig(image="other:latest")
        hash1 = compute_sandbox_config_hash(cfg1, "none", "/workspace", "/agent")
        hash2 = compute_sandbox_config_hash(cfg2, "none", "/workspace", "/agent")
        assert hash1 != hash2

    def test_hash_length(self):
        """哈希长度 16 位."""
        from flocks.sandbox.config_hash import compute_sandbox_config_hash
        from flocks.sandbox.types import SandboxDockerConfig

        cfg = SandboxDockerConfig()
        h = compute_sandbox_config_hash(cfg, "none", "/workspace", "/agent")
        assert len(h) == 16

    def test_workspace_access_affects_hash(self):
        """workspace_access 影响哈希."""
        from flocks.sandbox.config_hash import compute_sandbox_config_hash
        from flocks.sandbox.types import SandboxDockerConfig

        cfg = SandboxDockerConfig()
        hash_none = compute_sandbox_config_hash(cfg, "none", "/workspace", "/agent")
        hash_rw = compute_sandbox_config_hash(cfg, "rw", "/workspace", "/agent")
        assert hash_none != hash_rw


# ==================== 8. 环境变量安全测试 ====================


class TestEnvSecurity:
    """环境变量安全测试."""

    def test_rejects_ld_preload(self):
        """拒绝 LD_PRELOAD."""
        from flocks.sandbox.env_security import validate_host_env

        with pytest.raises(ValueError, match="Security Violation"):
            validate_host_env({"LD_PRELOAD": "/evil.so"})

    def test_rejects_pythonpath(self):
        """拒绝 PYTHONPATH."""
        from flocks.sandbox.env_security import validate_host_env

        with pytest.raises(ValueError, match="Security Violation"):
            validate_host_env({"PYTHONPATH": "/evil"})

    def test_rejects_path(self):
        """拒绝 PATH."""
        from flocks.sandbox.env_security import validate_host_env

        with pytest.raises(ValueError, match="Security Violation"):
            validate_host_env({"PATH": "/evil/bin"})

    def test_rejects_dyld_prefix(self):
        """拒绝 DYLD_ 前缀."""
        from flocks.sandbox.env_security import validate_host_env

        with pytest.raises(ValueError, match="Security Violation"):
            validate_host_env({"DYLD_SOMETHING": "value"})

    def test_allows_safe_vars(self):
        """允许安全变量."""
        from flocks.sandbox.env_security import validate_host_env

        # 不应抛出异常
        validate_host_env({"FOO": "bar", "MY_VAR": "value"})


# ==================== 9. 类型测试 ====================


class TestTypes:
    """类型定义测试."""

    def test_sandbox_config_defaults(self):
        """SandboxConfig 默认值."""
        from flocks.sandbox.types import SandboxConfig

        cfg = SandboxConfig()
        assert cfg.mode == "off"
        assert cfg.scope == "agent"
        assert cfg.workspace_access == "none"
        assert cfg.docker.image == "python:slim"

    def test_sandbox_context(self):
        """SandboxContext 创建."""
        from flocks.sandbox.types import (
            SandboxContext,
            SandboxDockerConfig,
            SandboxToolPolicy,
        )

        ctx = SandboxContext(
            enabled=True,
            session_key="test",
            workspace_dir="/workspace",
            agent_workspace_dir="/home/user",
            workspace_access="rw",
            container_name="flocks-sbx-test",
            container_workdir="/workspace",
            docker=SandboxDockerConfig(),
            tools=SandboxToolPolicy(),
        )
        assert ctx.enabled is True
        assert ctx.container_name == "flocks-sbx-test"

    def test_bash_sandbox_config(self):
        """BashSandboxConfig 创建."""
        from flocks.sandbox.types import BashSandboxConfig

        cfg = BashSandboxConfig(
            container_name="test",
            workspace_dir="/workspace",
            container_workdir="/workspace",
            env={"FOO": "bar"},
        )
        assert cfg.container_name == "test"
        assert cfg.env == {"FOO": "bar"}


# ==================== 10. Registry 测试 ====================


class TestRegistry:
    """注册表测试."""

    def test_registry_entry_roundtrip(self):
        """注册表条目序列化/反序列化."""
        from flocks.sandbox.registry import RegistryEntry

        entry = RegistryEntry(
            container_name="test",
            session_key="session",
            created_at_ms=1000.0,
            last_used_at_ms=2000.0,
            image="test:latest",
            config_hash="abc123",
        )
        data = entry.to_dict()
        restored = RegistryEntry.from_dict(data)
        assert restored.container_name == "test"
        assert restored.config_hash == "abc123"

    def test_registry_roundtrip(self):
        """注册表整体序列化/反序列化."""
        from flocks.sandbox.registry import Registry, RegistryEntry

        registry = Registry()
        registry.entries.append(
            RegistryEntry(
                container_name="c1",
                session_key="s1",
                created_at_ms=1000.0,
                last_used_at_ms=2000.0,
                image="img",
            )
        )
        data = registry.to_dict()
        restored = Registry.from_dict(data)
        assert len(restored.entries) == 1
        assert restored.entries[0].container_name == "c1"


# ==================== 11. 提升执行 (Elevated) 配置测试 ====================


class TestElevatedConfig:
    """提升执行配置测试."""

    def test_elevated_default_disabled(self):
        """默认 elevated 关闭."""
        from flocks.sandbox.types import SandboxElevatedConfig

        cfg = SandboxElevatedConfig()
        assert cfg.enabled is False
        assert cfg.tools is None

    def test_elevated_in_sandbox_config(self):
        """SandboxConfig 包含 elevated 字段."""
        from flocks.sandbox.types import SandboxConfig

        cfg = SandboxConfig()
        assert cfg.elevated.enabled is False

    def test_elevated_global_config_resolution(self):
        """全局 elevated 配置解析."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "mode": "on",
                "elevated": {
                    "enabled": True,
                    "tools": ["bash", "write"],
                },
            }
        }
        cfg = resolve_sandbox_config_for_agent(config_data)
        assert cfg.elevated.enabled is True
        assert cfg.elevated.tools == ["bash", "write"]

    def test_elevated_agent_overrides_global(self):
        """Agent 级 elevated 覆盖 global."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {
            "sandbox": {
                "mode": "on",
                "elevated": {"enabled": True, "tools": ["bash"]},
            },
            "agent": {
                "rex": {
                    "sandbox": {
                        "elevated": {"enabled": False},
                    }
                }
            },
        }
        cfg = resolve_sandbox_config_for_agent(config_data, agent_id="rex")
        assert cfg.elevated.enabled is False

    def test_elevated_disabled_when_not_specified(self):
        """未指定 elevated 时默认关闭."""
        from flocks.sandbox.config import resolve_sandbox_config_for_agent

        config_data = {"sandbox": {"mode": "on"}}
        cfg = resolve_sandbox_config_for_agent(config_data)
        assert cfg.elevated.enabled is False


# ==================== 12. 系统提示词测试 ====================


class TestSystemPrompt:
    """系统提示词构建测试."""

    @pytest.mark.asyncio
    async def test_returns_none_when_not_sandboxed(self):
        """sandbox mode=off 时返回 None."""
        from flocks.sandbox.system_prompt import build_sandbox_system_prompt

        result = await build_sandbox_system_prompt(
            config_data={"sandbox": {"mode": "off"}},
            session_key="test",
            agent_id="rex",
            main_session_key="main",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_session(self):
        """空 session_key 时返回 None."""
        from flocks.sandbox.system_prompt import build_sandbox_system_prompt

        result = await build_sandbox_system_prompt(
            config_data={"sandbox": {"mode": "on"}},
            session_key="",
            agent_id="rex",
            main_session_key="main",
        )
        assert result is None


# ==================== 13. Read/Write/Edit 沙箱路径解析测试 ====================


class TestFileToolSandboxPaths:
    """文件工具沙箱路径限制测试."""

    @pytest.mark.asyncio
    async def test_read_sandbox_path_resolution(self):
        """read 工具的沙箱路径解析 — 正常路径."""
        from flocks.tool.file.read import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": {"workspace_dir": "/tmp/sandbox_ws"}},
        )
        resolved, error = await _resolve_sandbox_file_path(
            ctx, "/tmp/sandbox_ws/file.txt"
        )
        assert error is None
        assert resolved == "/tmp/sandbox_ws/file.txt"

    @pytest.mark.asyncio
    async def test_read_sandbox_blocks_escape(self):
        """read 工具的沙箱路径逃逸检测."""
        from flocks.tool.file.read import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": {"workspace_dir": "/tmp/sandbox_ws"}},
        )
        resolved, error = await _resolve_sandbox_file_path(
            ctx, "/tmp/sandbox_ws/../etc/passwd"
        )
        assert resolved is None
        assert "escapes sandbox" in error

    @pytest.mark.asyncio
    async def test_read_no_sandbox_passthrough(self):
        """无沙箱时路径直接透传."""
        from flocks.tool.file.read import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={},
        )
        resolved, error = await _resolve_sandbox_file_path(ctx, "/any/path/file.txt")
        assert error is None
        assert resolved == "/any/path/file.txt"

    @pytest.mark.asyncio
    async def test_read_relative_path_joined(self):
        """read 相对路径拼接到 sandbox workspace_dir."""
        from flocks.tool.file.read import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": {"workspace_dir": "/tmp/sandbox_ws"}},
        )
        resolved, error = await _resolve_sandbox_file_path(ctx, "subdir/file.txt")
        assert error is None
        assert resolved == "/tmp/sandbox_ws/subdir/file.txt"

    @pytest.mark.asyncio
    async def test_write_sandbox_path_resolution(self):
        """write 工具的沙箱路径解析."""
        from flocks.tool.file.write import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": {"workspace_dir": "/tmp/sandbox_ws"}},
        )
        resolved, error, sandbox = await _resolve_sandbox_file_path(
            ctx, "/tmp/sandbox_ws/new_file.txt"
        )
        assert error is None
        assert resolved == "/tmp/sandbox_ws/new_file.txt"
        assert isinstance(sandbox, dict)

    @pytest.mark.asyncio
    async def test_write_sandbox_blocks_escape(self):
        """write 工具的沙箱路径逃逸检测."""
        from flocks.tool.file.write import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": {"workspace_dir": "/tmp/sandbox_ws"}},
        )
        resolved, error, _ = await _resolve_sandbox_file_path(
            ctx, "/tmp/sandbox_ws/../../etc/malicious"
        )
        assert resolved is None
        assert "escapes sandbox" in error

    @pytest.mark.asyncio
    async def test_write_sandbox_ro_returns_dict(self):
        """write ro 模式下返回 sandbox dict 以便后续检查."""
        from flocks.tool.file.write import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={
                "sandbox": {
                    "workspace_dir": "/tmp/sandbox_ws",
                    "workspace_access": "ro",
                }
            },
        )
        resolved, error, sandbox = await _resolve_sandbox_file_path(
            ctx, "/tmp/sandbox_ws/file.txt"
        )
        assert error is None
        assert sandbox["workspace_access"] == "ro"

    @pytest.mark.asyncio
    async def test_edit_sandbox_blocks_escape(self):
        """edit 工具的沙箱路径逃逸检测."""
        from flocks.tool.file.edit import _resolve_sandbox_file_path
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": {"workspace_dir": "/tmp/sandbox_ws"}},
        )
        resolved, error, _ = await _resolve_sandbox_file_path(
            ctx, "/outside/path/file.txt"
        )
        assert resolved is None
        assert "escapes sandbox" in error


# ==================== 14. Bash 沙箱辅助函数测试 ====================


class TestBashSandboxHelpers:
    """Bash 沙箱辅助函数测试."""

    def test_get_sandbox_config_from_ctx_dict(self):
        """从 ctx.extra dict 中提取 BashSandboxConfig."""
        from flocks.tool.code.bash import _get_sandbox_config_from_ctx
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={
                "sandbox": {
                    "container_name": "flocks-sbx-test",
                    "workspace_dir": "/tmp/ws",
                    "container_workdir": "/workspace",
                }
            },
        )
        cfg = _get_sandbox_config_from_ctx(ctx)
        assert cfg is not None
        assert cfg.container_name == "flocks-sbx-test"
        assert cfg.workspace_dir == "/tmp/ws"

    def test_get_sandbox_config_from_ctx_none(self):
        """无沙箱配置时返回 None."""
        from flocks.tool.code.bash import _get_sandbox_config_from_ctx
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={},
        )
        cfg = _get_sandbox_config_from_ctx(ctx)
        assert cfg is None

    def test_get_sandbox_config_from_ctx_model(self):
        """从 BashSandboxConfig 实例提取."""
        from flocks.sandbox.types import BashSandboxConfig
        from flocks.tool.code.bash import _get_sandbox_config_from_ctx
        from flocks.tool.registry import ToolContext

        model = BashSandboxConfig(
            container_name="flocks-sbx-m",
            workspace_dir="/ws",
            container_workdir="/workspace",
        )
        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={"sandbox": model},
        )
        cfg = _get_sandbox_config_from_ctx(ctx)
        assert cfg is not None
        assert cfg.container_name == "flocks-sbx-m"

    def test_is_elevated_allowed_enabled(self):
        """elevated 已启用且工具在列表中."""
        from flocks.tool.code.bash import _is_elevated_allowed
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={
                "sandbox_elevated": {
                    "enabled": True,
                    "tools": ["bash"],
                }
            },
        )
        assert _is_elevated_allowed(ctx, "bash") is True
        assert _is_elevated_allowed(ctx, "read") is False

    def test_is_elevated_allowed_disabled(self):
        """elevated 未启用."""
        from flocks.tool.code.bash import _is_elevated_allowed
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={
                "sandbox_elevated": {
                    "enabled": False,
                    "tools": ["bash"],
                }
            },
        )
        assert _is_elevated_allowed(ctx, "bash") is False

    def test_is_elevated_allowed_no_extra(self):
        """extra 中没有 sandbox_elevated."""
        from flocks.tool.code.bash import _is_elevated_allowed
        from flocks.tool.registry import ToolContext

        ctx = ToolContext(
            session_id="test",
            message_id="msg1",
            extra={},
        )
        assert _is_elevated_allowed(ctx, "bash") is False


# ==================== 15. StreamProcessor 沙箱缓存测试 ====================


class TestStreamProcessorSandboxMeta:
    """StreamProcessor 沙箱元数据解析 & 缓存测试."""

    @pytest.mark.asyncio
    async def test_sandbox_meta_not_sandboxed(self):
        """mode=off 时不沙箱化，返回空 extra."""
        from unittest.mock import AsyncMock, MagicMock

        from flocks.session.streaming.stream_processor import StreamProcessor

        agent = MagicMock()
        agent.name = "rex"
        msg = MagicMock()
        msg.id = "msg1"

        processor = StreamProcessor(
            session_id="sess1",
            assistant_message=msg,
            agent=agent,
            permission_callback=AsyncMock(),
            config_data={"sandbox": {"mode": "off"}},
            session_key="sess1",
            main_session_key="sess1",
        )
        result = await processor._resolve_sandbox_meta("bash")
        assert result["blocked"] is False
        assert result["extra"] == {}

    @pytest.mark.asyncio
    async def test_sandbox_meta_tool_blocked_by_policy(self):
        """工具被策略阻断."""
        from unittest.mock import AsyncMock, MagicMock

        from flocks.session.streaming.stream_processor import StreamProcessor

        agent = MagicMock()
        agent.name = "rex"
        msg = MagicMock()
        msg.id = "msg1"

        processor = StreamProcessor(
            session_id="sess1",
            assistant_message=msg,
            agent=agent,
            permission_callback=AsyncMock(),
            config_data={
                "sandbox": {
                    "mode": "on",
                    "tools": {"allow": ["bash"], "deny": ["delegate_task"]},
                },
            },
            session_key="test-session",
            main_session_key="main-session",
        )
        result = await processor._resolve_sandbox_meta("delegate_task")
        assert result["blocked"] is True
        assert "blocked by sandbox tool policy" in result["error"]

    @pytest.mark.asyncio
    async def test_sandbox_meta_non_file_tool_passes(self):
        """非 bash/read/write/edit 工具放行但无 extra."""
        from unittest.mock import AsyncMock, MagicMock

        from flocks.session.streaming.stream_processor import StreamProcessor

        agent = MagicMock()
        agent.name = "rex"
        msg = MagicMock()
        msg.id = "msg1"

        processor = StreamProcessor(
            session_id="sess1",
            assistant_message=msg,
            agent=agent,
            permission_callback=AsyncMock(),
            config_data={
                "sandbox": {"mode": "on"},
            },
            session_key="test-session",
            main_session_key="main-session",
        )
        result = await processor._resolve_sandbox_meta("grep")
        assert result["blocked"] is False
        assert result["extra"] == {}

    @pytest.mark.asyncio
    async def test_sandbox_runtime_cache(self):
        """runtime_status 缓存: 多次调用只解析一次."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from flocks.session.streaming.stream_processor import StreamProcessor

        agent = MagicMock()
        agent.name = "rex"
        msg = MagicMock()
        msg.id = "msg1"

        processor = StreamProcessor(
            session_id="sess1",
            assistant_message=msg,
            agent=agent,
            permission_callback=AsyncMock(),
            config_data={"sandbox": {"mode": "off"}},
            session_key="sess1",
            main_session_key="sess1",
        )

        with patch(
            "flocks.sandbox.runtime_status.resolve_sandbox_runtime_status",
            wraps=__import__(
                "flocks.sandbox.runtime_status",
                fromlist=["resolve_sandbox_runtime_status"],
            ).resolve_sandbox_runtime_status,
        ) as mock_resolve:
            await processor._resolve_sandbox_meta("bash")
            await processor._resolve_sandbox_meta("read")
            # 只调用 resolve_sandbox_runtime_status 一次（缓存生效）
            assert mock_resolve.call_count == 1


# ==================== 16. Workspace 工具测试 ====================


class TestWorkspace:
    """沙箱工作区管理测试."""

    @pytest.mark.asyncio
    async def test_ensure_sandbox_workspace_creates_dir(self):
        """确保工作区目录被创建."""
        from flocks.sandbox.workspace import ensure_sandbox_workspace

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_dir = os.path.join(tmpdir, "sandbox_ws")
            await ensure_sandbox_workspace(workspace_dir=ws_dir)
            assert os.path.isdir(ws_dir)

    @pytest.mark.asyncio
    async def test_ensure_sandbox_workspace_idempotent(self):
        """多次调用不报错."""
        from flocks.sandbox.workspace import ensure_sandbox_workspace

        with tempfile.TemporaryDirectory() as tmpdir:
            ws_dir = os.path.join(tmpdir, "sandbox_ws")
            await ensure_sandbox_workspace(workspace_dir=ws_dir)
            await ensure_sandbox_workspace(workspace_dir=ws_dir)
            assert os.path.isdir(ws_dir)


# ==================== 17. 注册表文件 I/O 测试 ====================


class TestRegistryIO:
    """注册表文件 I/O 测试."""

    @pytest.mark.asyncio
    async def test_read_empty_registry(self, monkeypatch):
        """读取不存在的注册表返回空."""
        import flocks.sandbox.registry as reg_mod
        from flocks.sandbox.registry import read_registry

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(
                reg_mod,
                "SANDBOX_REGISTRY_PATH",
                os.path.join(tmpdir, "nonexistent.json"),
            )
            registry = await read_registry()
            assert len(registry.entries) == 0

    @pytest.mark.asyncio
    async def test_write_and_read_registry(self, monkeypatch):
        """写入并读回注册表."""
        import flocks.sandbox.registry as reg_mod
        from flocks.sandbox.registry import (
            Registry,
            RegistryEntry,
            read_registry,
            write_registry,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "registry.json")
            monkeypatch.setattr(reg_mod, "SANDBOX_REGISTRY_PATH", path)
            monkeypatch.setattr(reg_mod, "SANDBOX_STATE_DIR", tmpdir)

            registry = Registry()
            registry.entries.append(
                RegistryEntry(
                    container_name="c1",
                    session_key="s1",
                    created_at_ms=1000.0,
                    last_used_at_ms=2000.0,
                    image="img:latest",
                    config_hash="hash123",
                )
            )
            await write_registry(registry)

            restored = await read_registry()
            assert len(restored.entries) == 1
            assert restored.entries[0].container_name == "c1"
            assert restored.entries[0].config_hash == "hash123"
