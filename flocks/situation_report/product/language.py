"""Report-scoped language for model instructions and runtime-authored replies."""

from __future__ import annotations

from .session_state import load_session_state, state_path


def resolve_report_language(session_id: str, requested: str | None = None) -> str:
    """Use the generation setting, then stored Session language, then the UI default."""
    if requested in {"zh-CN", "en-US"}:
        return requested
    if state_path(session_id).is_file():
        language = (load_session_state(session_id).report_state or {}).get("language")
        if language in {"zh-CN", "en-US"}:
            return language
    return "zh-CN"


def output_language_instruction(language: str) -> str:
    if language == "en-US":
        return (
            "The configured report language for this task is English (en-US). "
            "Use English for every user-visible response, including progress explanations, "
            "clarifications, revision summaries, completion replies, and the report itself. "
            "Use English for natural-language tool arguments such as source-read reasons. "
            "Do not follow the language of the user's message, previous replies, Skill, or tool instructions. "
            "Preserve exact tool names, JSON keys, identifiers, URLs, proper names, source quotations, "
            "and template-required headings. Do not change the configured report language."
        )
    return (
        "本轮报告配置语言为简体中文（zh-CN）。所有用户可见回复均使用简体中文，"
        "包括进度说明、澄清、修改摘要、完成回复和报告正文；素材回查原因等工具自然语言参数也使用简体中文。"
        "不要跟随用户输入、历史回复、Skill 或工具指令的语言切换。"
        "工具名、JSON 字段、标识符、URL、专有名称、原文引述及模板明确要求的章节名保留准确原值。"
        "不得自行改变报告配置语言。"
    )


_MESSAGES = {
    "continuation_unknown": (
        "我理解你希望继续报告任务，但当前没有可确认的上一任务。请从页面的报告生成入口开始，或说明希望修改已有报告的哪部分。",
        "I understand you want to continue the report task, but no previous task can be confirmed. Use the report generation entry, or specify which part of an existing report to revise.",
    ),
    "continuation_running": (
        "上一报告任务尚未记录最终状态，请先查看其执行状态，不要重复提交生成任务。",
        "The previous report task has not recorded a final status. Check its progress before submitting another generation task.",
    ),
    "continuation_cancelled": (
        "上一报告任务已取消，不会自动恢复已取消的执行。请通过页面的生成或重新生成入口重试；若要修改已有报告，请说明修改内容。",
        "The previous report task was cancelled and will not resume automatically. Retry using the generation or regeneration entry; to revise an existing report, specify the changes.",
    ),
    "continuation_failed": (
        "上一报告任务未完成。请先查看失败原因，处理后通过页面的生成或重新生成入口重试；若要修改已有报告，请说明修改内容。",
        "The previous report task failed. Review the failure details and address the cause before retrying through the generation or regeneration entry; for an existing report, specify the changes.",
    ),
    "continuation_succeeded": (
        "上一报告任务已经完成。请说明接下来需要修改或补充的章节和内容。",
        "The previous report task is complete. Please specify the sections or content you want revised or expanded.",
    ),
    "new_report": (
        "新报告需要通过页面的 AI 生成报告入口创建。",
        "Create a new report using the AI report generation entry on the page.",
    ),
    "configuration": (
        "素材、模板或语言需要在报告配置页修改。",
        "Change materials, the template, or the language on the report configuration page.",
    ),
    "config_button": ("前往配置", "Open configuration"),
    "out_of_scope": (
        "当前助手仅支持报告生成和报告内容修改。",
        "This assistant only supports report generation and report content revisions.",
    ),
    "recovery": ("正在继续完善报告。", "Continuing to complete the report."),
    "succeeded": (
        "报告已生成并发布（版本：{version}）。",
        "The report has been generated and published (version: {version}).",
    ),
    "cancelled": ("报告生成已取消。", "Report generation has been cancelled."),
    "failed": (
        "报告生成失败，请查看任务错误详情。",
        "Report generation failed. Please check the task error details.",
    ),
}


def report_message(key: str, language: str, **values: str) -> str:
    return _MESSAGES[key][1 if language == "en-US" else 0].format(**values)
