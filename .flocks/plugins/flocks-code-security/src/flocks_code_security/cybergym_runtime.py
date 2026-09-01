"""Restricted CyberGym Level 1 raw-input execution runtime.

The solver never supplies a shell command, image, mount, or executable.  Those
values come from the trusted task manifest persisted by :mod:`store` before a
solver session starts.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Protocol


_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,511}$")
_CONTAINER_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]{0,1023}$")
_BREAKPOINT_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_:<>~]*|[A-Za-z0-9._/-]+:[1-9][0-9]*)$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ARTIFACT_KINDS = {"seed", "corpus", "crash", "minimized", "dictionary"}
_MAX_OUTPUT_BYTES = 64 * 1024


class CyberGymManifestError(ValueError):
    """The host-supplied execution contract is incomplete or unsafe."""


@dataclass(frozen=True)
class CyberGymLimits:
    replay_seconds: int = 60
    gdb_seconds: int = 30
    fuzz_seconds: int = 300
    max_artifact_bytes: int = 4 * 1024 * 1024
    max_replay_runs: int = 16
    max_gdb_runs: int = 8
    max_fuzz_runs: int = 2
    max_minimize_runs: int = 4

    @classmethod
    def from_dict(cls, value: Any) -> "CyberGymLimits":
        if not isinstance(value, dict):
            raise CyberGymManifestError("manifest limits must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise CyberGymManifestError(f"manifest limits contain unknown fields: {', '.join(unknown)}")
        defaults = cls()
        values: dict[str, int] = {}
        for name in allowed:
            raw = value.get(name, getattr(defaults, name))
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
                raise CyberGymManifestError(f"manifest limit {name} must be a positive integer")
            values[name] = raw
        if values["replay_seconds"] > 600 or values["gdb_seconds"] > 300 or values["fuzz_seconds"] > 1800:
            raise CyberGymManifestError("manifest execution timeout exceeds Level 1 maximum")
        if values["max_artifact_bytes"] > 16 * 1024 * 1024:
            raise CyberGymManifestError("manifest max_artifact_bytes exceeds Level 1 maximum")
        if any(values[name] > 64 for name in ("max_replay_runs", "max_gdb_runs", "max_fuzz_runs", "max_minimize_runs")):
            raise CyberGymManifestError("manifest run budget exceeds Level 1 maximum")
        return cls(**values)


@dataclass(frozen=True)
class CyberGymTargetManifest:
    task_id: str
    task_kind: str
    vulnerable_runner: str
    target_binary: str
    argv_template: tuple[str, ...]
    input_path: str
    allow_empty_input: bool
    fuzzer_supported: bool
    fuzzer_target: str | None
    gdb_supported: bool
    limits: CyberGymLimits

    @classmethod
    def from_dict(cls, value: Any) -> "CyberGymTargetManifest":
        if not isinstance(value, dict):
            raise CyberGymManifestError("cybergym manifest must be an object")
        allowed = {
            "task_id", "task_kind", "vulnerable_runner", "target_binary", "argv_template",
            "input_path", "allow_empty_input", "fuzzer_supported", "fuzzer_target",
            "gdb_supported", "limits",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise CyberGymManifestError(f"cybergym manifest contains unknown fields: {', '.join(unknown)}")
        required = allowed - {"allow_empty_input", "fuzzer_target"}
        missing = sorted(name for name in required if name not in value)
        if missing:
            raise CyberGymManifestError(f"cybergym manifest is missing: {', '.join(missing)}")
        task_id = _bounded_string(value["task_id"], "task_id", 128)
        task_kind = _bounded_string(value["task_kind"], "task_kind", 32)
        if task_kind not in {"arvo", "oss_fuzz", "other"}:
            raise CyberGymManifestError("task_kind must be arvo, oss_fuzz, or other")
        runner = _bounded_string(value["vulnerable_runner"], "vulnerable_runner", 512)
        if not _IMAGE_RE.fullmatch(runner):
            raise CyberGymManifestError("vulnerable_runner must be a Docker image reference")
        binary = _container_path(value["target_binary"], "target_binary")
        input_path = _container_path(value["input_path"], "input_path")
        if str(PurePosixPath(input_path).parent) == "/":
            raise CyberGymManifestError("input_path must be beneath a dedicated container scratch directory")
        argv = value["argv_template"]
        if not isinstance(argv, list) or len(argv) > 64 or not all(isinstance(item, str) for item in argv):
            raise CyberGymManifestError("argv_template must be an array of at most 64 strings")
        argv_template = tuple(_bounded_string(item, "argv_template item", 4096) for item in argv)
        if sum(item.count("{input}") for item in argv_template) != 1:
            raise CyberGymManifestError("argv_template must contain exactly one {input} placeholder")
        if any("{" in item.replace("{input}", "") or "}" in item.replace("{input}", "") for item in argv_template):
            raise CyberGymManifestError("argv_template only supports the {input} placeholder")
        for boolean in ("fuzzer_supported", "gdb_supported"):
            if not isinstance(value[boolean], bool):
                raise CyberGymManifestError(f"{boolean} must be a boolean")
        fuzzer_target = value.get("fuzzer_target")
        if value["fuzzer_supported"]:
            fuzzer_target = _container_path(fuzzer_target, "fuzzer_target")
        elif fuzzer_target is not None:
            raise CyberGymManifestError("fuzzer_target requires fuzzer_supported=true")
        allow_empty = value.get("allow_empty_input", False)
        if not isinstance(allow_empty, bool):
            raise CyberGymManifestError("allow_empty_input must be a boolean")
        return cls(
            task_id=task_id,
            task_kind=task_kind,
            vulnerable_runner=runner,
            target_binary=binary,
            argv_template=argv_template,
            input_path=input_path,
            allow_empty_input=allow_empty,
            fuzzer_supported=value["fuzzer_supported"],
            fuzzer_target=fuzzer_target,
            gdb_supported=value["gdb_supported"],
            limits=CyberGymLimits.from_dict(value["limits"]),
        )

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["argv_template"] = list(self.argv_template)
        return value


def _bounded_string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\0" in value:
        raise CyberGymManifestError(f"{field} must be a non-empty string of at most {maximum} characters")
    return value


def _container_path(value: Any, field: str) -> str:
    path = _bounded_string(value, field, 1024)
    parsed = PurePosixPath(path)
    if not _CONTAINER_PATH_RE.fullmatch(path) or not parsed.is_absolute() or ".." in parsed.parts:
        raise CyberGymManifestError(f"{field} must be a normalized absolute container path")
    return path


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    unavailable: bool = False


class CommandExecutor(Protocol):
    async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult: ...


class DockerCommandExecutor:
    async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return CommandResult(None, "", "docker executable is unavailable", unavailable=True)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            return CommandResult(process.returncode, _decode_output(stdout), _decode_output(stderr), timed_out=True)
        return CommandResult(process.returncode, _decode_output(stdout), _decode_output(stderr))


def _decode_output(value: bytes) -> str:
    return value[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")


OfficialSubmitter = Callable[[CyberGymTargetManifest, bytes, dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


class CyberGymRuntime:
    """Host-owned restricted execution layer backed by ``ScanStore``."""

    def __init__(
        self,
        store: Any,
        *,
        executor: CommandExecutor | None = None,
        submitter: OfficialSubmitter | None = None,
    ) -> None:
        self.store = store
        self.executor = executor or DockerCommandExecutor()
        self.submitter = submitter
        self._fuzz_tasks: dict[str, asyncio.Task[None]] = {}

    def context(self, scan_id: str) -> dict[str, Any]:
        return self.store.cybergym_context(scan_id)

    def artifact_create(
        self,
        scan_id: str,
        *,
        kind: str,
        raw: bytes,
        parent_id: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = self._manifest(scan_id)
        if kind not in _ARTIFACT_KINDS:
            raise ValueError("Unsupported CyberGym artifact kind")
        if not raw and not manifest.allow_empty_input:
            raise ValueError("The trusted manifest does not allow empty input")
        if len(raw) > manifest.limits.max_artifact_bytes:
            raise ValueError("Artifact exceeds the trusted manifest size limit")
        return self.store.create_cybergym_artifact(
            scan_id,
            kind=kind,
            raw=raw,
            parent_id=parent_id,
            provenance=provenance or {},
        )

    async def replay(self, scan_id: str, artifact_id: str) -> dict[str, Any]:
        manifest = self._manifest(scan_id)
        self.store.consume_cybergym_budget(scan_id, "replay", manifest.limits.max_replay_runs)
        artifact = self.store.get_cybergym_artifact(scan_id, artifact_id, include_data=True)
        if artifact is None:
            raise ValueError("Artifact is not available for this CyberGym task")
        run = self.store.start_cybergym_run(scan_id, "replay", {"artifact_id": artifact_id})
        try:
            result = await self._execute_replay(manifest, artifact["data"])
            self.store.finish_cybergym_run(run["run_id"], "completed", result)
            return {"run_id": run["run_id"], **result}
        except BaseException as exc:
            self.store.finish_cybergym_run(
                run["run_id"], "failed", {"status": "runtime_error", "error": type(exc).__name__}
            )
            raise

    async def gdb(self, scan_id: str, artifact_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        manifest = self._manifest(scan_id)
        if not manifest.gdb_supported:
            return {"status": "gdb_unavailable", "reason": "disabled_by_trusted_manifest"}
        breakpoints, variables = _validate_gdb_intent(intent)
        self.store.consume_cybergym_budget(scan_id, "gdb", manifest.limits.max_gdb_runs)
        artifact = self.store.get_cybergym_artifact(scan_id, artifact_id, include_data=True)
        if artifact is None:
            raise ValueError("Artifact is not available for this CyberGym task")
        run = self.store.start_cybergym_run(
            scan_id, "gdb", {"artifact_id": artifact_id, "intent": {"breakpoints": breakpoints, "variables": variables}}
        )
        try:
            result = await self._execute_gdb(manifest, artifact["data"], breakpoints, variables)
            self.store.finish_cybergym_run(run["run_id"], "completed", result)
            return {"run_id": run["run_id"], **result}
        except BaseException as exc:
            self.store.finish_cybergym_run(
                run["run_id"], "failed", {"status": "runtime_error", "error": type(exc).__name__}
            )
            raise

    async def fuzz_start(
        self,
        scan_id: str,
        seed_ids: list[str],
        *,
        dictionary: list[str] | None = None,
        budget_seconds: int | None = None,
        max_length: int | None = None,
    ) -> dict[str, Any]:
        manifest = self._manifest(scan_id)
        if not manifest.fuzzer_supported or manifest.fuzzer_target is None:
            raise ValueError("Fuzzing is disabled by the trusted manifest")
        if not seed_ids or len(seed_ids) > 32 or len(set(seed_ids)) != len(seed_ids):
            raise ValueError("fuzz_start requires one to 32 distinct seed artifact IDs")
        if dictionary is not None and (
            len(dictionary) > 128
            or any(not isinstance(item, str) or not item or len(item) > 256 or "\0" in item for item in dictionary)
        ):
            raise ValueError("dictionary must contain at most 128 non-empty short strings")
        seconds = manifest.limits.fuzz_seconds if budget_seconds is None else budget_seconds
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= manifest.limits.fuzz_seconds:
            raise ValueError("fuzz budget exceeds the trusted manifest limit")
        if max_length is not None and (
            not isinstance(max_length, int) or isinstance(max_length, bool) or not 1 <= max_length <= manifest.limits.max_artifact_bytes
        ):
            raise ValueError("max_length exceeds the trusted manifest artifact limit")
        seeds = []
        for artifact_id in seed_ids:
            artifact = self.store.get_cybergym_artifact(scan_id, artifact_id, include_data=True)
            if artifact is None:
                raise ValueError("A fuzz seed artifact is not available for this CyberGym task")
            seeds.append(artifact)
        self.store.consume_cybergym_budget(scan_id, "fuzz", manifest.limits.max_fuzz_runs)
        run = self.store.start_cybergym_run(
            scan_id,
            "fuzz",
            {"seed_ids": seed_ids, "budget_seconds": seconds, "max_length": max_length},
        )
        task = asyncio.create_task(
            self._run_fuzz(scan_id, run["run_id"], manifest, seeds, dictionary or [], seconds, max_length),
            name=f"cybergym-fuzz:{run['run_id']}",
        )
        self._fuzz_tasks[run["run_id"]] = task
        task.add_done_callback(lambda _task: self._fuzz_tasks.pop(run["run_id"], None))
        return {"run_id": run["run_id"], "status": "running"}

    def fuzz_status(self, scan_id: str, run_id: str) -> dict[str, Any]:
        run = self.store.get_cybergym_run(scan_id, run_id)
        if run is None or run["kind"] != "fuzz":
            raise ValueError("Fuzz run is not available for this CyberGym task")
        return run

    async def minimize(self, scan_id: str, artifact_id: str) -> dict[str, Any]:
        manifest = self._manifest(scan_id)
        if not manifest.fuzzer_supported or manifest.fuzzer_target is None:
            return {"status": "minimize_unavailable", "reason": "fuzzer_disabled_by_trusted_manifest"}
        self.store.consume_cybergym_budget(scan_id, "minimize", manifest.limits.max_minimize_runs)
        artifact = self.store.get_cybergym_artifact(scan_id, artifact_id, include_data=True)
        if artifact is None:
            raise ValueError("Artifact is not available for this CyberGym task")
        run = self.store.start_cybergym_run(scan_id, "minimize", {"artifact_id": artifact_id})
        try:
            result = await self._execute_minimize(manifest, artifact["data"])
            minimized_id = None
            minimized_data = result.pop("minimized_data", None)
            if minimized_data is not None:
                minimized = self.artifact_create(
                    scan_id,
                    kind="minimized",
                    raw=minimized_data,
                    parent_id=artifact_id,
                    provenance={"run_id": run["run_id"], "operation": "libfuzzer_minimize"},
                )
                minimized_id = minimized["artifact_id"]
                replay = await self.replay(scan_id, minimized_id)
                result["minimized_artifact_id"] = minimized_id
                result["replay"] = replay
            self.store.finish_cybergym_run(run["run_id"], "completed", result)
            return {"run_id": run["run_id"], **result}
        except BaseException as exc:
            self.store.finish_cybergym_run(
                run["run_id"], "failed", {"status": "runtime_error", "error": type(exc).__name__}
            )
            raise

    async def submit(
        self,
        scan_id: str,
        artifact_id: str,
        *,
        local_validation: str,
        selection_reason: str,
    ) -> dict[str, Any]:
        if local_validation not in {"verified", "unverified"}:
            raise ValueError("local_validation must be verified or unverified")
        if not isinstance(selection_reason, str) or not selection_reason.strip() or len(selection_reason) > 2_000:
            raise ValueError("selection_reason must be a non-empty string of at most 2000 characters")
        manifest = self._manifest(scan_id)
        artifact = self.store.get_cybergym_artifact(scan_id, artifact_id, include_data=True)
        if artifact is None:
            raise ValueError("audit_cybergym_submit requires an existing artifact_id")
        if not artifact["size"] and not manifest.allow_empty_input:
            raise ValueError("The trusted manifest does not allow empty input")
        if local_validation == "verified" and not self.store.cybergym_artifact_has_stable_crash(scan_id, artifact_id):
            raise ValueError("verified submission requires a stable vulnerable replay crash")
        self.store.assert_cybergym_runs_terminal(scan_id)
        evidence = self.store.cybergym_artifact_evidence(scan_id, artifact_id)
        submission = self.store.reserve_cybergym_submission(
            scan_id,
            artifact_id=artifact_id,
            local_validation=local_validation,
            selection_reason=selection_reason.strip(),
            evidence=evidence,
        )
        try:
            official_result: dict[str, Any]
            if self.submitter is None:
                official_result = {"status": "not_configured", "reason": "official_submitter_unconfigured"}
            else:
                maybe_result = self.submitter(manifest, artifact["data"], _public_artifact(artifact))
                official_result = await maybe_result if inspect.isawaitable(maybe_result) else maybe_result
                if not isinstance(official_result, dict):
                    raise ValueError("Official submitter must return an object")
            self.store.complete_cybergym_submission(scan_id, official_result)
        except BaseException as exc:
            self.store.complete_cybergym_submission(
                scan_id, {"status": "submit_failed", "error": type(exc).__name__}
            )
            raise
        return self.store.get_cybergym_submission(scan_id) or submission

    def select_final_artifact(self, scan_id: str) -> dict[str, Any] | None:
        return self.store.select_cybergym_final_artifact(scan_id)

    def mark_failed_no_artifact(self, scan_id: str) -> dict[str, Any]:
        return self.store.mark_cybergym_failed_no_artifact(scan_id)

    def _manifest(self, scan_id: str) -> CyberGymTargetManifest:
        task = self.store.get_cybergym_task(scan_id)
        if task is None:
            raise ValueError("CyberGym Level 1 is not enabled for this scan")
        return CyberGymTargetManifest.from_dict(task["manifest"])

    async def _execute_replay(self, manifest: CyberGymTargetManifest, raw: bytes) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="cybergym-replay-") as temporary:
            scratch = Path(temporary)
            input_file = scratch / "input"
            input_file.write_bytes(raw)
            input_file.chmod(0o600)
            command = self._container_command(manifest, scratch)
            command.extend([manifest.target_binary, *self._argv(manifest)])
            result = await self.executor.run(command, timeout_seconds=manifest.limits.replay_seconds)
        status = _execution_status(result)
        return {
            "status": status,
            "crash": status == "crash",
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def _execute_gdb(
        self,
        manifest: CyberGymTargetManifest,
        raw: bytes,
        breakpoints: list[dict[str, str]],
        variables: list[str],
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="cybergym-gdb-") as temporary:
            scratch = Path(temporary)
            (scratch / "input").write_bytes(raw)
            command = self._container_command(manifest, scratch, gdb=True)
            command.extend(["gdb", "-q", "-nx", "-batch", "-ex", "set pagination off"])
            for breakpoint in breakpoints:
                command.extend(["-ex", f"break {breakpoint['location']}"])
            command.extend(["-ex", "run"])
            for variable in variables:
                command.extend(["-ex", f"print {variable}"])
            command.extend(["-ex", "bt 20", "--args", manifest.target_binary, *self._argv(manifest)])
            result = await self.executor.run(command, timeout_seconds=manifest.limits.gdb_seconds)
        if result.unavailable or result.returncode in {126, 127}:
            return {
                "status": "gdb_unavailable",
                "reason": "docker_unavailable" if result.unavailable else "gdb_unavailable_in_runner",
            }
        output = f"{result.stdout}\n{result.stderr}"
        hits = {
            breakpoint["kind"]: bool(re.search(rf"Breakpoint {index + 1}[, ]", output))
            for index, breakpoint in enumerate(breakpoints)
        }
        return {
            "status": "completed" if not result.timed_out else "timeout",
            "target_reached": hits.get("target", False),
            "vulnerable_branch_reached": hits.get("vulnerable_branch", False),
            "breakpoints": hits,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    async def _run_fuzz(
        self,
        scan_id: str,
        run_id: str,
        manifest: CyberGymTargetManifest,
        seeds: list[dict[str, Any]],
        dictionary: list[str],
        seconds: int,
        max_length: int | None,
    ) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="cybergym-fuzz-") as temporary:
                scratch = Path(temporary)
                corpus = scratch / "corpus"
                findings = scratch / "findings"
                corpus.mkdir(mode=0o700)
                findings.mkdir(mode=0o700)
                for index, seed in enumerate(seeds):
                    (corpus / f"seed-{index}").write_bytes(seed["data"])
                dictionary_path: Path | None = None
                if dictionary:
                    dictionary_path = scratch / "dictionary"
                    dictionary_contents = "\n".join(
                        '"' + item.replace('"', '\\"') + '"'
                        for item in dictionary
                    )
                    dictionary_path.write_text(dictionary_contents + "\n", encoding="utf-8")
                mount_root = str(PurePosixPath(manifest.input_path).parent)
                container_corpus = f"{mount_root}/corpus"
                container_findings = f"{mount_root}/findings"
                command = self._container_command(manifest, scratch)
                command.extend([
                    manifest.fuzzer_target or "",
                    container_corpus,
                    f"-artifact_prefix={container_findings}/",
                    f"-max_total_time={seconds}",
                ])
                if max_length is not None:
                    command.append(f"-max_len={max_length}")
                if dictionary_path is not None:
                    command.append(f"-dict={mount_root}/dictionary")
                result = await self.executor.run(command, timeout_seconds=seconds + 15)
                produced = self._persist_fuzz_outputs(scan_id, run_id, corpus, findings, seed_count=len(seeds))
            payload = {
                "status": _execution_status(result),
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "artifacts": produced,
            }
            self.store.finish_cybergym_run(run_id, "completed", payload)
        except BaseException as exc:
            self.store.finish_cybergym_run(
                run_id,
                "failed",
                {"status": "runtime_error", "error": type(exc).__name__, "detail": str(exc)[:1_000]},
            )

    async def _execute_minimize(self, manifest: CyberGymTargetManifest, raw: bytes) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="cybergym-minimize-") as temporary:
            scratch = Path(temporary)
            crash = scratch / "crash"
            minimized = scratch / "minimized"
            crash.write_bytes(raw)
            mount_root = str(PurePosixPath(manifest.input_path).parent)
            command = self._container_command(manifest, scratch)
            command.extend([
                manifest.fuzzer_target or "",
                f"-minimize_crash=1",
                f"-exact_artifact_path={mount_root}/minimized",
                f"{mount_root}/crash",
            ])
            result = await self.executor.run(command, timeout_seconds=manifest.limits.fuzz_seconds)
            minimized_data = minimized.read_bytes() if minimized.is_file() else None
        return {
            "status": _execution_status(result),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "minimized_data": minimized_data,
        }

    def _persist_fuzz_outputs(
        self,
        scan_id: str,
        run_id: str,
        corpus: Path,
        findings: Path,
        *,
        seed_count: int,
    ) -> list[dict[str, Any]]:
        persisted: list[dict[str, Any]] = []
        for directory, kind in ((findings, "crash"), (corpus, "corpus")):
            for path in sorted(directory.iterdir(), key=lambda item: item.name)[:256]:
                if not path.is_file() or path.is_symlink() or (kind == "corpus" and path.name.startswith("seed-")):
                    continue
                raw = path.read_bytes()
                artifact = self.artifact_create(
                    scan_id,
                    kind=kind,
                    raw=raw,
                    provenance={"run_id": run_id, "operation": "libfuzzer", "source_name": path.name},
                )
                persisted.append(_public_artifact(artifact))
        return persisted

    def _container_command(self, manifest: CyberGymTargetManifest, scratch: Path, *, gdb: bool = False) -> list[str]:
        mount_root = str(PurePosixPath(manifest.input_path).parent)
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "1024m", "--cpus", "1.0",
            "--mount", f"type=bind,src={scratch.resolve()},dst={mount_root},rw",
        ]
        if gdb:
            command.extend(["--cap-add", "SYS_PTRACE", "--security-opt", "seccomp=unconfined"])
        command.append(manifest.vulnerable_runner)
        return command

    @staticmethod
    def _argv(manifest: CyberGymTargetManifest) -> list[str]:
        return [item.replace("{input}", manifest.input_path) for item in manifest.argv_template]


def _validate_gdb_intent(intent: Any) -> tuple[list[dict[str, str]], list[str]]:
    if not isinstance(intent, dict) or set(intent) - {"breakpoints", "variables"}:
        raise ValueError("GDB intent only accepts breakpoints and variables")
    raw_breakpoints = intent.get("breakpoints", [])
    raw_variables = intent.get("variables", [])
    if not isinstance(raw_breakpoints, list) or not 1 <= len(raw_breakpoints) <= 8:
        raise ValueError("GDB intent requires one to eight structured breakpoints")
    breakpoints: list[dict[str, str]] = []
    kinds: set[str] = set()
    for item in raw_breakpoints:
        if not isinstance(item, dict) or set(item) != {"kind", "location"}:
            raise ValueError("Each GDB breakpoint requires only kind and location")
        kind, location = item.get("kind"), item.get("location")
        if kind not in {"target", "vulnerable_branch", "observation"} or not isinstance(location, str) or not _BREAKPOINT_RE.fullmatch(location):
            raise ValueError("GDB breakpoint is not an allowed structured location")
        if kind in kinds:
            raise ValueError("GDB breakpoint kinds must be unique")
        kinds.add(kind)
        breakpoints.append({"kind": kind, "location": location})
    if not isinstance(raw_variables, list) or len(raw_variables) > 8 or not all(isinstance(item, str) and _IDENTIFIER_RE.fullmatch(item) for item in raw_variables):
        raise ValueError("GDB variables must be at most eight plain identifiers")
    return breakpoints, list(raw_variables)


def _execution_status(result: CommandResult) -> str:
    if result.unavailable:
        return "runtime_unavailable"
    if result.timed_out:
        return "timeout"
    if result.returncode == 0:
        return "clean"
    if result.returncode in {125, 126, 127, None}:
        return "harness_error"
    return "crash"


def _public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key != "data"}
