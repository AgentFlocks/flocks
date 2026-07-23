"""
沙箱配置哈希

对齐 OpenClaw sandbox/config-hash.ts：
- 计算容器配置哈希，用于检测配置变更并触发容器重建
"""

import hashlib
import json
from typing import Optional

from .types import SandboxDockerConfig, WorkspaceAccess


def compute_sandbox_config_hash(
    docker: SandboxDockerConfig,
    workspace_access: WorkspaceAccess,
    workspace_dir: str,
    agent_workspace_dir: str,
) -> str:
    """
    计算沙箱配置哈希。

    当哈希变更时，容器需要重建。

    Args:
        docker: Docker 配置
        workspace_access: 工作区访问模式
        workspace_dir: 工作区目录
        agent_workspace_dir: Agent 工作区目录

    Returns:
        配置哈希（SHA-256 前 16 位）
    """
    data = {
        "image": docker.image,
        "workdir": docker.workdir,
        "read_only_root": docker.read_only_root,
        "tmpfs": sorted(docker.tmpfs),
        "network": docker.network,
        "user": docker.user,
        "cap_drop": sorted(docker.cap_drop),
        "cap_add": sorted(docker.cap_add),
        "env": docker.env or {},
        "pids_limit": docker.pids_limit,
        "memory": docker.memory,
        "memory_swap": docker.memory_swap,
        "cpus": docker.cpus,
        "dns": sorted(docker.dns or []),
        "binds": sorted(docker.binds or []),
        "workspace_access": workspace_access,
        "workspace_dir": workspace_dir,
        "agent_workspace_dir": agent_workspace_dir,
    }
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]
