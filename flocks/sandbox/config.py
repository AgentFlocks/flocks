"""
沙箱配置解析

对齐 OpenClaw sandbox/config.ts：
- 从全局/agent 配置中解析沙箱设置
- 层级: agents.defaults.sandbox → agents.list[].sandbox → 硬编码默认值
- scope === "shared" 时忽略 agent 级 docker 覆写
"""

from typing import Any, Dict, Optional

from .defaults import (
    DEFAULT_SANDBOX_CONTAINER_PREFIX,
    DEFAULT_SANDBOX_IDLE_HOURS,
    DEFAULT_SANDBOX_IMAGE,
    DEFAULT_SANDBOX_MAX_AGE_DAYS,
    DEFAULT_SANDBOX_WORKDIR,
    DEFAULT_SANDBOX_WORKSPACE_ROOT,
)
from .types import (
    SandboxConfig,
    SandboxDockerConfig,
    SandboxElevatedConfig,
    SandboxMode,
    SandboxPruneConfig,
    SandboxScope,
    SandboxToolPolicy,
)


def resolve_sandbox_mode(mode: Optional[str] = None) -> SandboxMode:
    """
    解析 sandbox mode，统一为 off/on。

    兼容历史值:
    - all -> on
    - non-main -> on
    """
    normalized = (mode or "").strip().lower()
    if normalized in ("on", "all", "non-main"):
        return "on"
    return "off"


def resolve_sandbox_scope(
    scope: Optional[str] = None,
) -> SandboxScope:
    """解析 sandbox scope，默认 agent."""
    if scope in ("session", "agent", "shared"):
        return scope  # type: ignore[return-value]
    return "agent"


def resolve_sandbox_docker_config(
    scope: SandboxScope,
    global_docker: Optional[Dict[str, Any]] = None,
    agent_docker: Optional[Dict[str, Any]] = None,
) -> SandboxDockerConfig:
    """
    解析 Docker 配置。

    对齐 OpenClaw：scope === "shared" 时忽略 agent 级 docker 覆写。
    """
    # shared scope 下不允许 agent 级覆写
    agent = agent_docker if scope != "shared" else None
    g = global_docker or {}

    env_global = g.get("env", {"LANG": "C.UTF-8"})
    env_agent = agent.get("env", {}) if agent else {}
    merged_env = {**env_global, **env_agent} if env_agent else env_global

    binds_global = g.get("binds") or []
    binds_agent = (agent.get("binds") or []) if agent else []
    merged_binds = binds_global + binds_agent

    def _pick(key: str, default: Any = None) -> Any:
        if agent and key in agent:
            return agent[key]
        return g.get(key, default)

    return SandboxDockerConfig(
        image=_pick("image", DEFAULT_SANDBOX_IMAGE),
        container_prefix=_pick("container_prefix", DEFAULT_SANDBOX_CONTAINER_PREFIX),
        workdir=_pick("workdir", DEFAULT_SANDBOX_WORKDIR),
        read_only_root=_pick("read_only_root", True),
        tmpfs=_pick("tmpfs", ["/tmp", "/var/tmp", "/run"]),
        network=_pick("network", "none"),
        user=_pick("user"),
        cap_drop=_pick("cap_drop", ["ALL"]),
        cap_add=_pick("cap_add", []),
        env=merged_env if merged_env else None,
        setup_command=_pick("setup_command"),
        pids_limit=_pick("pids_limit"),
        memory=_pick("memory"),
        memory_swap=_pick("memory_swap"),
        cpus=_pick("cpus"),
        ulimits=_pick("ulimits"),
        seccomp_profile=_pick("seccomp_profile"),
        apparmor_profile=_pick("apparmor_profile"),
        dns=_pick("dns"),
        extra_hosts=_pick("extra_hosts"),
        binds=merged_binds if merged_binds else None,
    )


def resolve_sandbox_prune_config(
    scope: SandboxScope,
    global_prune: Optional[Dict[str, Any]] = None,
    agent_prune: Optional[Dict[str, Any]] = None,
) -> SandboxPruneConfig:
    """解析自动清理配置."""
    agent = agent_prune if scope != "shared" else None
    g = global_prune or {}

    def _pick(key: str, default: Any = None) -> Any:
        if agent and key in agent:
            return agent[key]
        return g.get(key, default)

    return SandboxPruneConfig(
        idle_hours=_pick("idle_hours", DEFAULT_SANDBOX_IDLE_HOURS),
        max_age_days=_pick("max_age_days", DEFAULT_SANDBOX_MAX_AGE_DAYS),
    )


def resolve_sandbox_config_for_agent(
    config_data: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
) -> SandboxConfig:
    """
    解析指定 agent 的沙箱配置。

    对齐 OpenClaw resolveSandboxConfigForAgent：
    1. 从 agents.defaults.sandbox 获取全局默认
    2. 从 agents.list[agent_id].sandbox 获取 agent 覆写
    3. 合并

    Args:
        config_data: 完整配置字典 (ConfigInfo.model_dump())
        agent_id: Agent 标识

    Returns:
        解析后的 SandboxConfig
    """
    if config_data is None:
        return SandboxConfig()

    # 全局默认
    agents_data = config_data.get("agent", {}) or {}
    # 尝试从 sandbox 顶级字段获取
    global_sandbox = config_data.get("sandbox", {}) or {}

    # Agent 特定覆写
    agent_sandbox: Dict[str, Any] = {}
    if agent_id and agent_id in agents_data:
        agent_config = agents_data[agent_id]
        if isinstance(agent_config, dict):
            agent_sandbox = agent_config.get("sandbox", {}) or {}

    # 解析 scope
    scope = resolve_sandbox_scope(
        agent_sandbox.get("scope") or global_sandbox.get("scope")
    )

    # 解析 docker
    docker = resolve_sandbox_docker_config(
        scope=scope,
        global_docker=global_sandbox.get("docker"),
        agent_docker=agent_sandbox.get("docker"),
    )

    # 解析 prune
    prune = resolve_sandbox_prune_config(
        scope=scope,
        global_prune=global_sandbox.get("prune"),
        agent_prune=agent_sandbox.get("prune"),
    )

    # 解析工具策略
    global_tools = global_sandbox.get("tools", {}) or {}
    agent_tools = agent_sandbox.get("tools", {}) or {}
    allow = (
        agent_tools["allow"]
        if "allow" in agent_tools
        else global_tools.get("allow")
    )
    deny = (
        agent_tools["deny"]
        if "deny" in agent_tools
        else global_tools.get("deny")
    )
    tools = SandboxToolPolicy(allow=allow, deny=deny)

    # 解析提升执行策略
    global_elevated = global_sandbox.get("elevated", {}) or {}
    agent_elevated = agent_sandbox.get("elevated", {}) or {}
    elevated = SandboxElevatedConfig(
        enabled=bool(
            agent_elevated.get("enabled")
            if "enabled" in agent_elevated
            else global_elevated.get("enabled", False)
        ),
        tools=agent_elevated.get("tools") or global_elevated.get("tools"),
    )

    return SandboxConfig(
        mode=resolve_sandbox_mode(
            agent_sandbox.get("mode") or global_sandbox.get("mode", "off")
        ),
        scope=scope,
        workspace_access=(
            agent_sandbox.get("workspace_access")
            or global_sandbox.get("workspace_access", "none")
        ),
        workspace_root=(
            agent_sandbox.get("workspace_root")
            or global_sandbox.get("workspace_root")
        ),
        docker=docker,
        tools=tools,
        elevated=elevated,
        prune=prune,
    )
