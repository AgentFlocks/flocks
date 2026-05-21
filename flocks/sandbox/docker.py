"""
Docker 容器管理 (CLI 方式)

对齐 OpenClaw sandbox/docker.ts：
- 通过 `docker` CLI 命令管理容器 (不使用 Docker SDK)
- 天然兼容 asyncio
- ensure / create / start / exec / remove
"""

import asyncio
import os
import time
from typing import Dict, List, Optional, Tuple

from .config_hash import compute_sandbox_config_hash
from .defaults import DEFAULT_SANDBOX_IMAGE, SANDBOX_AGENT_WORKSPACE_MOUNT
from .registry import find_registry_entry, read_registry, update_registry
from .shared import resolve_sandbox_scope_key, slugify_session_key
from .types import SandboxConfig, SandboxDockerConfig, WorkspaceAccess

from flocks.utils.log import Log

log = Log.create(service="sandbox.docker")

# 最近使用的容器窗口期 (5 分钟内不强制重建)
HOT_CONTAINER_WINDOW_MS = 5 * 60 * 1000


# ==================== Docker CLI 封装 ====================


async def exec_docker(
    args: List[str],
    allow_failure: bool = False,
    timeout_s: Optional[float] = None,
) -> Tuple[str, str, int]:
    """
    执行 docker CLI 命令。

    对齐 OpenClaw execDocker: 使用 child_process.spawn("docker", args)。

    Args:
        args: docker 子命令参数列表
        allow_failure: 是否允许非零退出码

    Returns:
        (stdout, stderr, exit_code)

    Raises:
        RuntimeError: 命令失败且 allow_failure=False
    """
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if timeout_s is not None and timeout_s > 0:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        else:
            stdout_bytes, stderr_bytes = await proc.communicate()
    except asyncio.TimeoutError:
        proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        timeout_msg = f"docker {' '.join(args)} timed out after {timeout_s}s"
        if not allow_failure:
            raise RuntimeError(timeout_msg)
        return stdout, stderr.strip() or timeout_msg, 124
    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    exit_code = proc.returncode or 0

    if exit_code != 0 and not allow_failure:
        raise RuntimeError(
            stderr.strip() or f"docker {' '.join(args)} failed (exit {exit_code})"
        )

    return stdout, stderr, exit_code


# ==================== 镜像管理 ====================


async def docker_image_exists(image: str) -> bool:
    """检查 Docker 镜像是否存在."""
    _, stderr, code = await exec_docker(
        ["image", "inspect", image],
        allow_failure=True,
    )
    if code == 0:
        return True
    if "No such image" in stderr:
        return False
    raise RuntimeError(f"Failed to inspect sandbox image: {stderr.strip()}")


async def ensure_docker_image(image: str) -> None:
    """确保 Docker 镜像可用."""
    exists = await docker_image_exists(image)
    if exists:
        return
    if image == DEFAULT_SANDBOX_IMAGE:
        # 默认镜像: 直接 pull 默认镜像
        log.info("sandbox.pulling_base_image", {"image": DEFAULT_SANDBOX_IMAGE})
        await exec_docker(["pull", DEFAULT_SANDBOX_IMAGE])
        return
    raise RuntimeError(
        f"Sandbox image not found: {image}. Build or pull it first."
    )


# ==================== 容器状态 ====================


async def docker_container_state(
    name: str,
) -> Dict[str, bool]:
    """
    获取容器状态。

    Returns:
        {"exists": bool, "running": bool}
    """
    stdout, _, code = await exec_docker(
        ["inspect", "-f", "{{.State.Running}}", name],
        allow_failure=True,
    )
    if code != 0:
        return {"exists": False, "running": False}
    return {"exists": True, "running": stdout.strip() == "true"}


# ==================== 容器 label 读取 ====================


async def read_container_config_hash(container_name: str) -> Optional[str]:
    """读取容器 label 中的 configHash."""
    stdout, _, code = await exec_docker(
        [
            "inspect",
            "-f",
            '{{ index .Config.Labels "flocks.configHash" }}',
            container_name,
        ],
        allow_failure=True,
    )
    if code != 0:
        return None
    raw = stdout.strip()
    if not raw or raw == "<no value>":
        return None
    return raw


# ==================== 构建容器创建参数 ====================


