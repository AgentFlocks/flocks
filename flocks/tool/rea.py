"""Rex's entry point to the REA reverse-analysis plugin.

``install`` carries out the 「REA 安装指引」: it returns the tier plan for the
user to choose and confirm, then runs the install job (missing components in
parallel, the single MCP registration, the tier validation with real tool
calls) and reports the evidence. ``status`` gives the unified plugin state
with what to do next, and ``report`` shows an analysis result as a card.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any, Optional

from flocks.rea import attachment_guard, components, runtime, service, validation
from flocks.rea.installer import ReaInstaller, ReaInstallError, ReaInstallJob
from flocks.rea.uninstaller import ReaUninstaller, sessions_using_rea, stop_tasks
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log


log = Log.create(service="tool.rea")

REA_TOOL_NAME = "rea_analysis"
MAX_REPORT_ENTRIES = 12
MAX_REPORT_ITEMS = 12
MAX_REPORT_LABEL_CHARS = 40
MAX_REPORT_VALUE_CHARS = 2_000
MAX_REPORT_SUMMARY_CHARS = 1_000

REPORT_ENTRY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Short heading, e.g. 关键原理 / 证据 / 验证方法与结果"},
        "value": {"type": "string", "description": "Finding in plain language, tied to file, function, address or source"},
    },
    "required": ["label", "value"],
    "additionalProperties": False,
}

REA_TOOL_DESCRIPTION = """REA 逆向分析 plugin (Reverse Engineer Anything, rea-agents 6.4.0): installs, checks and repairs REA in Flocks and shows analysis results.

Use it for an actual reverse-engineering job on a concrete target (a binary, malware sample, APK/IPA, firmware, JavaScript/Electron or .NET app, archive, download link or website URL) or when the user asks to install, check or repair REA. Do not call it for terminology questions or general talk about reverse engineering or tools such as Ghidra/IDA.

This tool carries out the 「REA 安装指引」. When a message asks to install, check, repair or troubleshoot REA (including the full 「REA 安装指引」 text), call action="install" right away: do not call status first and never run the guide's commands (npx, dnf, curl, unzip, editing flocks.json) yourself. The tool reuses what exists and installs only the missing components of the chosen tier, in parallel.

