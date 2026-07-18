"""
沙箱类型定义

对齐 OpenClaw sandbox/types.ts + types.docker.ts。
使用 Pydantic BaseModel 与 flocks 现有配置风格保持一致。
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ==================== 基础类型别名 ====================

SandboxMode = Literal["off", "on"]
SandboxScope = Literal["session", "agent", "shared"]
WorkspaceAccess = Literal["none", "ro", "rw"]


# ==================== Docker 配置 ====================


class SandboxDockerConfig(BaseModel):
    """Docker 容器配置，对齐 OpenClaw SandboxDockerConfig。"""

    model_config = {"extra": "allow"}

    image: str = Field(
        default="python:slim",
        description="沙箱容器镜像",
    )
    container_prefix: str = Field(
        default="flocks-sbx-",
        description="容器名称前缀",
    )
    workdir: str = Field(
        default="/workspace",
        description="容器内工作目录",
    )
    read_only_root: bool = Field(
        default=True,
        description="只读根文件系统",
    )
    tmpfs: List[str] = Field(
        default_factory=lambda: ["/tmp", "/var/tmp", "/run"],
        description="tmpfs 挂载列表",
    )
    network: str = Field(
        default="none",
        description="网络模式 (none / bridge / host / 自定义网络名)",
    )
    user: Optional[str] = Field(
        default=None,
        description="容器用户 (e.g. sandbox / 1000:1000)",
    )
    cap_drop: List[str] = Field(
        default_factory=lambda: ["ALL"],
        description="需要丢弃的 Linux capability",
    )
    env: Optional[Dict[str, str]] = Field(
        default=None,
        description="注入容器的环境变量",
    )
    setup_command: Optional[str] = Field(
        default=None,
        description="容器首次创建后执行的初始化命令",
    )
    pids_limit: Optional[int] = Field(
        default=None,
        description="最大进程数",
    )
    memory: Optional[str] = Field(
        default=None,
        description="内存限制 (e.g. 512m, 1g)",
    )
    memory_swap: Optional[str] = Field(
        default=None,
        description="内存 + Swap 限制",
    )
    cpus: Optional[float] = Field(
        default=None,
        description="CPU 限制 (e.g. 0.5, 2.0)",
    )
    ulimits: Optional[Dict[str, str]] = Field(
        default=None,
        description="ulimit 设置 (name=soft:hard)",
    )
    seccomp_profile: Optional[str] = Field(
        default=None,
        description="seccomp 配置文件路径",
    )
    apparmor_profile: Optional[str] = Field(
        default=None,
        description="AppArmor 配置文件",
    )
    dns: Optional[List[str]] = Field(
        default=None,
        description="DNS 服务器列表",
    )
    extra_hosts: Optional[List[str]] = Field(
        default=None,
        description="额外的 host 条目 (host:ip)",
    )
    binds: Optional[List[str]] = Field(
        default=None,
        description="额外的 bind mount (host:container:mode)",
    )


# ==================== 工具策略 ====================


class SandboxToolPolicy(BaseModel):
    """沙箱工具允许/拒绝策略，对齐 OpenClaw SandboxToolPolicy。"""

    allow: Optional[List[str]] = Field(
        default=None,
        description="允许的工具列表 (支持通配符 *)",
    )
    deny: Optional[List[str]] = Field(
        default=None,
        description="拒绝的工具列表 (支持通配符 *)",
    )


class SandboxElevatedConfig(BaseModel):
    """沙箱内提升执行配置（逃逸到宿主机）."""

    enabled: bool = Field(
        default=False,
        description="是否允许在沙箱会话中提升到宿主机执行",
    )
    tools: Optional[List[str]] = Field(
        default=None,
        description="允许提升执行的工具列表（默认 ['bash']）",
    )


# ==================== 自动清理配置 ====================


class SandboxPruneConfig(BaseModel):
    """容器自动清理配置，对齐 OpenClaw SandboxPruneConfig。"""

    idle_hours: int = Field(
        default=24,
        description="空闲多少小时后清理",
    )
    max_age_days: int = Field(
        default=7,
        description="最大存活天数",
    )


# ==================== 沙箱主配置 ====================


class SandboxConfig(BaseModel):
    """
    沙箱主配置，对齐 OpenClaw SandboxConfig。

    层级: agents.defaults.sandbox → agents.list[].sandbox → 硬编码默认值
    """

    model_config = {"extra": "allow"}

    mode: SandboxMode = Field(
        default="off",
        description="沙箱模式: off / on",
    )
    scope: SandboxScope = Field(
        default="agent",
        description="容器隔离级别: session / agent / shared",
    )
    workspace_access: WorkspaceAccess = Field(
        default="none",
        description="工作区访问: none / ro / rw",
    )
    workspace_root: Optional[str] = Field(
        default=None,
        description="沙箱工作区根目录 (默认 ~/.flocks/sandboxes)",
    )
    docker: SandboxDockerConfig = Field(
        default_factory=SandboxDockerConfig,
        description="Docker 容器配置",
    )
    tools: SandboxToolPolicy = Field(
        default_factory=SandboxToolPolicy,
        description="工具策略",
    )
    elevated: SandboxElevatedConfig = Field(
        default_factory=SandboxElevatedConfig,
        description="提升执行策略",
    )
    prune: SandboxPruneConfig = Field(
        default_factory=SandboxPruneConfig,
        description="自动清理配置",
    )


# ==================== 运行时上下文 ====================


class SandboxContext(BaseModel):
    """
    沙箱运行时上下文，对齐 OpenClaw SandboxContext。

    由 context.resolve_sandbox_context() 组装，传递给工具执行层。
    """

    enabled: bool = Field(default=True)
    session_key: str = Field(description="会话标识")
    workspace_dir: str = Field(description="实际工作目录 (宿主机路径)")
    agent_workspace_dir: str = Field(description="Agent 工作目录 (宿主机路径)")
    workspace_access: WorkspaceAccess = Field(description="工作区访问模式")
    container_name: str = Field(description="Docker 容器名称")
    container_workdir: str = Field(description="容器内工作目录")
    docker: SandboxDockerConfig = Field(description="Docker 配置")
    tools: SandboxToolPolicy = Field(description="工具策略")


class SandboxWorkspaceInfo(BaseModel):
    """沙箱工作区信息（轻量版，仅路径）。"""

    workspace_dir: str
    container_workdir: str


class BashSandboxConfig(BaseModel):
    """传递给 bash 工具的精简沙箱配置。"""

    container_name: str
    workspace_dir: str
    container_workdir: str
    env: Optional[Dict[str, str]] = None
