"""
Startup readiness: does this process have the extensions it was told to have?

The plugin loader reports what went wrong with extensions it *found*.  It has
nothing to say about one that was never installed -- a missing package produces
no entry point, so no failure, so a silent start with the extension's behaviour
simply absent.  For a deployment that depends on an extension for access
control or data handling, starting without it is worse than not starting.

This module closes that gap.  A deployment declares what must be present; the
check runs after plugins load and before the process serves traffic, and an
unmet requirement stops startup rather than degrading quietly.

Declaring requirements
----------------------
Requirements come from outside the code, so a build serves every deployment:

- ``FLOCKS_REQUIRED_PLUGINS`` -- comma-separated entry-point names
- ``FLOCKS_REQUIRED_CAPABILITIES`` -- comma-separated capability names
- ``FLOCKS_REQUIREMENTS_FILE`` -- JSON file with ``plugins`` / ``capabilities``
  lists, which is how a packaged delivery ships its own list

A capability is a generic slot such as ``auth_backend``: requiring it means
"something other than the built-in default must be registered here", without
naming which implementation fills it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from flocks.utils.log import Log

log = Log.create(service="plugin.readiness")

ENV_REQUIRED_PLUGINS = "FLOCKS_REQUIRED_PLUGINS"
ENV_REQUIRED_CAPABILITIES = "FLOCKS_REQUIRED_CAPABILITIES"
ENV_REQUIREMENTS_FILE = "FLOCKS_REQUIREMENTS_FILE"


# ---------------------------------------------------------------------------
# Capability probes
# ---------------------------------------------------------------------------

def _auth_backend_replaced() -> bool:
    from flocks.auth import get_backend
    from flocks.auth.service import LocalAuthBackend

    return get_backend() is not LocalAuthBackend


def _audit_sink_replaced() -> bool:
    from flocks.audit import NullAuditSink, get_sink

    return get_sink() is not NullAuditSink


def _license_checker_replaced() -> bool:
    from flocks.license import AlwaysOkLicenseChecker, get_checker

    return get_checker() is not AlwaysOkLicenseChecker


def _content_source_added() -> bool:
    from flocks.content import registered_sources

    return any(name != "local" for name in registered_sources())


def _response_transform_registered() -> bool:
    from flocks.transform import response_transform_active

    return response_transform_active()


def _stage_handled(stage: str) -> bool:
    from flocks.hooks.pipeline import HookPipeline

    return bool(HookPipeline.registered_stage_handlers(stage))


def _llm_request_hook_registered() -> bool:
    from flocks.hooks.pipeline import HookStage

    return _stage_handled(HookStage.LLM_BEFORE)


def _tool_gate_hook_registered() -> bool:
    from flocks.hooks.pipeline import HookStage

    return _stage_handled(HookStage.TOOL_BEFORE)


#: Capability name -> probe answering "is a non-default implementation installed?"
CAPABILITY_PROBES: Dict[str, Callable[[], bool]] = {
    "auth_backend": _auth_backend_replaced,
    "audit_sink": _audit_sink_replaced,
    "license_checker": _license_checker_replaced,
    "content_source": _content_source_added,
    "response_transform": _response_transform_registered,
    # Something processes every outgoing model request (``llm.before``) /
    # decides on every tool call before it runs (``tool.execute.before``).
    "llm_request_hook": _llm_request_hook_registered,
    "tool_gate_hook": _tool_gate_hook_registered,
}


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------

def _split_names(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


@dataclass(frozen=True)
class StartupRequirements:
    """What this deployment declared it must have."""

    plugins: FrozenSet[str] = frozenset()
    capabilities: FrozenSet[str] = frozenset()

    @property
    def empty(self) -> bool:
        return not self.plugins and not self.capabilities

    @classmethod
    def from_environment(cls, env: Optional[Dict[str, str]] = None) -> "StartupRequirements":
        env = env if env is not None else dict(os.environ)

        plugins = set(_split_names(env.get(ENV_REQUIRED_PLUGINS)))
        capabilities = set(_split_names(env.get(ENV_REQUIRED_CAPABILITIES)))

        manifest_path = env.get(ENV_REQUIREMENTS_FILE)
        if manifest_path:
            plugins_from_file, caps_from_file = cls._read_manifest(Path(manifest_path))
            plugins.update(plugins_from_file)
            capabilities.update(caps_from_file)

        plugins_declared, caps_declared = cls._read_descriptor_requirements(env)
        plugins.update(plugins_declared)
        capabilities.update(caps_declared)

        return cls(plugins=frozenset(plugins), capabilities=frozenset(capabilities))

    @staticmethod
    def _read_descriptor_requirements(env: Dict[str, str]) -> tuple[List[str], List[str]]:
        """Requirements the deployment descriptor declares.

        A supervisor that starts the runtime writes ``requirements`` into
        ``<FLOCKS_ROOT>/config/deployment.json`` as well as into the
        environment of the process it starts.  A process started some other way
        -- by hand, from a shell on the same host -- reads the same list from
        the descriptor, so it cannot come up without the extensions the
        deployment depends on just because it was not given the variable.

        No descriptor, or one without ``requirements``, declares nothing.  One
        that cannot be read or parsed raises, like a requirements manifest.
        """

        root = (env.get("FLOCKS_ROOT") or "").strip()
        base = Path(root).expanduser() if root else Path.home() / ".flocks"
        path = base / "config" / "deployment.json"
        if not path.exists():
            return [], []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"部署描述文件无法解析: {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"部署描述文件格式错误: {path}")
        requirements = raw.get("requirements")
        if requirements is None:
            return [], []
        if not isinstance(requirements, dict):
            raise RuntimeError(f"部署描述文件的 requirements 必须是对象: {path}")
        return (
            StartupRequirements._string_list(requirements.get("plugins"), "requirements.plugins", path),
            StartupRequirements._string_list(requirements.get("capabilities"), "requirements.capabilities", path),
        )

    @staticmethod
    def _string_list(value: Any, key: str, path: Path) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise RuntimeError(f"必需扩展清单字段 {key} 必须是字符串列表: {path}")
        return [item.strip() for item in value if item.strip()]

    @staticmethod
    def _read_manifest(path: Path) -> tuple[List[str], List[str]]:
        """Read a requirements manifest.

        An unreadable or malformed manifest is itself a failure to declare
        requirements, so it raises rather than resolving to "nothing required" --
        which would turn a typo in a path into a silent loss of enforcement.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError(f"必需扩展清单不存在: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"必需扩展清单无法解析: {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise RuntimeError(f"必需扩展清单格式错误: {path}")

        def _as_list(value: Any, key: str) -> List[str]:
            if value is None:
                return []
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise RuntimeError(f"必需扩展清单字段 {key} 必须是字符串列表: {path}")
            return [item.strip() for item in value if item.strip()]

        return _as_list(raw.get("plugins"), "plugins"), _as_list(raw.get("capabilities"), "capabilities")


@dataclass(frozen=True)
class ReadinessReport:
    """The outcome of one readiness check."""

    requirements: StartupRequirements
    installed_plugins: FrozenSet[str] = frozenset()
    missing_plugins: FrozenSet[str] = frozenset()
    missing_capabilities: FrozenSet[str] = frozenset()
    unknown_capabilities: FrozenSet[str] = frozenset()
    failed_entrypoints: tuple[str, ...] = ()
    errors: tuple[str, ...] = field(default=())
    failed_probes: tuple[str, ...] = ()
    # Per-probe outcome, for the authenticated detail view. Mutable on a frozen
    # dataclass by design: it is filled in after the probes run, and carries no
    # part of the readiness decision (``failed_probes`` does).
    probe_details: Dict[str, str] = field(default_factory=dict)

    @property
    def failed_required_entrypoints(self) -> tuple[str, ...]:
        """Load failures of entry points the deployment declared mandatory.

        An entry point is reported as ``"<name>: <error>"``, so the name is
        matched against the part before the colon.
        """
        if not self.requirements.plugins:
            return ()
        required = self.requirements.plugins
        return tuple(
            item for item in self.failed_entrypoints
            if item.split(":", 1)[0].strip() in required
        )

    @property
    def requirements_met(self) -> bool:
        """Whether every *declared* requirement is present.

        A mandatory plugin that is installed but failed to register counts as
        unmet: the deployment asked for the behaviour, not for the package to
        be on disk. A non-mandatory plugin that failed is left to the loader's
        own signal, which stops ingress rather than the process.
        """
        return not (
            self.missing_plugins
            or self.missing_capabilities
            or self.unknown_capabilities
            or self.errors
            or self.failed_required_entrypoints
        )

    @property
    def satisfied(self) -> bool:
        """Whether this instance should take traffic.

        Stricter than :attr:`requirements_met`: an instance whose plugins
        failed to load, or whose registered probes report a dependency it
        cannot reach, is running but not in a state a load balancer should
        route to.
        """
        return self.requirements_met and not self.failed_entrypoints and not self.failed_probes

    def describe(self) -> str:
        """One-line summary naming exactly what is unmet."""
        if self.satisfied:
            return "startup requirements satisfied"
        parts: List[str] = []
        if self.missing_plugins:
            parts.append(f"未安装的必需插件: {', '.join(sorted(self.missing_plugins))}")
        if self.missing_capabilities:
            parts.append(f"未注册的必需能力: {', '.join(sorted(self.missing_capabilities))}")
        if self.unknown_capabilities:
            parts.append(f"无法识别的能力名: {', '.join(sorted(self.unknown_capabilities))}")
        if self.failed_entrypoints:
            parts.append(f"加载失败的入口: {', '.join(self.failed_entrypoints)}")
        if self.failed_probes:
            parts.append(f"就绪探针失败: {', '.join(self.failed_probes)}")
        if self.errors:
            parts.append("; ".join(self.errors))
        return " | ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "requirements_met": self.requirements_met,
            "required_plugins": sorted(self.requirements.plugins),
            "required_capabilities": sorted(self.requirements.capabilities),
            "installed_plugins": sorted(self.installed_plugins),
            "missing_plugins": sorted(self.missing_plugins),
            "missing_capabilities": sorted(self.missing_capabilities),
            "unknown_capabilities": sorted(self.unknown_capabilities),
            "failed_entrypoints": list(self.failed_entrypoints),
            "failed_probes": list(self.failed_probes),
            "probe_details": dict(self.probe_details),
            "errors": list(self.errors),
        }


_entrypoint_cache: Optional[FrozenSet[str]] = None


def installed_plugin_entrypoints(*, refresh: bool = False) -> FrozenSet[str]:
    """Names published in the plugin entry-point groups of this environment.

    Cached after the first scan.  The set cannot change without installing a
    package, which cannot happen inside a running process, and the readiness
    endpoint is polled by a load balancer -- rescanning every installed
    distribution on each probe would be pure waste.
    """

    global _entrypoint_cache
    if _entrypoint_cache is not None and not refresh:
        return _entrypoint_cache

    import importlib.metadata

    from flocks.plugin.loader import (
        _CRITICAL_PLUGIN_ENTRYPOINT_GROUP,
        _PLUGIN_ENTRYPOINT_GROUP,
    )

    names: set[str] = set()
    try:
        entry_points = importlib.metadata.entry_points()
    except Exception as exc:
        log.error("plugin.readiness.entrypoint_scan_failed", {"error": str(exc)})
        # Not cached: a scan that failed should be retried, not remembered.
        return frozenset()

    for group in (_PLUGIN_ENTRYPOINT_GROUP, _CRITICAL_PLUGIN_ENTRYPOINT_GROUP):
        try:
            names.update(ep.name for ep in entry_points.select(group=group))
        except Exception as exc:
            log.error(
                "plugin.readiness.group_scan_failed",
                {"group": group, "error": str(exc)},
            )
    _entrypoint_cache = frozenset(names)
    return _entrypoint_cache


def reset_entrypoint_cache() -> None:
    """Drop the cached scan.  For tests and for an explicit plugin reload."""

    global _entrypoint_cache
    _entrypoint_cache = None


def check_startup_readiness(
    *,
    requirements: Optional[StartupRequirements] = None,
    failed_entrypoints: tuple[str, ...] = (),
    env: Optional[Dict[str, str]] = None,
    quiet: bool = False,
) -> ReadinessReport:
    """Check declared requirements against what actually loaded."""

    errors: List[str] = []
    if requirements is None:
        try:
            requirements = StartupRequirements.from_environment(env)
        except RuntimeError as exc:
            # A declaration we cannot read is reported as unmet, never as "no
            # requirements".
            return ReadinessReport(
                requirements=StartupRequirements(),
                failed_entrypoints=failed_entrypoints,
                errors=(str(exc),),
            )

    installed = installed_plugin_entrypoints()
    missing_plugins = frozenset(requirements.plugins - installed)

    missing_capabilities: set[str] = set()
    unknown_capabilities: set[str] = set()
    for capability in requirements.capabilities:
        probe = CAPABILITY_PROBES.get(capability)
        if probe is None:
            unknown_capabilities.add(capability)
            continue
        try:
            if not probe():
                missing_capabilities.add(capability)
        except Exception as exc:
            errors.append(f"能力检查失败 {capability}: {exc}")

    report = ReadinessReport(
        requirements=requirements,
        installed_plugins=installed,
        missing_plugins=missing_plugins,
        missing_capabilities=frozenset(missing_capabilities),
        unknown_capabilities=frozenset(unknown_capabilities),
        failed_entrypoints=failed_entrypoints,
        errors=tuple(errors),
    )

    # A caller that polls (an extension's readiness endpoint) passes quiet=True
    # and logs state changes itself; a line per probe says nothing new.
    if not quiet:
        if report.satisfied:
            if not requirements.empty:
                log.info(
                    "plugin.readiness.satisfied",
                    {
                        "plugins": sorted(requirements.plugins),
                        "capabilities": sorted(requirements.capabilities),
                    },
                )
        else:
            log.error("plugin.readiness.unsatisfied", report.to_dict())
    return report


class StartupRequirementsUnmet(RuntimeError):
    """Startup requirements were declared and not met."""

    def __init__(self, report: ReadinessReport) -> None:
        self.report = report
        super().__init__(report.describe())


def enforce_startup_readiness(report: ReadinessReport) -> None:
    """Raise unless every declared requirement is met.

    Deliberately has no "continue anyway" path: a deployment that declared an
    extension mandatory gets a process that either has it or does not start.
    """

    if not report.requirements_met:
        raise StartupRequirementsUnmet(report)


__all__ = [
    "CAPABILITY_PROBES",
    "ENV_REQUIRED_CAPABILITIES",
    "ENV_REQUIRED_PLUGINS",
    "ENV_REQUIREMENTS_FILE",
    "ReadinessReport",
    "StartupRequirements",
    "StartupRequirementsUnmet",
    "check_startup_readiness",
    "enforce_startup_readiness",
    "installed_plugin_entrypoints",
    "reset_entrypoint_cache",
]
