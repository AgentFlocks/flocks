"""Background install of an REA tier, one job at a time on this host.

The job survives the request and conversation that started it and holds the
host's REA operation lock (see ``operation``), so an install or uninstall in
another Flocks process waits for it; every client reads it through
``/api/rea/status``. Stages and the progress each starts at:

- ``check`` (0)        Node.js range, npx, writable Flocks data directory
- ``components`` (10)  skill and the pinned CLI; the full tier also JDK 21 and
                       Ghidra 12.1.4, all in parallel and each reused if present
- ``register`` (55)    Flocks' MCP service ``rea`` (``npx -y rea-agents@6.4.0 --mcp``),
                       written only when missing or different, then connected
- ``validate`` (65)    ``rea mcp doctor``, then the tier's samples through the REA tools
- ``done`` (100)       the install record with the evidence

Nothing is rolled back: installed parts stay in place for 「排查并修复」 (a
new install reuses them) or 「卸载」.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel

from flocks.rea import components, operation, runtime
from flocks.session.ownership import outside_execution
from flocks.task.lease import outside_task_execution
from flocks.utils.log import Log


log = Log.create(service="rea.installer")

InstallStage = Literal["check", "components", "register", "validate", "done"]
STAGE_PROGRESS: dict[str, int] = {"check": 0, "components": 10, "register": 55, "validate": 65, "done": 100}
_ERROR_DETAIL_MAX_CHARS = 400
_SHARED_POLL_SECONDS = 1.0


class ReaInstallJob(BaseModel):
    id: str
    status: Literal["running", "succeeded", "failed"]
    stage: InstallStage
    progress: int
    tier: str = "light"
    errorCode: Optional[str] = None
    errorDetail: Optional[str] = None
    # Things only the user can do (root, platform) that stopped the install.
    blockers: list[dict[str, Any]] = []
    notes: list[str] = []
    components: dict[str, dict[str, Any]] = {}
    registration: Optional[str] = None
    service: Optional[dict[str, Any]] = None
    validation: dict[str, dict[str, Any]] = {}
    startedAt: int
    finishedAt: Optional[int] = None
    startedBy: Optional[str] = None
    sessionId: Optional[str] = None


class ReaInstallError(Exception):
    """A failed install stage with a stable code for the WebUI to localize."""

    def __init__(self, code: str, detail: Optional[str] = None, blockers: Optional[list[dict[str, Any]]] = None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
        self.blockers = blockers or []


def _now_ms() -> int:
    return int(time.time() * 1000)


def _short(text: Optional[str]) -> Optional[str]:
    return runtime.public_detail(text, _ERROR_DETAIL_MAX_CHARS)


async def _stage_check(tier: str) -> None:
    checks = await runtime.check_environment(refresh=True)
    failed = next((check for check in checks if not check.ok), None)
    if failed is not None:
        detail = f"{failed.key}:{failed.code}" + (f":{failed.detail}" if failed.detail else "")
        raise ReaInstallError("environment", detail)


def _write_service_config(config: dict) -> None:
    from flocks.config.config_writer import ConfigWriter
    from flocks.tool.tool_loader import save_mcp_config

    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, config)
    save_mcp_config(runtime.MCP_SERVER_NAME, config)


async def _service_status() -> tuple[Optional[str], int, Optional[str]]:
    """(status, tool count, error) of the ``rea`` server in Flocks' MCP manager."""

    from flocks.mcp import MCP

    info = (await MCP.status()).get(runtime.MCP_SERVER_NAME)
    if info is None:
        return None, 0, None
    raw = getattr(info, "status", None)
    status = getattr(raw, "value", raw)
    return (str(status) if status is not None else None), int(getattr(info, "tools_count", 0) or 0), getattr(info, "error", None)


async def _connect_service(config: dict) -> tuple[bool, Optional[str], int]:
    """Start the service through Flocks' MCP manager: (connected, error, tool count)."""

    from flocks.mcp import MCP

    # Connecting again replaces the client; the old one would keep its process.
    await MCP.disconnect(runtime.MCP_SERVER_NAME)
    connected = await MCP.connect(runtime.MCP_SERVER_NAME, config)
    status, tools, error = await _service_status()
    return bool(connected) and status in (None, "connected"), error, tools


