"""
Flocks 沙箱系统

对齐 OpenClaw sandbox 模块，提供 Docker 容器隔离的工具执行环境。

核心能力:
- mode: off / on (控制是否启用沙箱)
- scope: session / agent / shared (控制容器隔离级别)
- workspaceAccess: none / ro / rw (控制工作区访问)
- 工具策略: allow / deny 列表
- Docker CLI 交互 (不使用 SDK)
- 路径安全 (防逃逸/防 symlink)
- 容器注册表 + 自动清理

使用方式:
    from flocks.sandbox import resolve_sandbox_context, SandboxContext

    ctx = await resolve_sandbox_context(
        config_data=config_dict,
        session_key="my-session",
        agent_id="rex",
        main_session_key="main",
    )
    if ctx:
        # 沙箱化执行
        ...
"""

from .context import resolve_sandbox_context, ensure_sandbox_workspace_for_session
from .config import resolve_sandbox_config_for_agent
from .docker import (
    build_docker_exec_args,
    build_sandbox_env,
    ensure_sandbox_container,
    exec_docker,
)
from .env_security import validate_host_env
from .paths import assert_sandbox_path, resolve_sandbox_path
from .prune import maybe_prune_sandboxes, prune_sandboxes
from .registry import read_registry, update_registry
from .runtime_status import resolve_sandbox_runtime_status, SandboxRuntimeStatus
from .shared import (
    resolve_sandbox_scope_key,
    resolve_sandbox_workspace_dir,
    slugify_session_key,
)
from .tool_policy import build_tool_policy_metadata
from .system_prompt import build_sandbox_system_prompt
from .types import (
    BashSandboxConfig,
    SandboxConfig,
    SandboxContext,
    SandboxDockerConfig,
    SandboxElevatedConfig,
    SandboxMode,
    SandboxScope,
    SandboxToolPolicy,
    SandboxWorkspaceInfo,
    WorkspaceAccess,
)

__all__ = [
    # Context
    "resolve_sandbox_context",
    "ensure_sandbox_workspace_for_session",
    # Config
    "resolve_sandbox_config_for_agent",
    # Docker
    "build_docker_exec_args",
    "build_sandbox_env",
    "ensure_sandbox_container",
    "exec_docker",
    # Security
    "validate_host_env",
    "assert_sandbox_path",
    "resolve_sandbox_path",
    # Prune
    "maybe_prune_sandboxes",
    "prune_sandboxes",
    # Registry
    "read_registry",
    "update_registry",
    # Runtime
    "resolve_sandbox_runtime_status",
    "SandboxRuntimeStatus",
    # Shared
    "resolve_sandbox_scope_key",
    "resolve_sandbox_workspace_dir",
    "slugify_session_key",
    # Opaque policy metadata
    "build_tool_policy_metadata",
    "build_sandbox_system_prompt",
    # Types
    "BashSandboxConfig",
    "SandboxConfig",
    "SandboxContext",
    "SandboxDockerConfig",
    "SandboxElevatedConfig",
    "SandboxMode",
    "SandboxScope",
    "SandboxToolPolicy",
    "SandboxWorkspaceInfo",
    "WorkspaceAccess",
]
