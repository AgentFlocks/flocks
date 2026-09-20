"""Runtime-authored presentation, scoped to the five production report tools."""

from typing import Any, Literal

from .language import resolve_report_language

DisplayStatus = Literal["pending", "running", "succeeded", "needs_revision", "failed", "cancelled"]
TOOL_STEPS = {
    "situation_product_context_read": "context",
    "situation_product_material_read": "materials",
    "situation_product_source_read": "source",
    "situation_product_report_write": "write",
    "situation_product_report_validate": "validate",
}
_TITLES = {
    "context": ("读取报告配置", "Read report configuration"),
    "materials": ("读取素材", "Read materials"),
    "source": ("读取素材详情", "Read material details"),
    "write": ("写入报告初稿", "Write initial report draft"),
    "revision": ("写入报告修订稿", "Write revised report draft"),
    "validate": ("检查报告", "Check report"),
}


async def settings(session_id: str, generation_id: str) -> dict[str, Any]:
    from .workspace import read_tool_display_settings

    try:
        return await read_tool_display_settings(session_id=session_id, generation_id=generation_id)
    except Exception:
        # Display fallback must not bypass the operation's own validation.
        try:
            language = resolve_report_language(session_id)
        except Exception:
            language = "zh-CN"
        return {"language": language, "revision": False}


def metadata(step: str, status: DisplayStatus, language: str, detail: str = "") -> dict[str, Any]:
    en = language == "en-US"
    label = _TITLES[step][int(en)]
    titles = {
        "pending": (f"等待{label}", f"Waiting: {label}"),
        "running": (f"正在{label}", f"In progress: {label}"),
        "succeeded": (f"{label}完成", f"Completed: {label}"),
        "needs_revision": ("报告需修订", "Report needs revision"),
        "failed": (f"{label}失败", f"Failed: {label}"),
        "cancelled": (f"已停止{label}", f"Cancelled: {label}"),
    }
    title = titles[status][int(en)]
    if step == "validate" and status == "succeeded":
        title = "Report check passed" if en else "报告检查通过"
    return {
        "runtime": "situation-report-product-v1",
        "display": {"status": status, "title": title, "detail": detail},
    }


async def failure_metadata(
    tool_name: str, session_id: str, generation_id: str, status: Literal["failed", "cancelled"] = "failed"
) -> dict[str, Any] | None:
    """Fallback for rejection/cancellation before a report plugin can return."""
    if tool_name not in TOOL_STEPS:
        return None
    config = await settings(session_id, generation_id)
    step = TOOL_STEPS[tool_name]
    if step == "write" and config["revision"]:
        step = "revision"
    return metadata(step, status, config["language"])


def completed_metadata(step: str, output: dict[str, Any], language: str) -> dict[str, Any]:
    en = language == "en-US"
    detail = ""
    if step in {"write", "revision", "validate"}:
        check = output if step == "validate" else output["validation"]
        attempt = check["attempt"]
        status: DisplayStatus = "succeeded" if check["status"] == "passed" else "needs_revision"
        count = len(check.get("issues") or [])
        detail = f"Check {attempt}: {count} issue(s)." if en else f"第{attempt}次检查：{count}项问题。"
        if status == "needs_revision" and attempt >= 3:
            detail += (
                " Check limit reached; no automatic continuation is promised."
                if en
                else "已达检查次数上限，不会据此自动继续修订。"
            )
        return metadata("validate", status, language, detail)
    if step == "context":
        count = output.get("materialCount")
        detail = f"Materials: {count}; language: {language}." if en else f"素材共{count}条；报告语言：中文。"
    elif step == "materials":
        start, total = output["offset"], output["total"]
        fragment = output.get("materialFragment")
        if fragment:
            detail = (
                f"Material {start + 1}/{total}, characters {fragment['offset'] + 1}–"
                f"{fragment['nextOffset']}/{fragment['totalCharacters']}."
                if en
                else f"第{start + 1}/{total}条素材，字符{fragment['offset'] + 1}–"
                f"{fragment['nextOffset']}/{fragment['totalCharacters']}。"
            )
        elif output.get("materials"):
            end = start + len(output["materials"])
            detail = (
                f"Read materials {start + 1}–{end} of {total}."
                if en
                else f"本次读取第{start + 1}–{end}条，共{total}条。"
            )
        else:
            detail = "No materials returned on this page." if en else "本次未返回素材。"
        # A last-page cursor does not prove all earlier pages were read.
        if not output["hasMore"]:
            detail += " End of material snapshot." if en else "已到素材快照末页。"
    elif step == "source":
        if output.get("matchFound") is False:
            detail = (
                "No keyword match in this search range; this does not prove the fact is absent."
                if en
                else "本次范围内未匹配关键词，不代表该事实不存在。"
            )
        elif "totalCharacters" in output and output["nextOffset"] <= output["offset"]:
            detail = "No detail characters returned in this range." if en else "本次范围内未返回详情正文。"
        elif "totalCharacters" in output:
            detail = (
                f"Read detail characters {output['offset'] + 1}–{output['nextOffset']} of {output['totalCharacters']}."
                if en
                else f"本次读取详情字符{output['offset'] + 1}–{output['nextOffset']}，"
                f"共{output['totalCharacters']}字符。"
            )
            if output.get("hasMore"):
                detail += " More detail is available." if en else "还有后续内容。"
        else:
            detail = "Material details retrieved." if en else "已获取素材详情。"
    return metadata(step, "succeeded", language, detail)