def build_sandbox_create_args(
    name: str,
    cfg: SandboxDockerConfig,
    scope_key: str,
    config_hash: Optional[str] = None,
) -> List[str]:
    """
    构建 `docker create` 参数。

    对齐 OpenClaw buildSandboxCreateArgs。
    """
    created_at_ms = int(time.time() * 1000)
    args = ["create", "--name", name]

    # Labels
    args.extend(["--label", "flocks.sandbox=1"])
    args.extend(["--label", f"flocks.sessionKey={scope_key}"])
    args.extend(["--label", f"flocks.createdAtMs={created_at_ms}"])
    if config_hash:
        args.extend(["--label", f"flocks.configHash={config_hash}"])

    # 安全选项
    if cfg.read_only_root:
        args.append("--read-only")

    for entry in cfg.tmpfs:
        args.extend(["--tmpfs", entry])

    if cfg.network:
        args.extend(["--network", cfg.network])

    if cfg.user:
        args.extend(["--user", cfg.user])

    for cap in cfg.cap_drop:
        args.extend(["--cap-drop", cap])

    args.extend(["--security-opt", "no-new-privileges"])

    if cfg.seccomp_profile:
        args.extend(["--security-opt", f"seccomp={cfg.seccomp_profile}"])

    if cfg.apparmor_profile:
        args.extend(["--security-opt", f"apparmor={cfg.apparmor_profile}"])

    # DNS
    for entry in cfg.dns or []:
        if entry.strip():
            args.extend(["--dns", entry])

    for entry in cfg.extra_hosts or []:
        if entry.strip():
            args.extend(["--add-host", entry])

    # 资源限制
    if isinstance(cfg.pids_limit, int) and cfg.pids_limit > 0:
        args.extend(["--pids-limit", str(cfg.pids_limit)])

    if cfg.memory:
        args.extend(["--memory", str(cfg.memory)])

    if cfg.memory_swap:
        args.extend(["--memory-swap", str(cfg.memory_swap)])

    if isinstance(cfg.cpus, (int, float)) and cfg.cpus > 0:
        args.extend(["--cpus", str(cfg.cpus)])

    # ulimits
    for name_key, value in (cfg.ulimits or {}).items():
        args.extend(["--ulimit", f"{name_key}={value}"])

    # 额外的 bind mount
    if cfg.binds:
        for bind in cfg.binds:
            args.extend(["-v", bind])

    return args


# ==================== 创建容器 ====================


async def create_sandbox_container(
    name: str,
    cfg: SandboxDockerConfig,
    workspace_dir: str,
    workspace_access: WorkspaceAccess,
    agent_workspace_dir: str,
    scope_key: str,
    config_hash: Optional[str] = None,
) -> None:
    """
    创建沙箱容器。

    对齐 OpenClaw createSandboxContainer。
    """
    await ensure_docker_image(cfg.image)

    args = build_sandbox_create_args(
        name=name,
        cfg=cfg,
        scope_key=scope_key,
        config_hash=config_hash,
    )

    # 工作目录
    args.extend(["--workdir", cfg.workdir])

    # workspace 挂载
    main_mount_suffix = ""
    if workspace_access == "ro" and workspace_dir == agent_workspace_dir:
        main_mount_suffix = ":ro"
    args.extend(["-v", f"{workspace_dir}:{cfg.workdir}{main_mount_suffix}"])

    # agent workspace 额外挂载 (当 workspaceAccess != none 且 workspace 不同)
    if workspace_access != "none" and workspace_dir != agent_workspace_dir:
        agent_mount_suffix = ":ro" if workspace_access == "ro" else ""
        args.extend([
            "-v",
            f"{agent_workspace_dir}:{SANDBOX_AGENT_WORKSPACE_MOUNT}{agent_mount_suffix}",
        ])

    # 镜像 + 保持容器运行
    args.extend([cfg.image, "sleep", "infinity"])

    log.info("sandbox.creating_container", {"name": name, "image": cfg.image})
    await exec_docker(args)
    await exec_docker(["start", name])

    # 执行 setup command
    if cfg.setup_command and cfg.setup_command.strip():
        log.info("sandbox.running_setup", {"name": name})
        await exec_docker(["exec", "-i", name, "sh", "-lc", cfg.setup_command])


# ==================== 确保容器就绪 ====================


