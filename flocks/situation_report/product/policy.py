"""Model-free phase-one intent boundary for managed report conversations."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .contracts import ParsedReportPrompt, parse_report_prompt_parts
from .language import report_message, resolve_report_language
from .files import read_json, session_root
from .session_state import load_session_state, state_path


class ReportPolicyDecision(BaseModel):
    kind: Literal["execute", "direct"]
    prompt: Optional[ParsedReportPrompt] = None
    text: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_NEW_REPORT = re.compile(
    r"(?:新建(?:一?份)?(?:报告)?|(?<!重)新生成一?份(?:报告)?|"
    r"另(?:外)?(?:新)?生成一?份(?:报告)?|再生成一?份(?:报告)?)|"
    r"new\s+(?:another\s+)?report",
    re.I,
)
_CHANGE = r"(?:改|换|调整|切换|变更|修改|change|switch|replace)"
_CONFIG_PATTERNS = (
    (
        "material_change",
        re.compile(rf"(?:素材|材料|material).{{0,12}}{_CHANGE}|{_CHANGE}.{{0,12}}(?:素材|材料|material)", re.I),
    ),
    ("template_change", re.compile(rf"(?:模板|template).{{0,12}}{_CHANGE}|{_CHANGE}.{{0,12}}(?:模板|template)", re.I)),
    (
        "language_change",
        re.compile(
            rf"(?:语言|中文|英文|language).{{0,12}}{_CHANGE}|{_CHANGE}.{{0,12}}(?:语言|中文|英文|language)", re.I
        ),
    ),
)
_GENERAL_CONFIG = re.compile(
    r"(?:打开|进入|前往|跳转|访问|查看).{0,12}(?:报告)?(?:配置|设置)(?:页|页面)?|"
    r"(?:修改|调整|更改|配置|设置).{0,6}(?:报告)?(?:配置|设置)(?:页|页面)?|"
    r"^(?:请|我想|我要|帮我|麻烦)?(?:进行|做|改|修改|调整|更改|查看|打开|进入|前往)?"
    r"(?:一下)?(?:报告)?(?:配置|设置)(?:页|页面)?[。.!！?？]*$|"
    r"(?:open|go\s+to|show|change|edit)\s+(?:the\s+)?report\s+"
    r"(?:config(?:uration)?|settings?)",
    re.I,
)
_MODIFY_SIGNAL = re.compile(
    r"报告|章节|段落|标题|正文|内容|措辞|表格|建议|结论|摘要|事件|漏洞|IOC|ATT&CK|"
    r"修改|改写|润色|删除|删掉|增加|补充|调整|精简|扩写|纠正|"
    r"重新(?:生成|写|撰写)|从头(?:生成|写)|"
    r"report|section|paragraph|title|revise|modify|edit|shorten|expand|correct|"
    r"regenerate|rewrite\s+from\s+scratch",
    re.I,
)

# Standalone follow-ups refer to the preceding task; they are not unrelated Q&A.
# Do not turn an arbitrary sentence beginning with "continue" into a report task.
_CONTINUATION = re.compile(
    r"^(?:(?:请|麻烦|帮我)?\s*(?:继续(?:生成|撰写|写|执行|完成|修改)?(?:报告|任务)?|"
    r"接着(?:写|生成|完成)(?:报告)?|重试(?:一下|上次任务|报告)?)|"
    r"(?:please\s+)?(?:continue|resume|retry)(?:\s+(?:the\s+)?(?:report|task|generation))?)\s*[。.!！?？]*$",
    re.I,
)
_SHORT_REVISION = re.compile(
    r"^(?:(?:请|再|更)?(?:简短|简洁|详细|具体|正式|精简)(?:一点|一些)?|换个说法|"
    r"(?:make\s+it\s+)?(?:shorter|longer|more concise|more detailed))\s*[。.!！?？]*$", re.I,
)


def _continuation_guidance(session_id: str, language: str) -> ReportPolicyDecision:
    latest = None
    for path in (session_root(session_id) / "runs").glob("*/event_state.json"):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        try:
            value = read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(value, dict) or not isinstance(value.get("updatedAt"), str):
            continue
        if latest is None or value["updatedAt"] > latest[0]:
            latest = (value["updatedAt"], path.parent.name, value.get("lastStatus"))
    previous_status = latest[2] if latest else None
    reason = previous_status if previous_status in {"running", "cancelled", "failed", "succeeded"} else "unknown"
    return ReportPolicyDecision(
        kind="direct", text=report_message(f"continuation_{reason}", language),
        metadata={"policy": {"category": "continuation", "rejected": False,
                             "reason": f"previous_task_{reason}",
                             "previousGenerationID": latest[1] if latest else None,
                             "previousStatus": previous_status}},
    )


def _text(parts: list[dict[str, Any]]) -> str:
    return "\n".join(str(part.get("text") or "") for part in parts if part.get("type") == "text").strip()


def _ui_action(reason: str, session_id: str, language: str) -> ReportPolicyDecision:
    message = report_message("new_report" if reason == "new_report" else "configuration", language)
    return ReportPolicyDecision(
        kind="direct",
        text=message,
        metadata={
            "uiAction": {
                "type": "open_report_config",
                "reason": reason,
                "buttonText": report_message("config_button", language),
                "sessionID": session_id,
            }
        },
    )


def _reject(language: str) -> ReportPolicyDecision:
    return ReportPolicyDecision(
        kind="direct",
        text=report_message("out_of_scope", language),
        metadata={"policy": {"category": "out_of_scope", "rejected": True}},
    )


def decide_report_prompt(parts: list[dict[str, Any]], *, session_id: str) -> ReportPolicyDecision:
    """Apply explicit redirects/rejection before parsing an executable action."""

    language = resolve_report_language(session_id)
    try:
        prompt = parse_report_prompt_parts(parts)
    except (ValueError, TypeError):
        return _reject(language)

    language = resolve_report_language(session_id, prompt.action.language)
    text = prompt.text
    if _NEW_REPORT.search(text):
        return _ui_action("new_report", session_id, language)
    for reason, pattern in _CONFIG_PATTERNS:
        if pattern.search(text):
            return _ui_action(reason, session_id, language)
    if _GENERAL_CONFIG.search(text):
        return _ui_action("configuration_change", session_id, language)

    if prompt.action.operation == "modify" and _CONTINUATION.fullmatch(text.strip()):
        return _continuation_guidance(session_id, language)
    if prompt.action.operation == "modify" and _SHORT_REVISION.fullmatch(text.strip()):
        report = load_session_state(session_id).report_state if state_path(session_id).is_file() else None
        if report and (report.get("currentFlocksReportVersion") or report.get("syncedBackendReportVersion")):
            return ReportPolicyDecision(kind="execute", prompt=prompt)
        return _continuation_guidance(session_id, language)

    # The trusted business backend selects the operation from the product
    # entry point: first-generation button -> generate, dedicated regenerate
    # button -> regenerate, and every conversation turn -> modify.  User text
    # may describe a broad rewrite, but it must not override or invalidate the
    # explicit action selected by that entry point.
    if prompt.action.operation == "modify" and not _MODIFY_SIGNAL.search(text):
        return _reject(language)
    return ReportPolicyDecision(kind="execute", prompt=prompt)
