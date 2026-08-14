"""Model-free phase-one intent boundary for managed report conversations."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .contracts import ParsedReportPrompt, parse_report_prompt_parts


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
_REGENERATE = re.compile(r"重新(?:生成|写|撰写)|从头(?:生成|写)|regenerate|rewrite\s+from\s+scratch", re.I)
_MODIFY_SIGNAL = re.compile(
    r"报告|章节|段落|标题|正文|内容|措辞|表格|建议|结论|摘要|事件|漏洞|IOC|ATT&CK|"
    r"修改|改写|润色|删除|删掉|增加|补充|调整|精简|扩写|纠正|"
    r"report|section|paragraph|title|revise|modify|edit|shorten|expand|correct",
    re.I,
)


def _text(parts: list[dict[str, Any]]) -> str:
    return "\n".join(str(part.get("text") or "") for part in parts if part.get("type") == "text").strip()


def _ui_action(reason: str, session_id: str) -> ReportPolicyDecision:
    message = (
        "新报告需要通过页面的 AI 生成报告入口创建。"
        if reason == "new_report"
        else "素材、模板或语言需要在报告配置页修改。"
    )
    return ReportPolicyDecision(
        kind="direct",
        text=message,
        metadata={
            "uiAction": {
                "type": "open_report_config",
                "reason": reason,
                "buttonText": "前往配置",
                "sessionID": session_id,
            }
        },
    )


def _reject() -> ReportPolicyDecision:
    return ReportPolicyDecision(
        kind="direct",
        text="当前助手仅支持报告生成和报告内容修改。",
        metadata={"policy": {"category": "out_of_scope", "rejected": True}},
    )


def decide_report_prompt(parts: list[dict[str, Any]], *, session_id: str) -> ReportPolicyDecision:
    """Apply explicit redirects/rejection before parsing an executable action."""

    try:
        prompt = parse_report_prompt_parts(parts)
    except (ValueError, TypeError):
        return _reject()

    text = prompt.text
    if _NEW_REPORT.search(text):
        return _ui_action("new_report", session_id)
    for reason, pattern in _CONFIG_PATTERNS:
        if pattern.search(text):
            return _ui_action(reason, session_id)

    if prompt.action.operation == "modify":
        if _REGENERATE.search(text):
            return _reject()
        if not _MODIFY_SIGNAL.search(text):
            return _reject()
    elif prompt.action.operation == "regenerate" and not _REGENERATE.search(text):
        return _reject()
    return ReportPolicyDecision(kind="execute", prompt=prompt)