- action="install" (administrators): the first call returns the plan of both tiers and the user sees a card to choose 轻量档 (JavaScript / .NET / ZIP; no JDK, no Ghidra) or 完整档 (adds JDK 21 and Ghidra 12.1.4 for native ELF/PE decompilation) and to confirm system package installs and Flocks configuration writes. The card lists per tier what is installed, downloaded or reused and the registration: it is the confirmation the guide asks for before installing (sending the guide is not one). Ask the user in one short sentence to choose and confirm, then stop and wait; do not ask for files or analysis goals at this stage. If other conversations are using REA, install first lists them (phase "tasks"): ask whether to stop them and continue, and call install again only after the user chose that on the card. When the user has confirmed a tier on the card, call install with that tier and confirmed=true: the tool accepts only when the user's latest message is the card's confirmation of that tier, so answer questions or other replies and leave the plan open. It then waits until the components, the single MCP registration "rea" (npx -y rea-agents@6.4.0 --mcp) and the tier validation are done: real REA tool calls on generated JavaScript, .NET and ZIP samples, and for the full tier a small C program through open_binary and procedure_pseudo_code. Report briefly: tier, reused and new components, the registration, which capabilities passed (static analysis and runtime check separately), failed or were not verified. On a failure or a blocker (root rights, a read-only directory) give the cause and the exact action for the user, fix what you can, and call install again (everything in place is reused).
- action="status": the unified plugin state and what to do next; call it before a reverse-engineering task when you have not installed REA in this conversation.
- action="report": shows the analysis result as a card: summary (answer to the user's question), entries (key principle, evidence tied to file/function/address/source, verification method and result), facts (verified), inferences, open_items. Also save the full report with the write tool in the outputs directory.

After the install passed, ask for the file, download link or website URL to analyze and what the user wants to find out, unless they already gave them. 「帮我使用 REA 逆向工具分析上传的附件文件」 (the REA shortcut's prompt) asks to analyze the files attached to this conversation: when they are there, start with them and ask about the focus only if it is unclear; when none is attached, ask the user to attach them with the + button and stop. Only files the user attached to this conversation or named explicitly are targets: never look for them on the host, in other conversations' uploads or in the Flocks data directory. For the analysis use the REA MCP tools: they are named rea_<tool> and are deferred, so load the ones you need with tool_search (select:rea_inspect_artifact,...) before calling them; the skill reverse-engineer-anything has the workflow when it is enabled. Check and add target-specific tools only when the target needs them, and ask before installing system packages or large tools. Run an untrusted sample only with the user's consent, preferably in an isolated sandbox, never casually on the Flocks host. Keep verified facts, inferences and unfinished items apart; a passed install is not proof that every analysis capability works. Talk to the user in plain words: tool names, actions and parameters (action="install", tier="full", confirmed=true) are for you, never show them to the user."""

_CAPABILITY_LABELS = {
    "javascript": "JavaScript",
    "dotnet": ".NET",
    "archive": "ZIP archive",
    "native": "native decompilation",
}
_COMPONENT_LABELS = {
    "skill": f"skill {runtime.REA_SKILL_NAME}",
    "rea": f"{runtime.REA_PACKAGE_SPEC} (npx cache)",
    "jdk": f"JDK {runtime.JDK_MAJOR}",
    "ghidra": f"Ghidra {runtime.GHIDRA_VERSION}",
}
_ERROR_HINTS = {
    "environment": "A host prerequisite is missing (Node.js version, npx, writable Flocks data directory).",
    "blocked": "The install needs something only the user can do; see the blockers.",
    "component_failed": "A component could not be installed (network problems are retried as is).",
    "service_failed": "Flocks could not start the REA MCP service or it listed no tools.",
    "validation_failed": "The tier validation did not pass; see the per-capability results.",
    "install_failed": "The install job failed unexpectedly.",
    "interrupted": "The install was interrupted (service restart or stopped by an uninstall).",
    "uninstalling": "REA is being uninstalled; install again when the uninstall finished.",
    "tasks_unreadable": "A busy conversation could not be read, so it can be neither confirmed nor stopped from here.",
}


_PATHS_NOTE = (
    "This conversation shows codes and fixed texts only: host paths appear as <path>, error output and "
    "validation notes that quote other programs are left out. Administrators see the full diagnostics in the "
    "plugin square (插件广场 → REA 逆向分析 → 查看详情)."
)
_ENVIRONMENT_FAILURES = {
    ("storage", "not_writable"): "the Flocks service cannot write its data directory",
    ("node", "missing"): f"Node.js is missing (REA needs {runtime.NODE_REQUIREMENT})",
    ("node", "unreadable"): "the Node.js version could not be read",
    ("node", "unsupported"): f"the Node.js version is not supported (REA needs {runtime.NODE_REQUIREMENT})",
    ("npx", "missing"): "npx is missing",
}
# Values another program reports that the conversation keeps only in these shapes.
_VERSION = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")
_FORMAT = re.compile(r"^[0-9A-Za-z_.-]{1,32}$")


def _for_conversation(result: ToolResult) -> ToolResult:
    """Backstop over the public forms below: what the conversation keeps names no host paths."""

    return result.model_copy(update={
        "output": runtime.redact_paths(result.output),
        "error": runtime.redact_paths(result.error),
        "metadata": runtime.redact_paths(result.metadata),
    })


def _clean_text(value: object, limit: int) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _normalize_entries(entries: object) -> list[dict[str, str]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("entries must be a non-empty list of {label, value}")
    if len(entries) > MAX_REPORT_ENTRIES:
        raise ValueError(f"entries accepts at most {MAX_REPORT_ENTRIES} items")
    normalized = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("each entry must be an object with label and value")
        label = _clean_text(item.get("label"), MAX_REPORT_LABEL_CHARS)
        value = _clean_text(item.get("value"), MAX_REPORT_VALUE_CHARS)
        if not label or not value:
            raise ValueError("each entry needs a non-empty label and value")
        normalized.append({"label": label, "value": value})
    return normalized


def _normalize_items(items: object, name: str) -> list[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError(f"{name} must be a list of strings")
    if len(items) > MAX_REPORT_ITEMS:
        raise ValueError(f"{name} accepts at most {MAX_REPORT_ITEMS} items")
    return [text for text in (_clean_text(item, MAX_REPORT_VALUE_CHARS) for item in items) if text]


def _current_user() -> Any:
    from flocks.auth.context import get_current_auth_user

    return get_current_auth_user()


def _plan_mode(ctx: ToolContext) -> bool:
    from flocks.session.execution_mode import SessionExecutionMode, runtime_execution_mode

    return runtime_execution_mode(ctx.extra.get("execution_mode")) == SessionExecutionMode.PLAN


# ── evidence for messages and cards ──


def _shaped(value: Any, pattern: re.Pattern[str]) -> Optional[str]:
    return value if isinstance(value, str) and pattern.match(value) else None


def _public_capabilities(capabilities: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-capability evidence a conversation keeps: verdicts, our own samples and allowlisted notes."""

    public = {}
    for key, item in (capabilities or {}).items():
        if not isinstance(item, dict):
            continue
        sample = item.get("sample") if isinstance(item.get("sample"), dict) else {}
        notes, _hidden = validation.public_notes(item.get("notes"))
        calls = [call for call in item.get("calls") or [] if isinstance(call, dict)]
        public[key] = {
            "status": item.get("status"),
            "static": item.get("static"),
            "runtime": item.get("runtime"),
            "expected": item.get("expected"),
            "actual": validation.public_actual(item.get("actual")),
            "tools": [call.get("tool") for call in calls],
            # What each call returned stays in the install record (plugin square).
            "calls": [{"tool": call.get("tool"), "ok": call.get("ok")} for call in calls],
            "sample": {
                "name": re.split(r"[\\/]", str(sample.get("path") or ""))[-1] or None,
                "sha256": sample.get("sha256"),
                "format": _shaped(sample.get("format"), _FORMAT),
            },
            "notes": notes,
            "hiddenDetails": validation.hidden_details(item),
        }
    return public


def _public_components(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Status and checked evidence; the detail (paths, installer output) stays in the plugin square."""

    return {
        key: {
            "status": item.get("status"),
            "attempts": item.get("attempts"),
            "blocker": components.public_blocker(item["blocker"]) if isinstance(item.get("blocker"), dict) else None,
            "evidence": item.get("evidence"),
        }
        for key, item in (results or {}).items() if isinstance(item, dict)
    }


def _public_service(service_report: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(service_report, dict):
        return None
    return {
        "ok": service_report.get("ok"),
        "serverVersion": _shaped(service_report.get("server_version"), _VERSION),
        "toolsObserved": service_report.get("tools_observed"),
        "toolsExpected": service_report.get("tools_expected"),
        "requestTool": service_report.get("request_tool"),
        "requestOk": service_report.get("request_ok"),
    }


def _environment_failure(job: ReaInstallJob) -> Optional[dict[str, str]]:
    """The failed prerequisite as codes (the job's detail is ``<check>:<code>[:<host detail>]``)."""

    if job.errorCode != "environment" or not job.errorDetail:
        return None
    check, _, rest = job.errorDetail.partition(":")
    code = rest.partition(":")[0]
    return {"check": check, "code": code} if (check, code) in _ENVIRONMENT_FAILURES else None


def _unverified(tier: str, capabilities: dict[str, Any]) -> list[str]:
    """What the tier did not verify: native decompilation for the light tier, unfinished runtime checks."""

    items = []
    if tier != "full":
        items.append("native")
    for key, item in (capabilities or {}).items():
        if isinstance(item, dict) and item.get("runtime") == "incomplete":
            items.append(f"{key}:runtime")
    return items


def _capability_lines(capabilities: dict[str, Any], *, detailed: bool = True) -> list[str]:
    """One line per capability; ``detailed`` adds the evidence (administrators only)."""

    lines = []
    for key, item in capabilities.items():
        label = _CAPABILITY_LABELS.get(key, key)
        if not detailed:
            lines.append(
                f"- {label}: {item.get('status')} (static analysis {item.get('static')}, "
                f"runtime check {item.get('runtime')})"
            )
            continue
        tools = ", ".join(item.get("tools") or []) or "no tool call"
        actual = f", actual {item['actual']}" if item.get("actual") else ""
        lines.append(
            f"- {label}: {item.get('status')} (static analysis {item.get('static')}, runtime check "
            f"{item.get('runtime')}; expected {item.get('expected') or '-'}{actual}; tools: {tools})"
        )
        for note in item.get("notes") or []:
            if note:
                lines.append(f"  · {note}")
        if item.get("hiddenDetails"):
            lines.append(f"  · {item['hiddenDetails']} more detail(s) only in the plugin square")
    return lines


# ── install plan and confirmation ──


async def _tier_plan(tier: str) -> dict[str, Any]:
    plan = await components.plan_components(tier)
    expected = runtime.mcp_server_config(
        tier, java_home=await runtime.find_java_home() if tier == "full" else None,
        ghidra_dir=runtime.find_ghidra_dir() if tier == "full" else None,
    )
    current = runtime.current_mcp_config()
    if runtime.mcp_config_matches(current, expected):
        registration = "reuse"
    else:
        registration = "replace" if current is not None else "write"
    writes = [
        key for key, item in plan.items()
        if item["action"] == "install" or (key == "ghidra" and item["action"] == "download")
    ]
    if registration != "reuse":
        writes.append("mcp")
    blockers = [item["blocker"] for item in plan.values() if item.get("blocker")]
    return {
        "tier": tier,
        "components": plan,
        "registration": registration,
        "writes": writes,
        "blockers": blockers,
        "needsConfirmation": bool(writes),
    }


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """The plan as the conversation keeps it: blockers and the Ghidra directory in public form."""

    public_components = {}
    for key, item in plan["components"].items():
        public = {"action": item["action"]}
        if isinstance(item.get("blocker"), dict):
            public["blocker"] = components.public_blocker(item["blocker"])
        if item.get("command"):
            command = components.jdk_install_command()
            public["command"] = command if item["command"] == command else runtime.PATH_PLACEHOLDER
        if item.get("path"):
            public["path"] = item["path"] if item["path"] in runtime.GUIDE_PATHS else runtime.PATH_PLACEHOLDER
        public_components[key] = public
    return {
        **plan,
        "components": public_components,
        "blockers": [components.public_blocker(blocker) for blocker in plan["blockers"]],
    }


def _plan_lines(plan: dict[str, Any]) -> list[str]:
    name = "Light tier (轻量档)" if plan["tier"] == "light" else "Full tier (完整档)"
    parts = []
    for key, item in plan["components"].items():
        label = _COMPONENT_LABELS.get(key, key)
        action = item["action"]
        if action == "blocked":
            parts.append(f"{label}: BLOCKED ({components.blocker_text(item['blocker'])})")
        elif key == "jdk" and action == "install":
            parts.append(f"{label}: install the system package ({item.get('command')})")
        elif key == "ghidra" and action == "download":
            parts.append(f"{label}: download via the mirror, check the official SHA-256, unpack to {item.get('path')}")
        else:
            parts.append(f"{label}: {action}")
    registration = {
        "reuse": "reuse the identical registration",
        "write": "register once in the Flocks configuration",
        "replace": "replace the existing different registration in the Flocks configuration",
    }[plan["registration"]]
    parts.append(f"MCP service \"{runtime.MCP_SERVER_NAME}\": {registration}")
    return [f"{name}: " + "; ".join(parts) + "."]


# What the plan card's buttons (and the failed card's retry) send, as in
# webui/src/locales/*/session.json ``rea.confirm``. Only this text counts as consent for its tier.
CONFIRM_PHRASES: dict[str, tuple[str, ...]] = {
    "light": (
        "选择轻量档：确认安装缺失组件并写入 Flocks 配置。",
        "Light tier: I confirm installing the missing components and writing the Flocks configuration.",
    ),
    "full": (
        "选择完整档：确认安装缺失组件（含 JDK 21 系统软件包和 Ghidra 12.1.4）并写入 Flocks 配置。",
        "Full tier: I confirm installing the missing components (including the JDK 21 system package and "
        "Ghidra 12.1.4) and writing the Flocks configuration.",
    ),
}
# What the card listing the conversations that use REA sends (``rea.stop``): consent to stop them and
# to the tier's install, in one message.
STOP_PHRASES: dict[str, tuple[str, ...]] = {
    "light": (
        "停止正在使用 REA 的会话，确认按轻量档安装缺失组件并写入 Flocks 配置。",
        "Stop the conversations using REA, and I confirm installing the missing components of the light tier "
        "and writing the Flocks configuration.",
    ),
    "full": (
        "停止正在使用 REA 的会话，确认按完整档安装缺失组件（含 JDK 21 系统软件包和 Ghidra 12.1.4）并写入 Flocks 配置。",
        "Stop the conversations using REA, and I confirm installing the missing components of the full tier "
        "(including the JDK 21 system package and Ghidra 12.1.4) and writing the Flocks configuration.",
    ),
}


def _sent_by_user(message: Any) -> bool:
    """Text the user typed or a file the user attached (Flocks' own synthetic text does not count)."""

    for part in getattr(message, "parts", None) or []:
        if getattr(part, "synthetic", False):
            continue
        kind = getattr(part, "type", None)
        if kind == "file" or (kind == "text" and (getattr(part, "text", None) or "").strip()):
            return True
    return False


def _typed_text(message: Any) -> str:
    """What the user typed in a message (synthetic text added by Flocks does not count)."""

    return "\n".join(
        (part.text or "").strip()
        for part in getattr(message, "parts", None) or []
        if getattr(part, "type", None) == "text" and not getattr(part, "synthetic", False) and (part.text or "").strip()
    )


async def _latest_reply(ctx: ToolContext, *, after: tuple[str, ...]) -> Optional[tuple[str, dict[str, Any]]]:
    """The user's latest message and the card it answers: the last card of the ``after`` phases.

    None when there is no such card or the user's latest message (text or a file) is older.
    """

    from flocks.session.message import Message

    try:
        messages = await Message.list_with_parts(ctx.session_id)
    except Exception as exc:
        log.warning("rea.install.confirm_lookup_failed", {"error": str(exc)})
        return None
    card: Optional[tuple[int, dict[str, Any]]] = None
    latest: Optional[tuple[int, str]] = None
    for index, message in enumerate(messages):
        role = getattr(message.info, "role", None)
        if role == "user":
            if _sent_by_user(message):
                latest = (index, _typed_text(message))
            continue
        if role != "assistant":
            continue
        for part in message.parts or []:
            if getattr(part, "type", None) != "tool" or getattr(part, "tool", None) != REA_TOOL_NAME:
                continue
            state = getattr(part, "state", None)
            meta = getattr(state, "metadata", None) or {}
            rea = meta.get("rea") if isinstance(meta, dict) else None
            if getattr(state, "status", None) == "completed" and isinstance(rea, dict) and rea.get("phase") in after:
                card = (index, rea)
    if card is None or latest is None or latest[0] < card[0]:
        return None
    return latest[1], card[1]


async def _user_confirmed(ctx: ToolContext, phrases: tuple[str, ...], *, after: tuple[str, ...]) -> bool:
    """The user's latest message answers the last card of the ``after`` phases with one of ``phrases``.

    A question, a "not yet", the other tier's confirmation or a confirmation the user wrote (or
    attached) something after is not consent.
    """

    reply = await _latest_reply(ctx, after=after)
    return reply is not None and reply[0] in phrases


# Opaque references to the replies a 「tasks」 card lists (a conversation and the message that started
# its running reply): what the user approved is checked against them, without keeping other
# conversations' ids in this one. A new reply there, or a restart here, changes them, so an old card
# then lists the conversations again.
_TASK_REF_SALT = secrets.token_hex(16)


def _task_ref(task: dict[str, Any]) -> str:
    seed = f"{_TASK_REF_SALT}:{task['id']}:{task.get('turn') or ''}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _plan_result(plans: dict[str, dict[str, Any]], *, tier: Optional[str], reason: str) -> ToolResult:
    plans = {name: _public_plan(plan) for name, plan in plans.items()}
    lines = [reason, *[line for plan in plans.values() for line in _plan_lines(plan)]]
    if any(plan["blockers"] for plan in plans.values()):
        lines.append(_PATHS_NOTE)
    lines.append(
        "The user now sees a card to choose the tier and confirm. Ask in one short sentence which tier to "
        "install (轻量档 for JavaScript / .NET / ZIP, 完整档 to decompile native ELF/PE) and to confirm, then stop. "
        "Do not ask for analysis material now. When the user confirmed a tier on the card, call "
        "action=\"install\" with that tier and confirmed=true; any other reply (a question, \"not yet\") is not "
        "consent: answer it and leave the plan open."
    )
    return ToolResult(
        success=True,
        output="\n".join(lines),
        title="REA 安装方案",
        metadata={"rea": {
            "action": "install",
            "phase": "plan",
            "tier": tier,
            "recordedTier": runtime.recorded_tier(),
            "plans": plans,
        }},
    )


def _task_summary(tasks: list[dict[str, Any]]) -> str:
    """How many, never which: their titles would end up in this conversation, which others may read."""

    unknown = sum(1 for task in tasks if task.get("unknown"))
    parts = []
    if len(tasks) > unknown:
        parts.append(f"{len(tasks) - unknown} other conversation(s)")
    if unknown:
        parts.append(f"{unknown} busy conversation(s) whose state could not be read")
    return " and ".join(parts)


def _public_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the conversation keeps of them: an opaque reference to the running reply, no title."""

    return [{"ref": _task_ref(task), "unknown": bool(task.get("unknown"))} for task in tasks]


def _tasks_result(tasks: list[dict[str, Any]], tier: str) -> ToolResult:
    lines = [
        f"Other conversations are using REA right now: {_task_summary(tasks)}. Do not guess which ones.",
        "Validating REA again would interrupt their analysis: the check opens its own samples in the REA service "
        "they share, and a new registration reconnects it. Nothing was changed yet.",
        "The user sees a card listing them, with a button to stop them and continue. Ask in one short sentence "
        "whether to stop them and continue, then stop and wait. When the user chose that on the card, call "
        "action=\"install\" with the same tier and confirmed=true; any other reply leaves them running.",
    ]
    return ToolResult(
        success=True,
        output="\n".join(lines),
        title="REA 正在被其他会话使用",
        metadata={"rea": {
            "action": "install",
            "phase": "tasks",
            "tier": tier,
            "tasks": _public_tasks(tasks),
        }},
    )


# ── install results ──


def _job_metadata(job: ReaInstallJob, *, phase: str) -> dict[str, Any]:
    capabilities = _public_capabilities(job.validation)
    return {
        "action": "install",
        "phase": phase,
        "ok": phase == "done",
        "tier": job.tier,
        "stage": job.stage,
        "errorCode": job.errorCode,
        "environment": _environment_failure(job),
        "blockers": [components.public_blocker(blocker) for blocker in job.blockers],
        "components": _public_components(job.components),
        "registration": job.registration,
        "service": _public_service(job.service),
        "capabilities": capabilities,
        "unverified": _unverified(job.tier, capabilities),
    }


def _component_lines(job: ReaInstallJob) -> list[str]:
    lines = []
    for key, item in job.components.items():
        label = _COMPONENT_LABELS.get(key, key)
        status = item.get("status")
        line = f"- {label}: {status}"
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        if evidence.get("verified"):
            line += f" (official SHA-256 {evidence.get('sha256')} verified, via {evidence.get('source')})"
        if item.get("attempts", 1) > 1:
            line += f", {item['attempts']} attempts"
        lines.append(line)
    return lines


def _service_line(job: ReaInstallJob) -> Optional[str]:
    report = _public_service(job.service)
    if report is None:
        return None
    return (
        f"- Service: REA MCP server {report['serverVersion'] or '?'} started, "
        f"{report['toolsObserved']}/{report['toolsExpected']} tools listed, real call {report['requestTool'] or '?'} "
        f"{'succeeded' if report['requestOk'] else 'failed'} (rea mcp doctor)."
    )


def _success_result(job: ReaInstallJob) -> ToolResult:
    meta = _job_metadata(job, phase="done")
    tier_name = "light tier (轻量档)" if job.tier == "light" else "full tier (完整档)"
    lines = [
        f"REA {runtime.REA_VERSION} is installed and the {tier_name} validation passed.",
        "Components:",
        *_component_lines(job),
        f"- MCP service \"{runtime.MCP_SERVER_NAME}\": {job.registration or '?'} (registered once, "
        f"command npx -y {runtime.REA_PACKAGE_SPEC} --mcp).",
    ]
    service_line = _service_line(job)
    if service_line:
        lines.append(service_line)
    lines += ["Validation with real tool calls:", *_capability_lines(meta["capabilities"])]
    if job.tier != "full":
        lines.append("- Native ELF/PE decompilation: not verified (light tier, no JDK/Ghidra).")
    lines.append(
        "Tell the user briefly what was reused or installed and what is verified or not (the card shows the "
        "evidence). Then ask for the target (file, download link or website URL) and the question, unless "
        "the user already gave them."
    )
    return ToolResult(success=True, output="\n".join(lines), title="REA 安装与验证完成", metadata={"rea": meta})


def _failure_result(job: ReaInstallJob) -> ToolResult:
    meta = _job_metadata(job, phase="failed")
    lines = [f"REA install did not pass: stage \"{job.stage}\", code {job.errorCode or 'unknown'}."]
    hint = _ERROR_HINTS.get(job.errorCode or "")
    if hint:
        lines.append(hint)
    if meta["environment"]:
        failure = meta["environment"]
        lines.append(f"Failed prerequisite: {_ENVIRONMENT_FAILURES[(failure['check'], failure['code'])]}.")
    if meta["blockers"]:
        lines.append("Blockers the user has to clear:")
        lines += [f"- {components.blocker_text(blocker)}" for blocker in meta["blockers"]]
    if job.components:
        lines += ["Components:", *_component_lines(job)]
    service_line = _service_line(job)
    if service_line:
        lines.append(service_line)
    if meta["capabilities"]:
        lines += ["Validation:", *_capability_lines(meta["capabilities"])]
    lines.append(
        "REA is not usable yet and no first install was recorded. Explain the cause and, for blockers, the exact "
        "action for the user. Fix what you can, then call action=\"install\" again in this conversation "
        "(installed parts are reused). Do not ask for analysis material until the validation passes. "
        + _PATHS_NOTE
    )
    return ToolResult(success=False, error="\n".join(lines), metadata={"rea": meta})


async def _install(ctx: ToolContext, tier: Optional[str], confirmed: bool) -> ToolResult:
    user = _current_user()
    if not service.can_install(user):
        return ToolResult(
            success=False,
            error="Only administrators can install the REA plugin. Tell the user to ask an administrator.",
            metadata={"rea": {"action": "install", "phase": "failed", "ok": False, "errorCode": "forbidden"}},
        )
    if _plan_mode(ctx):
        return ToolResult(
            success=False,
            error=(
                "Plan mode cannot install or change REA. Tell the user to switch this conversation to Build mode "
                "and send the request again."
            ),
            metadata={"rea": {"action": "install", "phase": "failed", "ok": False, "errorCode": "plan_mode"}},
        )
    if ReaUninstaller.is_running():
        return ToolResult(
            success=False,
            error=_ERROR_HINTS["uninstalling"],
            metadata={"rea": {"action": "install", "phase": "failed", "ok": False, "errorCode": "uninstalling"}},
        )
    if tier is not None and tier not in runtime.TIERS:
        return ToolResult(success=False, error=f"tier must be one of {', '.join(runtime.TIERS)}")
    if ctx.session_id:
        try:
            await runtime.run_in_thread(service.record_install_session, ctx.session_id, user)
        except OSError as exc:
            log.debug("rea.install_session.record_failed", {"error": str(exc)})

    if not ReaInstaller.is_running():
        last = ReaInstaller.current_job()
        # A retry keeps the tier of the last attempt unless Rex names another.
        tier = tier or runtime.recorded_tier() or (last.tier if last is not None else None)
        plans = {name: await _tier_plan(name) for name in runtime.TIERS}
        if tier is None:
            return _plan_result(plans, tier=None, reason="REA install plan (nothing was changed yet).")
        consent = CONFIRM_PHRASES[tier] + STOP_PHRASES[tier]
        if plans[tier]["needsConfirmation"] and not (
            confirmed and await _user_confirmed(ctx, consent, after=("plan", "tasks"))
        ):
            return _plan_result(
                plans, tier=tier,
                reason="The install writes system packages or Flocks configuration and needs the user's confirmation first.",
            )
        # Validating again (and a new registration) would interrupt other conversations using REA:
        # they are named first and stopped only after the user agreed on the card.
        busy = await sessions_using_rea(exclude=ctx.session_id)
        if any(task.get("unknown") for task in busy):
            # Nothing tells one run of such a conversation from the next, so no card could be
            # confirmed for it: wait until it is idle (or readable) again.
            return ToolResult(
                success=False,
                error=(
                    f"Busy conversations: {_task_summary(busy)}. {_ERROR_HINTS['tasks_unreadable']} REA was not "
                    "changed and nothing was stopped. Tell the user to retry the install when they finished."
                ),
                metadata={"rea": {
                    "action": "install", "phase": "failed", "ok": False, "errorCode": "tasks_unreadable",
                    "tier": tier, "tasks": _public_tasks(busy),
                }},
            )
        if busy:
            # Only the replies the card listed are stopped: another conversation, or a new reply in a
            # listed one, means asking again.
            reply = await _latest_reply(ctx, after=("tasks",)) if confirmed else None
            approved = {
                task.get("ref") for task in (reply[1].get("tasks") or []) if isinstance(task, dict)
            } if reply is not None and reply[0] in STOP_PHRASES[tier] else set()
            if not approved or not {_task_ref(task) for task in busy} <= approved:
                return _tasks_result(busy, tier)
            still = await stop_tasks(busy)
            if still:
                left = [task for task in busy if task["id"] in still]
                return ToolResult(
                    success=False,
                    error=(
                        f"{_task_summary(left)} did not stop. REA was not changed. "
                        "Tell the user to try again when they finished."
                    ),
                    metadata={"rea": {
                        "action": "install", "phase": "failed", "ok": False, "errorCode": "tasks_running",
                        "tier": tier, "tasks": _public_tasks(left),
                    }},
                )
    try:
        user_id = getattr(user, "id", None)
        await ReaInstaller.start(tier or "light", user_id=user_id, session_id=ctx.session_id)
    except ReaInstallError as exc:
        return ToolResult(
            success=False,
            error=_ERROR_HINTS.get(exc.code, exc.code),
            metadata={"rea": {"action": "install", "phase": "failed", "ok": False, "errorCode": exc.code}},
        )
    job = await ReaInstaller.wait()
    if job is None:
        return ToolResult(success=False, error="The REA install job disappeared; call install again.")
    if job.status == "succeeded":
        return _success_result(job)
    return _failure_result(job)


# ── status ──


def _attachments_line(attached: Optional[int]) -> list[str]:
    if attached is None:
        return []
    if attached == 0:
        return ["No file is attached to this conversation yet."]
    return [f"The user attached {attached} file(s) to this conversation."]


def _status_text(state: str, *, admin: bool, attached: Optional[int] = None) -> str:
    if state == "ready":
        record = runtime.read_install_record() or {}
        tier = record.get("tier")
        verification = record.get("verification") or {}
        lines = [
            f"REA ({runtime.REA_PACKAGE_SPEC}) is installed on this Flocks service, {tier} tier, and its "
            "validation passed. Its tools are the deferred rea_* tools (load them with tool_search "
            "select:rea_<tool>).",
            # Members get the verdicts only: the evidence can name the host's paths.
            *_capability_lines(_public_capabilities(verification.get("capabilities") or {}), detailed=admin),
        ]
        if tier != "full":
            lines.append(
                "Native ELF/PE decompilation is not verified (light tier). If the target needs it, tell the user in "
                "plain words that the 完整档 (adds JDK 21 and Ghidra) covers it and an administrator can install it; "
                "call install with the full tier only when an administrator asks for it."
            )
        lines += _attachments_line(attached)
        lines.append(
            "Continue the user's task with REA on the files attached to this conversation or the link, URL or path "
            "the user named. If there is nothing to analyze yet, ask the user to attach the file(s) with the + "
            "button or to give a link, then stop; ask what they want to find out only when that is unclear. Files "
            "are only those attached to this conversation or named by the user: do not search the host or other "
            "conversations."
        )
        return "\n".join(lines)
    if state in ("installing", "validating"):
        job = ReaInstaller.current_job()
        stage = job.stage if job else "?"
        return (
            f"REA is being installed right now (stage {stage}). Do not start another install: an administrator "
            "can call action=\"install\" to wait for this one. Tell the user it will be usable when it finished."
        )
    if state == "uninstalling":
        return "REA is being uninstalled. Do not start an analysis; tell the user to wait for the uninstall to finish."
    if state == "uninstall_failed":
        return (
            "The last REA uninstall did not finish and left parts behind, so REA is not usable. Tell the user that "
            "an administrator can retry the uninstall in the plugin square (插件广场). If an administrator asks to "
            "install REA again instead, call action=\"install\"."
        )
    if state == "degraded":
        reasons = "; ".join(service.degraded_reasons(details=False)) if admin else ""
        lines = [
            "REA passed its validation before, but its service or configuration is not usable now"
            + (f": {reasons}." if reasons else ".")
        ]
        if admin:
            lines.append(
                "Repair it first: call action=\"install\" (it reuses the existing configuration and validates "
                "again; it asks for confirmation only if configuration must be written). Then continue the task. "
                + _PATHS_NOTE
            )
        else:
            lines.append("Tell the user an administrator has to repair the REA plugin first, then stop.")
        return "\n".join(lines)
    lines = []
    if state == "install_failed":
        lines.append("The last REA install or its validation did not pass; REA is not usable yet.")
        job = ReaInstaller.current_job()
        if admin and job is not None and job.status == "failed":
            lines.append(f"Last attempt: stage {job.stage}, code {job.errorCode}.")
            lines += [f"- {components.blocker_text(components.public_blocker(blocker))}" for blocker in job.blockers]
            lines.append(_PATHS_NOTE)
    else:
        lines.append(f"REA ({runtime.REA_PACKAGE_SPEC}) is not installed on this Flocks service.")
    if admin:
        lines.append(
            "If the user asks to install, check or repair REA, call action=\"install\" now. Otherwise tell the user "
            "in one sentence that this task needs the REA plugin, which they can install from the 「REA 逆向分析」 "
            "shortcut of a new Rex session or the plugin square, then stop."
        )
    else:
        lines.append(
            "The current user is not an administrator and cannot install server components. Tell the user an "
            "administrator has to install the REA plugin first, then stop."
        )
    return "\n".join(lines)


async def _attached_files(session_id: Optional[str]) -> Optional[int]:
    """How many files the user attached to this conversation (None when it cannot be read)."""

    if not session_id:
        return None
    try:
        return await attachment_guard.attached_file_count(session_id)
    except Exception as exc:
        log.debug("rea.status.attachments_unreadable", {"error": str(exc)})
        return None


async def _status(ctx: ToolContext) -> ToolResult:
    user = _current_user()
    state = service.install_state()
    admin = service.can_install(user)
    attached = await _attached_files(ctx.session_id) if state == "ready" else None
    return ToolResult(
        success=True,
        output=_status_text(state, admin=admin, attached=attached),
        title="REA 逆向分析插件",
        metadata={"rea": {"action": "status", "state": state, "canInstall": admin}},
    )


# ── report ──


def _report(
    entries: Any,
    summary: Optional[str],
    title: Optional[str],
    facts: Any,
    inferences: Any,
    open_items: Any,
) -> ToolResult:
    try:
        normalized = _normalize_entries(entries)
        fact_list = _normalize_items(facts, "facts")
        inference_list = _normalize_items(inferences, "inferences")
        open_list = _normalize_items(open_items, "open_items")
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc), metadata={"code": "invalid_report"})
    report = {
        "action": "report",
        "title": _clean_text(title, 80) or None,
        "summary": _clean_text(summary, MAX_REPORT_SUMMARY_CHARS) or None,
        "entries": normalized,
        "facts": fact_list,
        "inferences": inference_list,
        "openItems": open_list,
    }
    return ToolResult(
        success=True,
        output=(
            "The user now sees the result card. Make sure the full report is saved with the write tool in the "
            "outputs directory, then reply with a one- or two-sentence summary without repeating the card."
        ),
        title="逆向分析结果",
        metadata={"rea": report},
    )


@ToolRegistry.register_function(
    name=REA_TOOL_NAME,
    group="代码分析",
    description=REA_TOOL_DESCRIPTION,
    description_cn="REA 逆向分析插件：按安装指引选择档位、安装缺失组件、注册 MCP 服务并用真实工具调用验证，在会话中展示分析成果。",
    category=ToolCategory.SYSTEM,
    native=True,
    always_load=False,
    requires_confirmation=False,
    tags=["reverse-engineering", "rea", "binary-analysis"],
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="install | status | report",
            enum=["install", "status", "report"],
        ),
        ToolParameter(
            name="tier",
            type=ParameterType.STRING,
            required=False,
            description="For install: light (JavaScript/.NET/ZIP) or full (adds JDK 21 + Ghidra for native ELF/PE)",
            enum=list(runtime.TIERS),
        ),
        ToolParameter(
            name="confirmed",
            type=ParameterType.BOOLEAN,
            required=False,
            description="For install: true only after the user chose the tier and confirmed the plan in this conversation",
        ),
        ToolParameter(
            name="entries",
            type=ParameterType.ARRAY,
            required=False,
            description="For report: sections such as 关键原理, 证据, 验证方法与结果",
            json_schema={
                "type": "array",
                "items": REPORT_ENTRY_JSON_SCHEMA,
                "minItems": 1,
                "maxItems": MAX_REPORT_ENTRIES,
            },
        ),
        ToolParameter(
            name="summary",
            type=ParameterType.STRING,
            required=False,
            description="For report: the conclusion in one short paragraph",
        ),
        ToolParameter(
            name="title",
            type=ParameterType.STRING,
            required=False,
            description="For report: optional title",
        ),
        ToolParameter(
            name="facts",
            type=ParameterType.ARRAY,
            required=False,
            description="For report: verified facts",
            json_schema={"type": "array", "items": {"type": "string"}, "maxItems": MAX_REPORT_ITEMS},
        ),
        ToolParameter(
            name="inferences",
            type=ParameterType.ARRAY,
            required=False,
            description="For report: inferences that are not verified",
            json_schema={"type": "array", "items": {"type": "string"}, "maxItems": MAX_REPORT_ITEMS},
        ),
        ToolParameter(
            name="open_items",
            type=ParameterType.ARRAY,
            required=False,
            description="For report: unfinished or unresolved items",
            json_schema={"type": "array", "items": {"type": "string"}, "maxItems": MAX_REPORT_ITEMS},
        ),
    ],
)
async def rea_analysis_tool(
    ctx: ToolContext,
    action: str,
    tier: Optional[str] = None,
    confirmed: Any = False,
    entries: Any = None,
    summary: Optional[str] = None,
    title: Optional[str] = None,
    facts: Any = None,
    inferences: Any = None,
    open_items: Any = None,
) -> ToolResult:
    if action == "status":
        return _for_conversation(await _status(ctx))
    if action == "install":
        return _for_conversation(await _install(ctx, tier, confirmed is True or str(confirmed).lower() == "true"))
    if action == "report":
        return _report(entries, summary, title, facts, inferences, open_items)
    return ToolResult(success=False, error=f"Unsupported action: {action!r}")
