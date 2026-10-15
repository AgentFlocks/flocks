"""Uninstall REA from the plugin square, one job per service process.

Only REA's own resources go: the MCP service ``rea`` (disconnected and removed
from Flocks' configuration), the reverse-engineer-anything skill, the npm
prefix of earlier builds, the validation samples and the install record.
Shared dependencies (JDK, ``/opt/ghidra_*``, the npx cache ``~/.npm/_npx``),
conversations, uploaded materials, reports and the first-install records stay.

Related running work (an install job, conversations that are using REA) is
only stopped after the user confirmed it; without that the uninstall does not
start. The job ends with what was removed, what was kept and any residue.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from flocks.rea import runtime
from flocks.rea.components import npx_cache_dir
from flocks.utils.log import Log


log = Log.create(service="rea.uninstaller")

_STOP_WAIT_SECONDS = 20.0
_POLL_SECONDS = 0.25


class ReaUninstallJob(BaseModel):
    id: str
    status: Literal["running", "succeeded", "failed"]
    removed: list[str] = []
    kept: list[str] = []
    residues: list[str] = []
    stoppedTasks: list[dict[str, Any]] = []
    errorDetail: Optional[str] = None
    startedAt: int
    finishedAt: Optional[int] = None
    startedBy: Optional[str] = None
    # The plugin state before the uninstall; the badge keeps it meanwhile.
    previousState: Optional[str] = None


class TasksRunning(Exception):
    """Related work is running and the user has not agreed to stop it."""

    def __init__(self, tasks: list[dict[str, Any]]):
        super().__init__("related tasks are running")
        self.tasks = tasks


def _now_ms() -> int:
    return int(time.time() * 1000)


def kept_resources() -> list[str]:
    """What an uninstall keeps, with this host's cache paths (the guide's /opt/ghidra_* and /root/.npm/_npx on a root host)."""

    return [
        f"JDK {runtime.JDK_MAJOR}",
        str(Path(runtime.ghidra_target_dir()).parent / "ghidra_*"),
        str(npx_cache_dir()),
        "历史会话、上传的材料和已生成的报告",
    ]


def removable_resources() -> list[str]:
    """What an uninstall removes, for the confirmation."""

    return [
        f"MCP 服务 {runtime.MCP_SERVER_NAME} 的配置和运行中的服务",
        f"技能 {runtime.REA_SKILL_NAME}",
        "REA 安装记录和验证样例",
    ]


def has_resources() -> bool:
    """Something of REA is in place that an uninstall would remove (configuration and files only)."""

    return (
        runtime.current_mcp_config() is not None
        or runtime.mcp_config_file() is not None
        or runtime.installed_skill_dir().exists()
        or runtime.legacy_install_root().exists()
        or runtime.install_record_path().exists()
    )


def _uses_rea(parts: list[Any], *, service_tools_only: bool = False) -> bool:
    """A step called REA: its MCP tools (rea_<tool>), or also the rea_analysis plugin tool."""

    for part in parts:
        if getattr(part, "type", None) != "tool":
            continue
        name = str(getattr(part, "tool", "") or "")
        if name == "rea_analysis":
            if not service_tools_only:
                return True
        elif name.startswith(f"{runtime.MCP_SERVER_NAME}_"):
            return True
    return False


def _typed_by_user(message: Any) -> bool:
    for part in getattr(message, "parts", None) or []:
        kind = getattr(part, "type", None)
        if kind == "file" or (kind == "text" and not getattr(part, "synthetic", False)):
            return True
    return False


def _current_turn(messages: list[Any]) -> tuple[int, str]:
    """Where the running reply starts (after the user's last message) and what names that reply.

    The name is the message's id and the id of the reply's first step: editing the message or
    regenerating the reply keeps the message but starts over with new steps, so it is another
    reply. Empty when a step or id is missing: such a reply cannot be told from the next one.
    """

    start, opener = 0, None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if getattr(message.info, "role", None) == "user" and _typed_by_user(message):
            start, opener = index + 1, getattr(message.info, "id", None)
            break
    first_step = next((m for m in messages[start:] if getattr(m.info, "role", None) == "assistant"), None)
    step_id = getattr(first_step.info, "id", None) if first_step is not None else None
    if not step_id or (start and not opener):
        return start, ""
    return start, f"{opener or ''}:{step_id}"


def _current_turn_uses_rea(messages: list[Any], *, service_tools_only: bool = False) -> bool:
    """The running reply (every step since the user's last message) called REA."""

    start, _turn = _current_turn(messages)
    return any(
        getattr(message.info, "role", None) == "assistant"
        and _uses_rea(list(message.parts or []), service_tools_only=service_tools_only)
        for message in messages[start:]
    )


async def related_tasks() -> list[dict[str, Any]]:
    """Running work that uses REA: the install job and busy conversations whose current reply called REA."""

    from flocks.rea.installer import ReaInstaller
    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message
    from flocks.session.session import Session

    tasks: list[dict[str, Any]] = []
    job = ReaInstaller.current_job()
    if job is not None and job.status == "running":
        tasks.append({"kind": "install", "id": job.id, "title": "REA 安装与验证", "sessionId": job.sessionId})
    for session_id, info in SessionStatus.list().items():
        if getattr(info, "type", "idle") == "idle":
            continue
        try:
            messages = await Message.list_with_parts(session_id)
        except Exception as exc:
            log.debug("rea.uninstall.inspect_failed", {"session_id": session_id, "error": str(exc)})
            continue
        if not _current_turn_uses_rea(list(messages or [])):
            continue
        session = await Session.get_by_id_unfiltered(session_id)
        tasks.append({"kind": "session", "id": session_id, "title": getattr(session, "title", None) or session_id})
    return tasks


async def sessions_using_rea(*, exclude: Optional[str] = None) -> list[dict[str, Any]]:
    """Busy conversations whose running reply called REA's MCP tools (``exclude``: the caller's own).

    Validating REA again opens samples in the service they share, and a new registration reconnects
    it: either would interrupt their analysis. A busy conversation that cannot be read counts too
    (``unknown``): not knowing is not the same as not using REA. ``turn`` names the running reply
    (see :func:`_current_turn`), so a confirmation covers that reply only; a reply that cannot be
    named is ``unknown`` as well. No titles: they would end up in the asking conversation, which
    other people may read.
    """

    from flocks.session.core.status import SessionStatus
    from flocks.session.message import Message

    tasks: list[dict[str, Any]] = []
    for session_id, info in SessionStatus.list().items():
        if session_id == exclude or getattr(info, "type", "idle") == "idle":
            continue
        try:
            messages = list(await Message.list_with_parts(session_id) or [])
        except Exception as exc:
            log.warning("rea.usage.inspect_failed", {"session_id": session_id, "error": str(exc)})
            tasks.append({"kind": "session", "id": session_id, "turn": "", "unknown": True})
            continue
        if not _current_turn_uses_rea(messages, service_tools_only=True):
            continue
        _start, turn = _current_turn(messages)
        tasks.append({"kind": "session", "id": session_id, "turn": turn, "unknown": not turn})
    return tasks


async def stop_tasks(tasks: list[dict[str, Any]]) -> list[str]:
    """Stop the given work after the user agreed; returns what is still running afterwards."""

    return await _stop(tasks)


async def _stop(tasks: list[dict[str, Any]]) -> list[str]:
    """Stop the given work; returns what is still running afterwards."""

    from flocks.rea.installer import ReaInstaller
    from flocks.server.routes.session import _abort_session_processing
    from flocks.session.core.status import SessionStatus

    for task in tasks:
        if task["kind"] == "install":
            ReaInstaller.cancel()
        else:
            await _abort_session_processing(task["id"])
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        still = []
        for task in tasks:
            if task["kind"] == "install":
                if ReaInstaller.is_running():
                    still.append(task["id"])
            elif getattr(SessionStatus.get(task["id"]), "type", "idle") != "idle":
                still.append(task["id"])
        if not still:
            return []
        await asyncio.sleep(_POLL_SECONDS)
    return still


def _failure_text(exc: Exception) -> Optional[str]:
    """``Permission denied: ~/.flocks/plugins/skills/…`` instead of a Python repr."""

    if isinstance(exc, OSError) and exc.filename:
        return runtime.public_detail(f"{exc.strerror or type(exc).__name__}: {exc.filename}", 300)
    return runtime.public_detail(str(exc), 300)


def _drop_service_config() -> None:
    """Remove both registrations of the service; what stays is reported by :func:`residues`."""

    from flocks.config.config_writer import ConfigWriter
    from flocks.tool.tool_loader import delete_mcp_config

    if runtime.current_mcp_config() is not None:
        ConfigWriter.remove_mcp_server(runtime.MCP_SERVER_NAME)
    if runtime.mcp_config_file() is not None:
        delete_mcp_config(runtime.MCP_SERVER_NAME)


def _kept_paths() -> list[Path]:
    """The shared dependencies an uninstall keeps, wherever this host has them (read before the
    service configuration with its GHIDRA_INSTALL_DIR / JAVA_HOME is removed)."""

    config = runtime.current_mcp_config() or {}
    environment = config.get("environment") or config.get("env") or {}
    paths = [
        *runtime.ghidra_dir_candidates(), *runtime.java_home_candidates(), str(npx_cache_dir()),
        environment.get("GHIDRA_INSTALL_DIR"), environment.get("JAVA_HOME"),
        # The npm cache the service itself runs npx with.
        environment.get("npm_config_cache"), environment.get("NPM_CONFIG_CACHE"),
    ]
    if isinstance(environment.get("HOME"), str) and environment["HOME"]:
        paths.append(str(Path(environment["HOME"]) / ".npm"))
    return [Path(path) for path in paths if isinstance(path, str) and path]


def _holds_kept(directory: Path, kept: list[Path]) -> Optional[Path]:
    root = directory.resolve()
    for path in kept:
        resolved = path.resolve()
        if resolved == root or resolved.is_relative_to(root):
            return path
    return None


def _remove_files(kept: list[Path]) -> list[str]:
    """Remove REA's own files. A folder that holds a kept dependency stays; the reasons are returned."""

    from flocks.skill.skill import Skill

    held = []
    for directory in (runtime.installed_skill_dir(), runtime.legacy_install_root(), runtime.validation_root()):
        if not directory.exists():
            continue
        inside = _holds_kept(directory, kept)
        if inside is not None:
            held.append(
                f"{runtime.public_detail(str(directory))} 里有要保留的 {runtime.public_detail(str(inside))}，没有删除"
            )
            continue
        shutil.rmtree(directory)
    runtime.drop_install_record()
    Skill.clear_cache()
    return held


async def residues() -> list[str]:
    """What of REA is still in place."""

    from flocks.mcp import MCP

    left = []
    if runtime.current_mcp_config() is not None:
        left.append("Flocks 配置里的 MCP 服务 rea")
    if runtime.mcp_config_file() is not None:
        left.append("插件目录里的 MCP 服务 rea 配置文件")
    if runtime.MCP_SERVER_NAME in (await MCP.status()):
        left.append("运行中的 MCP 服务 rea")
    if runtime.installed_skill_dir().exists():
        left.append(f"技能 {runtime.REA_SKILL_NAME}")
    if runtime.legacy_install_root().exists():
        left.append(f"早期版本的 REA 安装目录 {runtime.legacy_install_root()}")
    if runtime.validation_root().exists():
        left.append("REA 验证样例")
    if runtime.install_record_path().exists():
        left.append("REA 安装记录")
    return left


class ReaUninstaller:
    """Process-wide owner of the single REA uninstall job."""

    _job: Optional[ReaUninstallJob] = None
    _task: Optional[asyncio.Task] = None
    # Set while start() looks for related tasks, so no install starts meanwhile.
    _starting = False

    @classmethod
    def current_job(cls) -> Optional[ReaUninstallJob]:
        job = cls._job
        if job is not None and job.status == "running" and (cls._task is None or cls._task.done()):
            job = job.model_copy(update={
                "status": "failed", "errorDetail": job.errorDetail or "interrupted",
                "finishedAt": job.finishedAt or _now_ms(),
            })
            cls._job = job
        return job

    @classmethod
    def is_running(cls) -> bool:
        if cls._starting:
            return True
        job = cls.current_job()
        return job is not None and job.status == "running"

    @classmethod
    async def start(
        cls,
        *,
        stop_tasks: bool,
        user_id: Optional[str] = None,
        previous_state: Optional[str] = None,
    ) -> ReaUninstallJob:
        """Start the uninstall. Raises :class:`TasksRunning` when work must be stopped first."""

        while cls._starting:
            # Another request is starting it; answer with its job.
            await asyncio.sleep(_POLL_SECONDS)
        current = cls.current_job()
        if current is not None and current.status == "running":
            return current
        cls._starting = True
        try:
            tasks = await related_tasks()
            if tasks and not stop_tasks:
                raise TasksRunning(tasks)
            job = ReaUninstallJob(
                id=f"reauninst_{uuid.uuid4().hex[:12]}", status="running", startedAt=_now_ms(), startedBy=user_id,
                kept=kept_resources(), previousState=previous_state,
            )
            cls._job = job
            cls._task = asyncio.create_task(cls._run(job.id, tasks))
        finally:
            cls._starting = False
        await cls._publish()
        return job

    @classmethod
    async def wait(cls) -> Optional[ReaUninstallJob]:
        task = cls._task
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # the waiter itself is being cancelled
                # The job was stopped (e.g. by an uninstall); its state says so.
        return cls.current_job()

    @classmethod
    def reset_for_testing(cls) -> None:
        if cls._task is not None and not cls._task.done():
            cls._task.cancel()
        cls._job = None
        cls._task = None
        cls._starting = False

    @classmethod
    async def _set(cls, job_id: str, **changes) -> None:
        if cls._job is None or cls._job.id != job_id:
            return
        cls._job = cls._job.model_copy(update=changes)
        await cls._publish()

    @classmethod
    async def _run(cls, job_id: str, tasks: list[dict[str, Any]]) -> None:
        from flocks.mcp import MCP

        removed: list[str] = []
        try:
            kept = _kept_paths()
            if tasks:
                still = await _stop(tasks)
                await cls._set(job_id, stoppedTasks=[t for t in tasks if t["id"] not in still])
                if still:
                    await cls._set(
                        job_id, status="failed", residues=[f"仍在运行：{task}" for task in still],
                        errorDetail="相关任务没有停下来，卸载没有执行", finishedAt=_now_ms(),
                    )
                    return
            try:
                await MCP.remove(runtime.MCP_SERVER_NAME)
            except Exception as exc:
                log.warning("rea.uninstall.disconnect_failed", {"error": str(exc)})
            if runtime.current_mcp_config() is not None or runtime.mcp_config_file() is not None:
                await runtime.run_in_thread(_drop_service_config)
                if runtime.current_mcp_config() is None and runtime.mcp_config_file() is None:
                    removed.append(f"MCP 服务 {runtime.MCP_SERVER_NAME} 的配置")
            targets = [
                (runtime.installed_skill_dir(), f"技能 {runtime.REA_SKILL_NAME}"),
                (runtime.legacy_install_root(), "早期版本的 REA 安装目录"),
                (runtime.validation_root(), "REA 验证样例"),
                (runtime.install_record_path(), "REA 安装记录"),
            ]
            present = [(path, label) for path, label in targets if path.exists()]
            held = await runtime.run_in_thread(_remove_files, kept)
            removed += [label for path, label in present if not path.exists()]
            left = await residues()
            if not left:
                from flocks.rea import service

                await runtime.run_in_thread(service.forget_install_sessions)
            await cls._set(
                job_id, status="failed" if left else "succeeded", removed=removed, residues=left,
                errorDetail=("；".join(held) if held else "部分资源没有移除") if left else None,
                finishedAt=_now_ms(),
            )
            log.info("rea.uninstall.finished", {"removed": removed, "residues": left})
        except asyncio.CancelledError:
            await cls._set(job_id, status="failed", removed=removed, errorDetail="interrupted", finishedAt=_now_ms())
            raise
        except Exception as exc:
            log.error("rea.uninstall.crashed", {"error": str(exc)})
            left = await residues()
            await cls._set(
                job_id, status="failed", removed=removed, residues=left, errorDetail=_failure_text(exc),
                finishedAt=_now_ms(),
            )

    @classmethod
    async def _publish(cls) -> None:
        job = cls._job
        if job is None:
            return
        try:
            from flocks.server.routes.event import publish_event

            # Only that something changed: the details come from the status API, which checks the role.
            await publish_event("rea.uninstall.updated", {"job": {"id": job.id, "status": job.status}})
        except Exception as exc:
            log.debug("rea.uninstall.publish_failed", {"error": str(exc)})
