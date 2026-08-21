"""Trusted validation and local-Docker execution for dynamic audit probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, TypeVar, TypedDict

from flocks.utils.langfuse import span_scope

from flocks_code_security.dockerfile_policy import (
    dockerfile_base_images as _dockerfile_base_images,
)
from flocks_code_security.paths import docker_runtime_dir, ensure_private_directory
from flocks_code_security.snapshot import normalize_relative_path


MAX_SCRIPT_BYTES = 16 * 1024
MAX_EXPECTED_DIFFERENCE = 4_000
MAX_LOG_BYTES = 64 * 1024
BUILD_TIMEOUT_SECONDS = 300
MEMORY_LIMIT = "512m"
PROCESS_LIMIT = 128
BUILD_CPU_PERIOD = 100_000
BUILD_CPU_QUOTA = 100_000
BUILD_SHM_SIZE = "64m"
RUN_TMPFS_SIZE = "64m"
_REMOTE_BUILD_ENVIRONMENT = {
    "BUILDKIT_HOST",
    "BUILDX_BUILDER",
    "BUILDX_CONFIG",
    "DOCKER_DEFAULT_PLATFORM",
}
_T = TypeVar("_T")


class CommandResult(TypedDict):
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


@dataclass(frozen=True)
class BuildIdentity:
    scan_id: str
    snapshot_id: str
    tree_digest: str
    context_path: str
    dockerfile_path: str

    @property
    def digest(self) -> str:
        value = "\0".join(
            (
                self.scan_id,
                self.snapshot_id,
                self.tree_digest,
                self.context_path,
                self.dockerfile_path,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedCandidate:
    build_identity: BuildIdentity
    candidate_id: str
    probe: dict[str, Any]
    context: Path
    dockerfile: Path
    runtime: Path
    base_images: tuple[str, ...]


async def _await_uninterruptibly(
    awaitable: Awaitable[_T],
) -> tuple[_T, bool]:
    """Wait for cleanup to finish and report cancellation received while waiting."""
    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while True:
        try:
            return await asyncio.shield(task), interrupted
        except asyncio.CancelledError:
            interrupted = True
            if task.done():
                return task.result(), interrupted


def _start_observation(
    parent: Any,
    name: str,
    *,
    input: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    if parent is None:
        return None
    try:
        return span_scope(
            parent=parent,
            name=name,
            input=input,
            metadata=metadata,
        )
    except Exception:
        return None


def _end_observation(
    scope: Any,
    *,
    output: dict[str, Any],
    error: BaseException | None = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    if scope is None:
        return
    try:
        if error is None and level is None:
            scope.end(output=output)
        elif error is not None:
            scope.end(
                output={**output, "error_type": type(error).__name__},
                level="ERROR",
                status_message=type(error).__name__,
            )
        else:
            scope.end(
                output=output,
                level=level,
                status_message=status_message,
            )
    except Exception:
        # Telemetry is best-effort and must never change scan behavior.
        pass


def _observation(scope: Any, fallback: Any) -> Any:
    return fallback if scope is None else scope.observation


def _command_summary(result: CommandResult) -> dict[str, Any]:
    """Return trace-safe command facts without scripts or raw process output."""
    if result["timed_out"]:
        status = "timeout"
    elif result["exit_code"] != 0:
        status = "failed"
    else:
        status = "completed"
    return {
        "status": status,
        "exit_code": result["exit_code"],
        "duration_ms": result["duration_ms"],
        "timed_out": result["timed_out"],
        "truncated": result["truncated"],
    }


def _bounded_text(value: Any, *, field: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8 text") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{field} exceeds the {max_bytes}-byte limit")
    return value


def _bounded_characters(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8 text") from exc
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds the {maximum}-character limit")
    return value


def _probe_path(value: Any, *, allow_root: bool) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("Probe paths must use canonical snapshot-relative syntax")
    return normalize_relative_path(value, allow_root=allow_root)


def _inside(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(f"{directory}/")


def _reject_symlink_components(root: Path, relative_path: str) -> None:
    current = root
    if relative_path == ".":
        return
    for part in Path(relative_path).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Probe path contains a symbolic link")


def validate_probe(
    probe: dict[str, Any],
    *,
    candidate_id: str,
    snapshot_root: Path,
    snapshot_files: set[str],
) -> dict[str, Any]:
    """Return a canonical probe after validating its complete persisted contract."""
    validated, _base_images = _validate_probe(
        probe,
        candidate_id=candidate_id,
        snapshot_root=snapshot_root,
        snapshot_files=snapshot_files,
    )
    return validated


def _validate_probe(
    probe: dict[str, Any],
    *,
    candidate_id: str,
    snapshot_root: Path,
    snapshot_files: set[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not isinstance(probe, dict):
        raise ValueError("probe must be an object")
    if probe.get("candidate_id") != candidate_id:
        raise ValueError("Probe candidate_id is not bound to this work unit")
    status = probe.get("status")
    if status == "not_runnable":
        if set(probe) != {"candidate_id", "status", "reason"}:
            raise ValueError("not_runnable probes require only candidate_id, status, and reason")
        reason = _bounded_characters(
            probe.get("reason"),
            field="reason",
            maximum=4_000,
        ).strip()
        return (
            {
                "candidate_id": candidate_id,
                "status": "not_runnable",
                "reason": reason,
            },
            (),
        )
    if status != "runnable":
        raise ValueError("Probe status must be runnable or not_runnable")
    expected_fields = {
        "candidate_id",
        "status",
        "context_path",
        "dockerfile_path",
        "control",
        "attack",
        "expected_difference",
    }
    if set(probe) != expected_fields:
        raise ValueError("Runnable probe fields do not match the supported contract")

    context_path = _probe_path(probe.get("context_path"), allow_root=True)
    dockerfile_path = _probe_path(probe.get("dockerfile_path"), allow_root=False)
    if not _inside(dockerfile_path, context_path):
        raise ValueError("dockerfile_path must be inside context_path")
    if dockerfile_path not in snapshot_files:
        raise ValueError("Dockerfile is not a recorded snapshot file")

    if snapshot_root.is_symlink():
        raise ValueError("Snapshot root cannot be a symbolic link")
    root = snapshot_root.resolve(strict=True)
    _reject_symlink_components(root, context_path)
    _reject_symlink_components(root, dockerfile_path)
    context = (root / context_path).resolve(strict=True)
    dockerfile = (root / dockerfile_path).resolve(strict=True)
    if not context.is_relative_to(root) or not dockerfile.is_relative_to(context):
        raise ValueError("Probe path escapes the immutable snapshot")
    if not context.is_dir():
        raise ValueError("context_path must name a snapshot directory")
    if not dockerfile.is_file():
        raise ValueError("dockerfile_path must name a regular snapshot file")

    phases: dict[str, dict[str, Any]] = {}
    for phase in ("control", "attack"):
        value = probe.get(phase)
        if not isinstance(value, dict) or set(value) != {"script", "timeout_seconds"}:
            raise ValueError(f"{phase} requires only script and timeout_seconds")
        script = _bounded_text(
            value.get("script"),
            field=f"{phase}.script",
            max_bytes=MAX_SCRIPT_BYTES,
        )
        timeout = value.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 60:
            raise ValueError(f"{phase}.timeout_seconds must be an integer from 1 to 60")
        phases[phase] = {"script": script, "timeout_seconds": timeout}
    expected_difference = _bounded_characters(
        probe.get("expected_difference"),
        field="expected_difference",
        maximum=MAX_EXPECTED_DIFFERENCE,
    ).strip()

    base_images = tuple(
        _dockerfile_base_images(dockerfile.read_text(encoding="utf-8"))
    )
    return (
        {
            "candidate_id": candidate_id,
            "status": "runnable",
            "context_path": context_path,
            "dockerfile_path": dockerfile_path,
            "control": phases["control"],
            "attack": phases["attack"],
            "expected_difference": expected_difference,
        },
        base_images,
    )


class DockerDynamicRunner:
    """Execute validated probes without assigning vulnerability semantics."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._build_lock = asyncio.Lock()
        self._containers: set[str] = set()
        self._images: set[str] = set()
        self._active_scan_ids: set[str] = set()
        self._verified_contexts: set[tuple[str, str, str]] = set()
        self._verification_lock = threading.Lock()
        self._build_backend: Literal["buildkit", "legacy"] | None = None

    async def preflight(self, *, observation_parent: Any = None) -> None:
        self._build_backend = None
        scope = _start_observation(
            observation_parent,
            "code-security.dynamic.preflight",
            input={"remote_endpoints_allowed": False},
            metadata={"component": "docker"},
        )
        try:
            docker = shutil.which("docker")
            if docker is None:
                raise RuntimeError("Dynamic validation requires the Docker CLI")
            endpoint = os.environ.get("DOCKER_HOST", "")
            if endpoint and not endpoint.startswith(("unix://", "npipe://")):
                raise RuntimeError("Remote Docker endpoints are not allowed")
            context = await self._command(
                [
                    docker,
                    "context",
                    "inspect",
                    "--format",
                    "{{json .Endpoints.docker.Host}}",
                ],
                timeout_seconds=15,
            )
            if context["exit_code"] != 0:
                raise RuntimeError("Unable to inspect the active Docker context")
            try:
                active_endpoint = json.loads(context["stdout"].strip())
            except json.JSONDecodeError as exc:
                raise RuntimeError("Docker returned an invalid active endpoint") from exc
            if not isinstance(active_endpoint, str) or not active_endpoint.startswith(
                ("unix://", "npipe://")
            ):
                raise RuntimeError("Remote Docker endpoints are not allowed")
            version = await self._command([docker, "version"], timeout_seconds=15)
            if version["exit_code"] != 0:
                raise RuntimeError("The local Docker daemon is unavailable")
            build_backend = await self._select_build_backend(docker)
            if build_backend == "buildkit":
                await self._require_local_default_builder(docker)
            self._build_backend = build_backend
        except BaseException as exc:
            _end_observation(scope, output={"status": "failed"}, error=exc)
            raise
        _end_observation(
            scope,
            output={"status": "passed", "build_backend": self._build_backend},
        )

    async def run_all(
        self,
        runs: list[dict[str, Any]],
        *,
        concurrency: int = 2,
        observation_parent: Any = None,
    ) -> None:
        limit = max(1, min(int(concurrency), 2))
        with self._verification_lock:
            self._verified_contexts.clear()
        scope = _start_observation(
            observation_parent,
            "code-security.dynamic.runner",
            input={"candidate_count": len(runs), "concurrency": limit},
            metadata={"component": "docker"},
        )
        parent = _observation(scope, observation_parent)
        build_group_count = 0

        try:
            prepared = await self._prepare_runs(runs)
            groups: dict[BuildIdentity, list[PreparedCandidate]] = {}
            for candidate in prepared:
                groups.setdefault(candidate.build_identity, []).append(candidate)
            build_group_count = len(groups)
            semaphore = asyncio.Semaphore(limit)
            async with asyncio.TaskGroup() as tasks:
                for candidates in groups.values():
                    tasks.create_task(
                        self._run_build_group(
                            candidates,
                            semaphore=semaphore,
                            observation_parent=parent,
                        )
                    )
        except BaseException as exc:
            try:
                _, interrupted = await _await_uninterruptibly(
                    self.cleanup(observation_parent=parent)
                )
            except Exception as cleanup_exc:
                exc.add_note(
                    "Dynamic validation cleanup also failed: "
                    f"{type(cleanup_exc).__name__}"
                )
                interrupted = False
            _end_observation(
                scope,
                output={
                    "status": "failed",
                    "candidate_count": len(runs),
                    "build_group_count": build_group_count,
                },
                error=exc,
            )
            if interrupted and not isinstance(exc, asyncio.CancelledError):
                raise asyncio.CancelledError from exc
            raise
        try:
            _, interrupted = await _await_uninterruptibly(
                self.cleanup(observation_parent=parent)
            )
        except BaseException as exc:
            _end_observation(
                scope,
                output={
                    "status": "cleanup_failed",
                    "candidate_count": len(runs),
                    "build_group_count": build_group_count,
                },
                error=exc,
            )
            raise
        if interrupted:
            cancelled = asyncio.CancelledError()
            _end_observation(
                scope,
                output={
                    "status": "failed",
                    "candidate_count": len(runs),
                    "build_group_count": build_group_count,
                },
                error=cancelled,
            )
            raise cancelled
        _end_observation(
            scope,
            output={
                "status": "completed",
                "candidate_count": len(runs),
                "build_group_count": build_group_count,
            },
        )

    async def _prepare_runs(
        self,
        runs: list[dict[str, Any]],
    ) -> list[PreparedCandidate]:
        runtimes: dict[str, Path] = {}
        prepared: list[PreparedCandidate] = []
        for run in runs:
            scan_id = run["scan_id"]
            self._active_scan_ids.add(scan_id)
            runtime = runtimes.get(scan_id)
            if runtime is None:
                runtime = ensure_private_directory(docker_runtime_dir(scan_id))
                runtimes[scan_id] = runtime
            prepared.append(
                await asyncio.to_thread(self._prepare_candidate, run, runtime)
            )
        return prepared

    async def cleanup(self, *, observation_parent: Any = None) -> None:
        scope = _start_observation(
            observation_parent,
            "code-security.dynamic.cleanup",
            input={
                "container_count": len(self._containers),
                "image_count": len(self._images),
                "runtime_count": len(self._active_scan_ids),
            },
            metadata={"component": "docker"},
        )
        docker = shutil.which("docker")
        failures: list[str] = []
        try:
            if docker is None:
                failures.extend(f"container {item}" for item in sorted(self._containers))
                failures.extend(f"image {item}" for item in sorted(self._images))
            else:
                for container in sorted(self._containers):
                    result = await self._command(
                        [docker, "rm", "-f", container],
                        timeout_seconds=30,
                    )
                    if result["exit_code"] != 0:
                        failures.append(f"container {container}")
                    else:
                        self._containers.discard(container)
                for image in sorted(self._images):
                    result = await self._command(
                        [docker, "image", "rm", "-f", image],
                        timeout_seconds=60,
                    )
                    if result["exit_code"] != 0:
                        failures.append(f"image {image}")
                    else:
                        self._images.discard(image)
            for scan_id in tuple(self._active_scan_ids):
                path = docker_runtime_dir(scan_id)
                if path.exists():
                    await asyncio.to_thread(shutil.rmtree, path)
                self._active_scan_ids.discard(scan_id)
            if failures:
                raise RuntimeError("Dynamic validation cleanup failed for: " + ", ".join(failures))
        except BaseException as exc:
            _end_observation(
                scope,
                output={"status": "failed", "failure_count": len(failures)},
                error=exc,
            )
            raise
        _end_observation(scope, output={"status": "completed"})

    async def _run_build_group(
        self,
        candidates: list[PreparedCandidate],
        *,
        semaphore: asyncio.Semaphore,
        observation_parent: Any,
    ) -> None:
        if not candidates:
            raise ValueError("Dynamic build group cannot be empty")
        identity = candidates[0].build_identity
        if any(candidate.build_identity != identity for candidate in candidates):
            raise ValueError("Dynamic build group contains incompatible candidates")
        scope = _start_observation(
            observation_parent,
            "code-security.dynamic.build_group",
            input={
                "build_id": identity.digest,
                "candidate_count": len(candidates),
            },
            metadata={"component": "docker", "build_id": identity.digest},
        )
        parent = _observation(scope, observation_parent)
        try:
            outcome = await self._execute_build_group(
                candidates,
                semaphore=semaphore,
                observation_parent=parent,
            )
        except BaseException as exc:
            _end_observation(scope, output={"status": "failed"}, error=exc)
            raise
        _end_observation(scope, output=outcome)

    async def _execute_build_group(
        self,
        candidates: list[PreparedCandidate],
        *,
        semaphore: asyncio.Semaphore,
        observation_parent: Any,
    ) -> dict[str, Any]:
        prepared = candidates[0]
        identity = prepared.build_identity
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("Dynamic validation requires the Docker CLI")
        if self._build_backend is None:
            raise RuntimeError("Dynamic validation preflight has not completed")

        for index, image in enumerate(prepared.base_images):
            inspection = await self._observed_command(
                observation_parent,
                "base_image_check",
                [docker, "image", "inspect", image],
                timeout_seconds=30,
                input={"base_image_index": index},
            )
            if inspection["exit_code"] != 0:
                return await self._mark_build_group_inconclusive(
                    candidates,
                    build=inspection,
                    reason=f"Required local base image is unavailable: {image}",
                    observation_parent=observation_parent,
                )

        iidfile = prepared.runtime / f"build-{identity.digest}.iid"
        iidfile.unlink(missing_ok=True)
        build_argv = [
            docker,
            "build",
            *self._build_limit_arguments(self._build_backend),
            "--shm-size",
            BUILD_SHM_SIZE,
            "--ulimit",
            f"nproc={PROCESS_LIMIT}:{PROCESS_LIMIT}",
            "--network",
            "none",
            "--pull=false",
            "--no-cache",
            "--label",
            f"flocks.code_security.scan_id={identity.scan_id}",
            "--label",
            f"flocks.code_security.build_id={identity.digest}",
            "--iidfile",
            str(iidfile),
            "-f",
            str(prepared.dockerfile),
            str(prepared.context),
        ]
        async with self._build_lock:
            build = await self._observed_command(
                observation_parent,
                "build",
                build_argv,
                timeout_seconds=BUILD_TIMEOUT_SECONDS,
                env=self._build_environment(self._build_backend),
            )
        if build["timed_out"] or build["exit_code"] != 0 or not iidfile.is_file():
            reason = (
                "Docker build exceeded 300 seconds."
                if build["timed_out"]
                else "Docker build failed."
            )
            return await self._mark_build_group_inconclusive(
                candidates,
                build=build,
                reason=reason,
                observation_parent=observation_parent,
            )

        image_id = iidfile.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
            raise RuntimeError("Docker did not return a content-addressed image ID")
        self._images.add(image_id)
        outcomes: list[dict[str, Any]] = []

        async def run_one(candidate: PreparedCandidate) -> None:
            async with semaphore:
                outcomes.append(
                    await self._run_candidate(
                        candidate,
                        docker=docker,
                        image_id=image_id,
                        build=build,
                        observation_parent=observation_parent,
                    )
                )

        try:
            async with asyncio.TaskGroup() as tasks:
                for candidate in candidates:
                    tasks.create_task(run_one(candidate))
            completed = sum(item["status"] == "completed" for item in outcomes)
            return {
                "status": "completed" if completed == len(candidates) else "partial",
                "candidate_count": len(candidates),
                "completed_count": completed,
                "inconclusive_count": len(candidates) - completed,
            }
        finally:
            removal = await self._observed_command(
                observation_parent,
                "image_cleanup",
                [docker, "image", "rm", "-f", image_id],
                timeout_seconds=60,
            )
            if removal["exit_code"] == 0:
                self._images.discard(image_id)

    async def _mark_build_group_inconclusive(
        self,
        candidates: list[PreparedCandidate],
        *,
        build: CommandResult,
        reason: str,
        observation_parent: Any,
    ) -> dict[str, Any]:
        for candidate in candidates:
            scope = _start_observation(
                observation_parent,
                "code-security.dynamic.candidate",
                input={
                    "scan_id": candidate.build_identity.scan_id,
                    "candidate_id": candidate.candidate_id,
                },
                metadata={
                    "scan_id": candidate.build_identity.scan_id,
                    "candidate_id": candidate.candidate_id,
                },
            )
            try:
                outcome = await self._mark_inconclusive(
                    candidate.candidate_id,
                    {
                        "runner_status": "inconclusive",
                        "build_id": candidate.build_identity.digest,
                        "build": build,
                    },
                    phase="build",
                    reason=reason,
                )
            except BaseException as exc:
                _end_observation(scope, output={"status": "failed"}, error=exc)
                raise
            _end_observation(scope, output=outcome)
        return {
            "status": "inconclusive",
            "candidate_count": len(candidates),
            "failed_phase": "build",
        }

    async def _run_candidate(
        self,
        prepared: PreparedCandidate,
        *,
        docker: str,
        image_id: str,
        build: CommandResult,
        observation_parent: Any,
    ) -> dict[str, Any]:
        scan_id = prepared.build_identity.scan_id
        candidate_id = prepared.candidate_id
        scope = _start_observation(
            observation_parent,
            "code-security.dynamic.candidate",
            input={"scan_id": scan_id, "candidate_id": candidate_id},
            metadata={"scan_id": scan_id, "candidate_id": candidate_id},
        )
        parent = _observation(scope, observation_parent)
        try:
            outcome = await self._execute_candidate(
                prepared,
                docker=docker,
                image_id=image_id,
                build=build,
                observation_parent=parent,
            )
        except BaseException as exc:
            _end_observation(scope, output={"status": "failed"}, error=exc)
            raise
        _end_observation(scope, output=outcome)
        return outcome

    async def _execute_candidate(
        self,
        prepared: PreparedCandidate,
        *,
        docker: str,
        image_id: str,
        build: CommandResult,
        observation_parent: Any,
    ) -> dict[str, Any]:
        scan_id = prepared.build_identity.scan_id
        candidate_id = prepared.candidate_id
        probe = prepared.probe
        facts: dict[str, Any] = {
            "runner_status": "inconclusive",
            "build_id": prepared.build_identity.digest,
            "build": build,
            "image_id": image_id,
        }

        for phase in ("control", "attack"):
            name = self._container_name(scan_id, candidate_id, phase)
            self._containers.add(name)
            phase_probe = probe[phase]
            result = await self._observed_command(
                observation_parent,
                phase,
                [
                    docker,
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--read-only",
                    "--pids-limit",
                    str(PROCESS_LIMIT),
                    "--memory",
                    MEMORY_LIMIT,
                    "--cpus",
                    "1",
                    "--tmpfs",
                    f"/tmp:rw,nosuid,nodev,size={RUN_TMPFS_SIZE}",
                    "--label",
                    f"flocks.code_security.scan_id={scan_id}",
                    "--label",
                    f"flocks.code_security.candidate_id={candidate_id}",
                    "--label",
                    f"flocks.code_security.phase={phase}",
                    "--entrypoint",
                    "/bin/sh",
                    image_id,
                    "-c",
                    phase_probe["script"],
                ],
                timeout_seconds=phase_probe["timeout_seconds"],
                on_timeout=partial(self._remove_container, name),
            )
            if not result["timed_out"]:
                self._containers.discard(name)
            facts[phase] = result
            if result["timed_out"] or result["exit_code"] != 0:
                reason = (
                    f"Docker {phase} run timed out."
                    if result["timed_out"]
                    else f"Docker {phase} run failed."
                )
                return await self._mark_inconclusive(
                    candidate_id,
                    facts,
                    phase=phase,
                    reason=reason,
                )
        facts["runner_status"] = "completed"
        await asyncio.to_thread(
            self.store.complete_dynamic_run,
            candidate_id,
            "completed",
            facts,
        )
        return {"status": "completed"}

    def _prepare_candidate(
        self,
        run: dict[str, Any],
        runtime: Path,
    ) -> PreparedCandidate:
        """Load and verify trusted state without blocking the event loop."""
        scan_id = run["scan_id"]
        candidate_id = run["candidate_id"]
        scan = self.store.get_scan(scan_id)
        snapshot = self.store.get_snapshot(scan["snapshot_id"]) if scan else None
        if snapshot is None:
            raise ValueError("Dynamic run snapshot not found")
        snapshot_root = Path(snapshot.root_path)
        records = self.store.list_snapshot_files(snapshot.snapshot_id)
        snapshot_files = {item.relative_path for item in records}
        probe, base_images = _validate_probe(
            run["probe"],
            candidate_id=candidate_id,
            snapshot_root=snapshot_root,
            snapshot_files=snapshot_files,
        )
        verification_key = (
            snapshot.snapshot_id,
            snapshot.tree_digest,
            probe["context_path"],
        )
        # Snapshot roots are host-owned and read-only. Verify each shared context
        # once per runner batch so concurrent candidates do not rehash it.
        with self._verification_lock:
            if verification_key not in self._verified_contexts:
                self._verify_context_contents(
                    snapshot_root,
                    probe["context_path"],
                    records,
                )
                self._verified_contexts.add(verification_key)
        root = snapshot_root.resolve(strict=True)
        context = root if probe["context_path"] == "." else root / probe["context_path"]
        dockerfile = root / probe["dockerfile_path"]
        return PreparedCandidate(
            build_identity=BuildIdentity(
                scan_id=scan_id,
                snapshot_id=snapshot.snapshot_id,
                tree_digest=snapshot.tree_digest,
                context_path=probe["context_path"],
                dockerfile_path=probe["dockerfile_path"],
            ),
            candidate_id=candidate_id,
            probe=probe,
            context=context,
            dockerfile=dockerfile,
            runtime=runtime,
            base_images=base_images,
        )

    async def _mark_inconclusive(
        self,
        candidate_id: str,
        facts: dict[str, Any],
        *,
        phase: str,
        reason: str,
    ) -> dict[str, Any]:
        facts.update(
            runner_status="inconclusive",
            failed_phase=phase,
            reason=reason,
        )
        await asyncio.to_thread(
            self.store.complete_dynamic_run,
            candidate_id,
            "inconclusive",
            facts,
        )
        return {"status": "inconclusive", "failed_phase": phase}

    async def _observed_command(
        self,
        observation_parent: Any,
        operation: str,
        argv: list[str],
        *,
        timeout_seconds: int,
        input: dict[str, Any] | None = None,
        on_timeout: Callable[[], Awaitable[None]] | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        scope = _start_observation(
            observation_parent,
            f"code-security.dynamic.{operation}",
            input={"timeout_seconds": timeout_seconds, **(input or {})},
            metadata={"operation": operation},
        )
        try:
            result = await self._command(
                argv,
                timeout_seconds=timeout_seconds,
                on_timeout=on_timeout,
                env=env,
            )
        except BaseException as exc:
            _end_observation(scope, output={"status": "failed"}, error=exc)
            raise
        summary = _command_summary(result)
        if summary["status"] == "completed":
            _end_observation(scope, output=summary)
        else:
            _end_observation(
                scope,
                output=summary,
                level="ERROR",
                status_message=str(summary["status"]),
            )
        return result

    @staticmethod
    def _verify_context_contents(
        snapshot_root: Path,
        context_path: str,
        records: list[Any],
    ) -> None:
        context = snapshot_root if context_path == "." else snapshot_root / context_path
        expected = {
            item.relative_path: item
            for item in records
            if _inside(item.relative_path, context_path)
        }
        for relative_path, record in expected.items():
            path = snapshot_root / relative_path
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Snapshot context entry is not a regular file: {relative_path}")
            size = 0
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            if size != record.size_bytes or digest.hexdigest() != record.blob_digest:
                raise ValueError(f"Snapshot context content changed: {relative_path}")
        for path in context.rglob("*"):
            relative_path = path.relative_to(snapshot_root).as_posix()
            if path.is_symlink():
                raise ValueError(f"Snapshot context contains a symbolic link: {relative_path}")
            if path.is_file() and relative_path not in expected:
                raise ValueError(f"Snapshot context contains an unrecorded file: {relative_path}")
            if not path.is_dir() and not path.is_file():
                raise ValueError(f"Snapshot context contains an unsupported entry: {relative_path}")

    async def _remove_container(self, name: str) -> None:
        docker = shutil.which("docker")
        if docker is None:
            return
        result = await self._command(
            [docker, "rm", "-f", name],
            timeout_seconds=30,
        )
        if result["exit_code"] == 0:
            self._containers.discard(name)

    async def _select_build_backend(
        self,
        docker: str,
    ) -> Literal["buildkit", "legacy"]:
        buildkit_help = await self._command(
            [docker, "build", "--help"],
            timeout_seconds=15,
            env=self._build_environment("buildkit"),
        )
        buildkit_flags = ("--builder", "--resource", "--shm-size", "--ulimit")
        if self._supports_build_flags(buildkit_help, buildkit_flags):
            return "buildkit"

        legacy_help = await self._command(
            [docker, "build", "--help"],
            timeout_seconds=15,
            env=self._build_environment("legacy"),
        )
        required_flags = (
            "--memory",
            "--memory-swap",
            "--cpu-period",
            "--cpu-quota",
            "--shm-size",
            "--ulimit",
        )
        if self._supports_build_flags(legacy_help, required_flags):
            return "legacy"
        raise RuntimeError("Docker cannot enforce resource limits during image builds")

    async def _require_local_default_builder(self, docker: str) -> None:
        builder = await self._command(
            [docker, "buildx", "ls", "--format", "{{json .}}"],
            timeout_seconds=15,
            env=self._build_environment("buildkit"),
        )
        if builder["exit_code"] != 0 or builder["truncated"]:
            raise RuntimeError("Unable to list Docker builders")
        try:
            builders = [
                json.loads(line)
                for line in builder["stdout"].splitlines()
                if line.strip()
            ]
        except json.JSONDecodeError as exc:
            raise RuntimeError("Docker returned an invalid builder list") from exc
        default_builder = next(
            (
                item
                for item in builders
                if isinstance(item, dict) and item.get("Name") == "default"
            ),
            None,
        )
        if default_builder is None or default_builder.get("Driver") != "docker":
            raise RuntimeError(
                "Dynamic validation requires the local default Docker builder"
            )

    @staticmethod
    def _supports_build_flags(result: CommandResult, flags: tuple[str, ...]) -> bool:
        if result["exit_code"] != 0:
            return False
        return all(
            re.search(
                rf"(?m)^\s*(?:-\S+,\s+)?{re.escape(flag)}(?:[=\s]|$)",
                result["stdout"],
            )
            for flag in flags
        )

    @staticmethod
    def _build_limit_arguments(backend: Literal["buildkit", "legacy"]) -> list[str]:
        if backend == "buildkit":
            return [
                "--builder",
                "default",
                "--resource",
                f"memory={MEMORY_LIMIT}",
                "--resource",
                f"memory-swap={MEMORY_LIMIT}",
                "--resource",
                f"cpu-period={BUILD_CPU_PERIOD}",
                "--resource",
                f"cpu-quota={BUILD_CPU_QUOTA}",
            ]
        return [
            "--memory",
            MEMORY_LIMIT,
            "--memory-swap",
            MEMORY_LIMIT,
            "--cpu-period",
            str(BUILD_CPU_PERIOD),
            "--cpu-quota",
            str(BUILD_CPU_QUOTA),
        ]

    @staticmethod
    def _build_environment(backend: Literal["buildkit", "legacy"]) -> dict[str, str]:
        environment = os.environ.copy()
        for name in _REMOTE_BUILD_ENVIRONMENT:
            environment.pop(name, None)
        environment["DOCKER_BUILDKIT"] = "1" if backend == "buildkit" else "0"
        return environment

    @staticmethod
    def _container_name(scan_id: str, candidate_id: str, phase: str) -> str:
        return f"flocks-{scan_id[5:17]}-{candidate_id[-12:]}-{phase}"

    async def _command(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        on_timeout: Callable[[], Awaitable[None]] | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        async def read_bounded(stream: asyncio.StreamReader | None) -> tuple[str, bool]:
            if stream is None:
                return "", False
            saved = bytearray()
            truncated = False
            while chunk := await stream.read(16 * 1024):
                remaining = MAX_LOG_BYTES - len(saved)
                if remaining > 0:
                    saved.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
            return saved.decode("utf-8", errors="replace"), truncated

        stdout_task = asyncio.create_task(read_bounded(process.stdout))
        stderr_task = asyncio.create_task(read_bounded(process.stderr))
        timed_out = False

        async def terminate() -> Exception | None:
            cleanup_error: Exception | None = None
            try:
                if on_timeout is not None:
                    try:
                        await on_timeout()
                    except Exception as exc:
                        cleanup_error = exc
            finally:
                try:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                finally:
                    await asyncio.gather(
                        stdout_task,
                        stderr_task,
                        return_exceptions=True,
                    )
            return cleanup_error

        timeout_cleanup_error: Exception | None = None
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            timeout_cleanup_error, interrupted = await _await_uninterruptibly(terminate())
            if interrupted:
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            try:
                await _await_uninterruptibly(terminate())
            except BaseException:
                pass
            raise
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        if timeout_cleanup_error is not None:
            raise RuntimeError(
                "Timed-out Docker container cleanup failed"
            ) from timeout_cleanup_error
        return {
            "exit_code": None if timed_out else process.returncode,
            "duration_ms": round((time.monotonic() - started) * 1_000),
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "truncated": stdout_truncated or stderr_truncated,
        }