async def _stage_register(config: dict) -> str:
    """Register REA once: an identical registration is reused, not written again."""

    if runtime.mcp_config_matches(runtime.current_mcp_config(), config):
        status, tools, _error = await _service_status()
        if status == "connected" and tools > 0:
            return "reused"
        connected, error, tools = await _connect_service(config)
        if not connected or tools <= 0:
            raise ReaInstallError("service_failed", error or "the REA tool service listed no tools")
        return "reused"
    try:
        await runtime.run_in_thread(_write_service_config, config)
    except (OSError, ValueError, RuntimeError) as exc:
        # Unwritable files, a malformed flocks.json, or a runtime config override.
        raise ReaInstallError("service_failed", f"cannot write the MCP configuration: {exc}") from exc
    connected, error, tools = await _connect_service(config)
    if not connected or tools <= 0:
        raise ReaInstallError("service_failed", error or "the REA tool service listed no tools")
    return "written"


def _build_record(
    job: ReaInstallJob,
    config: dict,
    doctor: runtime.DoctorReport,
    validation: dict[str, Any],
) -> dict[str, Any]:
    previous = runtime.read_install_record() or {}
    now = _now_ms()
    passed = bool(validation.get("ok"))
    first_passed = previous.get("firstPassedAt") if isinstance(previous.get("firstPassedAt"), int) else None
    previous_mcp = previous.get("mcp") if isinstance(previous.get("mcp"), dict) else {}
    return {
        "package": runtime.REA_PACKAGE,
        "version": runtime.REA_VERSION,
        "tier": job.tier,
        "installedAt": now,
        "skill": runtime.REA_SKILL_NAME,
        "skillPath": str(runtime.installed_skill_dir()),
        "components": job.components,
        "mcp": {
            "server": runtime.MCP_SERVER_NAME,
            "command": config.get("command"),
            "environment": config.get("environment"),
            "registeredAt": previous_mcp.get("registeredAt") if job.registration == "reused" else now,
            "registration": job.registration,
        },
        "verification": {
            "ok": passed,
            "tier": job.tier,
            "verifiedAt": now,
            "service": doctor.to_dict(),
            "capabilities": validation.get("capabilities") or {},
            "samples": validation.get("root"),
        },
        "firstPassedAt": first_passed or (now if passed else None),
    }


async def refresh_jobs() -> None:
    """Pick up the install and uninstall other Flocks processes on this host run or ran."""

    from flocks.rea.uninstaller import ReaUninstaller

    await ReaInstaller.refresh_shared()
    await ReaUninstaller.refresh_shared()


