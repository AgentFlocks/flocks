"""Trusted validation and local-Docker execution for dynamic audit probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from flocks_code_security.paths import docker_runtime_dir, ensure_private_directory
from flocks_code_security.snapshot import normalize_relative_path


MAX_SCRIPT_BYTES = 16 * 1024
MAX_EXPECTED_DIFFERENCE = 4_000
MAX_LOG_BYTES = 64 * 1024
BUILD_TIMEOUT_SECONDS = 300
_FROM_RE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.I)
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")


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


def _probe_path(value: Any, *, allow_root: bool) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("Probe paths must use canonical snapshot-relative syntax")
    return normalize_relative_path(value, allow_root=allow_root)


def _inside(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(f"{directory}/")


def validate_probe(
    probe: dict[str, Any],
    *,
    candidate_id: str,
    snapshot_root: Path,
    snapshot_files: set[str],
) -> dict[str, Any]:
    """Return a canonical probe after validating its complete persisted contract."""
    if not isinstance(probe, dict):
        raise ValueError("probe must be an object")
    if probe.get("candidate_id") != candidate_id:
        raise ValueError("Probe candidate_id is not bound to this work unit")
    status = probe.get("status")
    if status == "not_runnable":
        if set(probe) != {"candidate_id", "status", "reason"}:
            raise ValueError("not_runnable probes require only candidate_id, status, and reason")
        reason = _bounded_text(probe.get("reason"), field="reason", max_bytes=4_000).strip()
        return {
            "candidate_id": candidate_id,
            "status": "not_runnable",
            "reason": reason,
        }
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
    context = (root / context_path).resolve(strict=True)
    dockerfile = (root / dockerfile_path).resolve(strict=True)
    if not context.is_relative_to(root) or not dockerfile.is_relative_to(context):
        raise ValueError("Probe path escapes the immutable snapshot")
    if context.is_symlink() or not context.is_dir():
        raise ValueError("context_path must name a snapshot directory")
    if dockerfile.is_symlink() or not dockerfile.is_file():
        raise ValueError("dockerfile_path must name a regular snapshot file")
    for path in (context, dockerfile):
        current = path
        while current != root:
            if current.is_symlink():
                raise ValueError("Probe path contains a symbolic link")
            current = current.parent

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
    expected_difference = _bounded_text(
        probe.get("expected_difference"),
        field="expected_difference",
        max_bytes=MAX_EXPECTED_DIFFERENCE,
    ).strip()

    _dockerfile_base_images(dockerfile.read_text(encoding="utf-8"))
    return {
        "candidate_id": candidate_id,
        "status": "runnable",
        "context_path": context_path,
        "dockerfile_path": dockerfile_path,
        "control": phases["control"],
        "attack": phases["attack"],
        "expected_difference": expected_difference,
    }


def _dockerfile_base_images(contents: str) -> list[str]:
    """Accept a deliberately small, offline-checkable Dockerfile subset."""
    logical_lines: list[str] = []
    pending = ""
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.lower().startswith("# syntax="):
                raise ValueError("Remote Dockerfile frontends are not supported")
            continue
        pending += line
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        logical_lines.append(pending.strip())
        pending = ""
    if pending:
        raise ValueError("Dockerfile has an unterminated continuation")

    images: list[str] = []
    stages: set[str] = set()
    for line in logical_lines:
        upper = line.upper()
        if upper.startswith("ADD ") and re.search(r"(?:^|\s)https?://", line, re.I):
            raise ValueError("Remote Dockerfile sources are not supported")
        copy_from = re.match(r"^COPY\s+--from=(\S+)\s+", line, re.I)
        if copy_from is not None:
            source = copy_from.group(1)
            if (
                "$" in source
                or source.lower().startswith(("http://", "https://"))
                or _IMAGE_RE.fullmatch(source) is None
            ):
                raise ValueError("Remote Dockerfile sources are not supported")
            if source not in stages and not source.isdigit():
                images.append(source)
        if not upper.startswith("FROM "):
            continue
        match = _FROM_RE.fullmatch(line)
        if match is None:
            raise ValueError("Dockerfile FROM instruction is not safely supported")
        image, alias = match.groups()
        if (
            "$" in image
            or image.lower().startswith(("http://", "https://"))
            or _IMAGE_RE.fullmatch(image) is None
        ):
            raise ValueError("Dockerfile base image must be a literal local image")
        if image not in stages:
            images.append(image)
        if alias:
            stages.add(alias)
    if not images:
        raise ValueError("Dockerfile must declare a literal base image")
    return list(dict.fromkeys(images))


class DockerDynamicRunner:
    """Execute validated probes without assigning vulnerability semantics."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self._build_lock = asyncio.Lock()
        self._containers: set[str] = set()
        self._images: set[str] = set()
        self._active_scan_ids: set[str] = set()

    async def preflight(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("Dynamic validation requires the Docker CLI")
        endpoint = os.environ.get("DOCKER_HOST", "")
        if endpoint and not endpoint.startswith(("unix://", "npipe://")):
            raise RuntimeError("Remote Docker endpoints are not allowed")
        context = await self._command(
            [docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            timeout_seconds=15,
        )
        if context["exit_code"] != 0:
            raise RuntimeError("Unable to inspect the active Docker context")
        try:
            active_endpoint = json.loads(context["stdout"].strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("Docker returned an invalid active endpoint") from exc
        if not isinstance(active_endpoint, str) or not active_endpoint.startswith(("unix://", "npipe://")):
            raise RuntimeError("Remote Docker endpoints are not allowed")
        version = await self._command([docker, "version"], timeout_seconds=15)
        if version["exit_code"] != 0:
            raise RuntimeError("The local Docker daemon is unavailable")

    async def run_all(self, runs: list[dict[str, Any]], *, concurrency: int = 2) -> None:
        semaphore = asyncio.Semaphore(max(1, min(int(concurrency), 2)))

        async def run_one(run: dict[str, Any]) -> None:
            async with semaphore:
                await self._run_candidate(run)

        try:
            await asyncio.gather(*(run_one(run) for run in runs))
        finally:
            await asyncio.shield(self.cleanup())

    async def cleanup(self) -> None:
        docker = shutil.which("docker")
        failures: list[str] = []
        if docker is None:
            failures.extend(f"container {item}" for item in sorted(self._containers))
            failures.extend(f"image {item}" for item in sorted(self._images))
        else:
            for container in sorted(self._containers):
                result = await self._command([docker, "rm", "-f", container], timeout_seconds=30)
                if result["exit_code"] != 0:
                    failures.append(f"container {container}")
                else:
                    self._containers.discard(container)
            for image in sorted(self._images):
                result = await self._command([docker, "image", "rm", "-f", image], timeout_seconds=60)
                if result["exit_code"] != 0:
                    failures.append(f"image {image}")
                else:
                    self._images.discard(image)
        for scan_id in self._active_scan_ids:
            path = docker_runtime_dir(scan_id)
            if path.exists():
                shutil.rmtree(path)
        if failures:
            raise RuntimeError("Dynamic validation cleanup failed for: " + ", ".join(failures))

    async def _run_candidate(self, run: dict[str, Any]) -> None:
        scan_id = run["scan_id"]
        self._active_scan_ids.add(scan_id)
        candidate_id = run["candidate_id"]
        probe = run["probe"]
        scan = self.store.get_scan(scan_id)
        snapshot = self.store.get_snapshot(scan["snapshot_id"]) if scan else None
        if snapshot is None:
            raise ValueError("Dynamic run snapshot not found")
        runtime = ensure_private_directory(docker_runtime_dir(scan_id))
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("Dynamic validation requires the Docker CLI")

        snapshot_root = Path(snapshot.root_path)
        records = self.store.list_snapshot_files(snapshot.snapshot_id)
        snapshot_files = {item.relative_path for item in records}
        probe = validate_probe(
            probe,
            candidate_id=candidate_id,
            snapshot_root=snapshot_root,
            snapshot_files=snapshot_files,
        )
        self._verify_context_contents(
            snapshot_root,
            probe["context_path"],
            records,
        )
        context = snapshot_root if probe["context_path"] == "." else snapshot_root / probe["context_path"]
        dockerfile = snapshot_root / probe["dockerfile_path"]
        facts: dict[str, Any] = {"runner_status": "inconclusive"}
        image_id: str | None = None
        try:
            for image in _dockerfile_base_images(dockerfile.read_text(encoding="utf-8")):
                inspection = await self._command(
                    [docker, "image", "inspect", image],
                    timeout_seconds=30,
                )
                if inspection["exit_code"] != 0:
                    facts.update(
                        failed_phase="build",
                        reason=f"Required local base image is unavailable: {image}",
                        build=inspection,
                    )
                    self.store.complete_dynamic_run(candidate_id, "inconclusive", facts)
                    return

            iidfile = runtime / f"{candidate_id}.iid"
            build_argv = [
                docker,
                "build",
                "--network",
                "none",
                "--pull=false",
                "--no-cache",
                "--label",
                f"flocks.code_security.scan_id={scan_id}",
                "--label",
                f"flocks.code_security.candidate_id={candidate_id}",
                "--iidfile",
                str(iidfile),
                "-f",
                str(dockerfile),
                str(context),
            ]
            async with self._build_lock:
                build = await self._command(build_argv, timeout_seconds=BUILD_TIMEOUT_SECONDS)
            facts["build"] = build
            if build["timed_out"] or build["exit_code"] != 0 or not iidfile.is_file():
                facts.update(
                    failed_phase="build",
                    reason=("Docker build exceeded 300 seconds." if build["timed_out"] else "Docker build failed."),
                )
                self.store.complete_dynamic_run(candidate_id, "inconclusive", facts)
                return
            image_id = iidfile.read_text(encoding="utf-8").strip()
            if re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
                raise RuntimeError("Docker did not return a content-addressed image ID")
            self._images.add(image_id)
            facts["image_id"] = image_id

            for phase in ("control", "attack"):
                name = self._container_name(scan_id, candidate_id, phase)
                self._containers.add(name)
                phase_probe = probe[phase]
                result = await self._command(
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
                        "128",
                        "--memory",
                        "512m",
                        "--cpus",
                        "1",
                        "--tmpfs",
                        "/tmp:rw,nosuid,nodev,size=64m",
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
                    on_timeout=lambda name=name: self._remove_container(name),
                )
                if not result["timed_out"]:
                    self._containers.discard(name)
                facts[phase] = result
                if result["timed_out"] or result["exit_code"] != 0:
                    facts.update(
                        failed_phase=phase,
                        reason=(
                            f"Docker {phase} run timed out." if result["timed_out"] else f"Docker {phase} run failed."
                        ),
                    )
                    self.store.complete_dynamic_run(candidate_id, "inconclusive", facts)
                    return
            facts["runner_status"] = "completed"
            self.store.complete_dynamic_run(candidate_id, "completed", facts)
        finally:
            if image_id is not None:
                removal = await self._command(
                    [docker, "image", "rm", "-f", image_id],
                    timeout_seconds=60,
                )
                if removal["exit_code"] == 0:
                    self._images.discard(image_id)

    @staticmethod
    def _verify_context_contents(
        snapshot_root: Path,
        context_path: str,
        records: list[Any],
    ) -> None:
        context = snapshot_root if context_path == "." else snapshot_root / context_path
        expected = {item.relative_path: item for item in records if _inside(item.relative_path, context_path)}
        for relative_path, record in expected.items():
            path = snapshot_root / relative_path
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Snapshot context entry is not a regular file: {relative_path}")
            data = path.read_bytes()
            if len(data) != record.size_bytes or hashlib.sha256(data).hexdigest() != record.blob_digest:
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

    @staticmethod
    def _container_name(scan_id: str, candidate_id: str, phase: str) -> str:
        return f"flocks-{scan_id[5:17]}-{candidate_id[-12:]}-{phase}"

    async def _command(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        on_timeout: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
        timeout_cleanup_error: Exception | None = None
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            if on_timeout is not None:
                try:
                    await on_timeout()
                except Exception as exc:
                    timeout_cleanup_error = exc
            if process.returncode is None:
                process.kill()
                await process.wait()
        except asyncio.CancelledError:
            if on_timeout is not None:
                try:
                    await asyncio.shield(on_timeout())
                except Exception:
                    pass
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(
                stdout_task,
                stderr_task,
                return_exceptions=True,
            )
            raise
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        if timeout_cleanup_error is not None:
            raise RuntimeError("Timed-out Docker container cleanup failed") from timeout_cleanup_error
        return {
            "exit_code": None if timed_out else process.returncode,
            "duration_ms": round((time.monotonic() - started) * 1_000),
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "truncated": stdout_truncated or stderr_truncated,
        }