async def ensure_sandbox_container(
    session_key: str,
    workspace_dir: str,
    agent_workspace_dir: str,
    cfg: SandboxConfig,
) -> str:
    """
    确保沙箱容器已就绪。

    对齐 OpenClaw ensureSandboxContainer：
    1. 计算 scope key 和容器名
    2. 计算配置哈希
    3. 检查容器是否存在
    4. 哈希不匹配时重建（除非容器最近在使用）
    5. 更新注册表

    Returns:
        容器名称
    """
    scope_key = resolve_sandbox_scope_key(cfg.scope, session_key)
    slug = "shared" if cfg.scope == "shared" else slugify_session_key(scope_key)
    name = f"{cfg.docker.container_prefix}{slug}"
    container_name = name[:63]  # Docker 容器名最大 63 字符

    expected_hash = compute_sandbox_config_hash(
        docker=cfg.docker,
        workspace_access=cfg.workspace_access,
        workspace_dir=workspace_dir,
        agent_workspace_dir=agent_workspace_dir,
    )

    now = time.time() * 1000
    state = await docker_container_state(container_name)
    has_container = state["exists"]
    running = state["running"]
    hash_mismatch = False

    if has_container:
        # 检查配置哈希
        current_hash = await read_container_config_hash(container_name)
        if not current_hash:
            entry = await find_registry_entry(container_name)
            if entry:
                current_hash = entry.config_hash

        hash_mismatch = not current_hash or current_hash != expected_hash

        if hash_mismatch:
            # 检查是否最近使用
            entry = await find_registry_entry(container_name)
            last_used = entry.last_used_at_ms if entry else 0
            is_hot = running and (
                not isinstance(last_used, (int, float))
                or now - last_used < HOT_CONTAINER_WINDOW_MS
            )
            if is_hot:
                log.info(
                    "sandbox.config_changed_hot",
                    {"container": container_name},
                )
            else:
                log.info(
                    "sandbox.recreating_container",
                    {"container": container_name, "reason": "config_hash_mismatch"},
                )
                await exec_docker(
                    ["rm", "-f", container_name],
                    allow_failure=True,
                )
                has_container = False
                running = False

    if not has_container:
        await create_sandbox_container(
            name=container_name,
            cfg=cfg.docker,
            workspace_dir=workspace_dir,
            workspace_access=cfg.workspace_access,
            agent_workspace_dir=agent_workspace_dir,
            scope_key=scope_key,
            config_hash=expected_hash,
        )
    elif not running:
        await exec_docker(["start", container_name])

    # 更新注册表
    await update_registry(
        container_name=container_name,
        session_key=scope_key,
        created_at_ms=now,
        last_used_at_ms=now,
        image=cfg.docker.image,
        config_hash=(
            None if (hash_mismatch and running) else expected_hash
        ),
    )

    return container_name


# ==================== 容器内执行命令 ====================


def build_docker_exec_args(
    container_name: str,
    command: str,
    workdir: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    tty: bool = False,
) -> List[str]:
    """
    构建 `docker exec` 参数。

    对齐 OpenClaw buildDockerExecArgs (bash-tools.shared.ts)。
    使用 login shell 并处理 PATH 注入。
    """
    args = ["exec", "-i"]
    if tty:
        args.append("-t")
    if workdir:
        args.extend(["-w", workdir])

    env = env or {}
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])

    # PATH 特殊处理: 避免 login shell 重置 PATH
    has_custom_path = "PATH" in env and env["PATH"]
    if has_custom_path:
        args.extend(["-e", f"FLOCKS_PREPEND_PATH={env['PATH']}"])

    path_export = ""
    if has_custom_path:
        path_export = (
            'export PATH="${FLOCKS_PREPEND_PATH}:$PATH"; '
            "unset FLOCKS_PREPEND_PATH; "
        )

    args.extend([container_name, "sh", "-lc", f"{path_export}{command}"])
    return args


def build_sandbox_env(
    default_path: str,
    params_env: Optional[Dict[str, str]] = None,
    sandbox_env: Optional[Dict[str, str]] = None,
    container_workdir: str = "/workspace",
) -> Dict[str, str]:
    """
    构建沙箱内环境变量。

    对齐 OpenClaw buildSandboxEnv (bash-tools.shared.ts)。
    """
    env: Dict[str, str] = {
        "PATH": default_path,
        "HOME": container_workdir,
    }
    for key, value in (sandbox_env or {}).items():
        env[key] = value
    for key, value in (params_env or {}).items():
        env[key] = value
    return env


# ==================== 容器清理 ====================


async def remove_container(container_name: str) -> None:
    """强制删除容器."""
    await exec_docker(["rm", "-f", container_name], allow_failure=True)


async def stop_container(container_name: str) -> None:
    """停止容器."""
    await exec_docker(["stop", container_name], allow_failure=True)