class ReaInstaller:
    """The REA install job of this host: run here, or read from the process running it."""

    _job: Optional[ReaInstallJob] = None
    _task: Optional[asyncio.Task] = None
    _lock: Optional[operation.OperationLock] = None
    _publish_lock: Optional[asyncio.Lock] = None
    # A newer job another process published (see refresh_shared).
    _shared: Optional[ReaInstallJob] = None

    @classmethod
    def current_job(cls) -> Optional[ReaInstallJob]:
        job = cls._job
        if job is not None and job.status == "running" and (cls._task is None or cls._task.done()):
            # The worker died without settling the job (e.g. its loop closed).
            job = job.model_copy(update={
                "status": "failed", "errorCode": "interrupted", "finishedAt": job.finishedAt or _now_ms(),
            })
            cls._job = job
        shared = cls._shared
        if shared is not None and (job is None or shared.startedAt > job.startedAt):
            return shared
        return job

    @classmethod
    async def refresh_shared(cls) -> None:
        """Read the install another process on this host published; one no longer holding the lock was interrupted."""

        found = await operation.read("install")
        if found is None:
            cls._shared = None
            return
        data, alive = found
        try:
            job = ReaInstallJob.model_validate(data)
        except ValueError:
            cls._shared = None
            return
        if cls._job is not None and cls._job.id == job.id:
            cls._shared = None  # this process runs it
            return
        if job.status == "running" and not alive:
            job = job.model_copy(update={
                "status": "failed", "errorCode": "interrupted", "finishedAt": job.finishedAt or _now_ms(),
            })
        cls._shared = job

    @classmethod
    def is_running(cls) -> bool:
        job = cls.current_job()
        return job is not None and job.status == "running"

    @classmethod
    async def start(
        cls,
        tier: str = "light",
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ReaInstallJob:
        """Start an install, or return the job that is already running (here or in another process).

        Raises :class:`ReaInstallError` (``uninstalling``) while an uninstall runs,
        ``busy`` while another job on this host holds the operation lock.
        """

        from flocks.rea.uninstaller import ReaUninstaller

        await refresh_jobs()
        current = cls.current_job()
        if current is not None and current.status == "running":
            return current
        if ReaUninstaller.is_running():
            raise ReaInstallError("uninstalling", "REA is being uninstalled")
        job_id = f"reainst_{uuid.uuid4().hex[:12]}"
        lock = operation.acquire(job_id)
        if lock is None:
            # Another process on this host started a job meanwhile.
            await refresh_jobs()
            current = cls.current_job()
            if current is not None and current.status == "running":
                return current
            if ReaUninstaller.is_running():
                raise ReaInstallError("uninstalling", "REA is being uninstalled")
            raise ReaInstallError("busy", "another Flocks process on this host is changing REA")
        job = ReaInstallJob(
            id=job_id,
            status="running",
            stage="check",
            progress=STAGE_PROGRESS["check"],
            tier=tier if tier in runtime.TIERS else "light",
            startedAt=_now_ms(),
            startedBy=user_id,
            sessionId=session_id,
        )
        cls._job = job
        cls._shared = None
        cls._lock = lock
        cls._task = None
        try:
            await cls._publish()
            # Outside the conversation's (or task's) execution: the job outlives it, so
            # stopping that conversation must not make the job's validation lose its lease.
            with outside_execution(), outside_task_execution():
                cls._task = asyncio.create_task(cls._held(job.id, lock))
            # A task cancelled before it ever ran never reaches _held's finally.
            cls._task.add_done_callback(lambda _task: lock.release())
        except BaseException:
            if cls._task is None:
                # Stopped before the job owned the lock: let go of it here (the job reads as interrupted).
                lock.release()
            raise
        return job

    @classmethod
    async def _held(cls, job_id: str, lock: operation.OperationLock) -> None:
        """Run the job holding the operation lock; it is let go only once the job and its threads ended."""

        try:
            await cls._run(job_id)
        finally:
            lock.release()

    @classmethod
    async def wait(cls) -> Optional[ReaInstallJob]:
        task = cls._task
        if task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # the waiter itself is being cancelled
                # The job was stopped (e.g. by an uninstall); its state says so.
        else:
            # A job another process runs: follow what it publishes.
            while (job := cls.current_job()) is not None and job.status == "running" and job is cls._shared:
                await asyncio.sleep(_SHARED_POLL_SECONDS)
                await cls.refresh_shared()
        return cls.current_job()

    @classmethod
    def cancel(cls) -> bool:
        task = cls._task
        if task is None or task.done():
            return False
        task.cancel()
        return True

    @classmethod
    def reset_for_testing(cls) -> None:
        if cls._task is not None and not cls._task.done():
            cls._task.cancel()
        if cls._lock is not None:
            cls._lock.release()
        cls._job = None
        cls._task = None
        cls._lock = None
        cls._publish_lock = None
        cls._shared = None

    @classmethod
    async def _set(cls, job_id: str, **changes) -> None:
        if cls._job is None or cls._job.id != job_id:
            return
        cls._job = cls._job.model_copy(update=changes)
        await cls._publish()

    @classmethod
    async def _advance(cls, job_id: str, stage: InstallStage) -> None:
        await cls._set(job_id, stage=stage, progress=STAGE_PROGRESS[stage])

    @classmethod
    async def _run(cls, job_id: str) -> None:
        job = cls._job
        assert job is not None
        tier = job.tier
        try:
            await _stage_check(tier)
            await cls._advance(job_id, "components")

            async def component_done(result: components.ComponentResult) -> None:
                current = cls._job.components if cls._job else {}
                await cls._set(job_id, components={**current, result.key: result.to_dict()})

            results = await components.ensure_components(tier, on_done=component_done)
            failed = [result for result in results.values() if not result.ok]
            if failed:
                blockers = [result.blocker for result in failed if result.blocker]
                broken = [result for result in failed if result.status != "blocked"]
                # Blockers carry their own action; the detail is for what actually failed.
                details = "; ".join(f"{result.key}: {result.detail}" for result in broken) or None
                code = "component_failed" if broken else "blocked"
                raise ReaInstallError(code, details, blockers)

            await cls._advance(job_id, "register")
            java_home = results["jdk"].detail if tier == "full" else None
            ghidra_dir = results["ghidra"].detail if tier == "full" else None
            config = runtime.mcp_server_config(tier, java_home=java_home, ghidra_dir=ghidra_dir)
            if config is None:
                raise ReaInstallError("environment", "npx:missing")
            registration = await _stage_register(config)
            await cls._set(job_id, registration=registration)

            await cls._advance(job_id, "validate")
            doctor = await runtime.run_doctor(config.get("environment"))
            await cls._set(job_id, service=doctor.to_dict())
            if not doctor.ok:
                failed_checks = ", ".join(doctor.failed_checks) or doctor.error or "unhealthy"
                raise ReaInstallError("validation_failed", f"rea mcp doctor: {failed_checks}")

            from flocks.rea.validation import run_validation

            async def capability_done(result) -> None:
                current = cls._job.validation if cls._job else {}
                await cls._set(job_id, validation={**current, result.key: result.to_dict()})

            validation = await run_validation(
                tier, session_id=job.sessionId or "rea-install", on_result=capability_done,
            )
            record = _build_record(cls._job or job, config, doctor, validation)
            try:
                await runtime.run_in_thread(runtime.write_install_record, record)
            except OSError as exc:
                raise ReaInstallError("install_failed", f"cannot write install record: {exc}") from exc
            if not validation.get("ok"):
                unfinished = [
                    key for key, item in (validation.get("capabilities") or {}).items()
                    if item.get("status") != "passed"
                ]
                raise ReaInstallError("validation_failed", "unverified: " + ", ".join(unfinished))

            from flocks.rea import service

            await runtime.run_in_thread(service.record_first_install, job.startedBy)
            await cls._set(
                job_id, status="succeeded", stage="done", progress=STAGE_PROGRESS["done"], finishedAt=_now_ms(),
            )
            log.info("rea.install.succeeded", {"tier": tier, "registration": registration})
        except ReaInstallError as exc:
            log.warning("rea.install.failed", {"code": exc.code, "detail": exc.detail})
            await cls._set(
                job_id, status="failed", errorCode=exc.code, errorDetail=_short(exc.detail),
                blockers=exc.blockers, finishedAt=_now_ms(),
            )
        except asyncio.CancelledError:
            await cls._set(job_id, status="failed", errorCode="interrupted", finishedAt=_now_ms())
            raise
        except Exception as exc:
            log.error("rea.install.crashed", {"error": str(exc), "error_type": type(exc).__name__})
            await cls._set(
                job_id, status="failed", errorCode="install_failed", errorDetail=_short(str(exc)),
                finishedAt=_now_ms(),
            )

    @classmethod
    async def _publish(cls) -> None:
        if cls._job is None:
            return
        if cls._publish_lock is None:
            cls._publish_lock = asyncio.Lock()
        # One at a time, each the latest state: an earlier write never lands last. Only
        # while this job holds the lock, so a job that no longer does never writes over another.
        async with cls._publish_lock:
            job = cls._job
            lock = cls._lock
            if lock is not None and lock.held and lock.job_id == job.id:
                try:
                    await operation.publish("install", job.model_dump())
                except Exception as exc:
                    log.warning("rea.install.share_failed", {"error": str(exc)})
        try:
            from flocks.server.routes.event import publish_event

            # Only that something changed: the details come from the status API, which checks the role.
            fields = ("id", "status", "stage", "progress", "tier")
            await publish_event("rea.install.updated", {"job": {key: getattr(job, key) for key in fields}})
        except Exception as exc:
            log.debug("rea.install.publish_failed", {"error": str(exc)})
