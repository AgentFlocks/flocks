from __future__ import annotations

from flocks.situation_report.product.contracts import ReportAction, build_report_prompt_text
from flocks.situation_report.product.policy import decide_report_prompt


def _parts(text: str, operation: str | None = None):
    if operation is not None:
        action = {
            "name": f"situation_report.{operation}",
            "version": "1",
            "requestID": f"req-{operation}",
            "generationID": f"gen-{operation}",
        }
        if operation == "generate":
            action["language"] = "zh-CN"
        else:
            action["baseBackendReportVersion"] = 3
        text = build_report_prompt_text(
            action=ReportAction.model_validate(action),
            user_instruction=text,
        )
    return [{"type": "text", "text": text}]


def test_policy_redirects_new_report_and_configuration_changes_without_execution():
    cases = [
        ("另外生成一份报告", "new_report"),
        ("新生成一份报告", "new_report"),
        ("新建报告", "new_report"),
        ("再生成一份报告", "new_report"),
        ("把素材换一批", "material_change"),
        ("修改一下模板", "template_change"),
        ("把语言切换成英文", "language_change"),
    ]
    for text, reason in cases:
        decision = decide_report_prompt(_parts(text, "modify"), session_id="ses_current")
        assert decision.kind == "direct"
        assert decision.metadata["uiAction"]["reason"] == reason
        assert decision.metadata["uiAction"]["sessionID"] == "ses_current"


def test_policy_rejects_unstructured_or_unrelated_questions():
    for parts in (_parts("今天天气怎么样"), _parts("解释一下量子计算", "modify")):
        decision = decide_report_prompt(parts, session_id="ses_current")
        assert decision.kind == "direct"
        assert decision.metadata == {"policy": {"category": "out_of_scope", "rejected": True}}


def test_policy_executes_the_operation_selected_by_the_backend_entry_point():
    assert decide_report_prompt(_parts("生成报告", "generate"), session_id="ses_current").kind == "execute"
    assert (
        decide_report_prompt(_parts("请修改当前报告的行动建议", "modify"), session_id="ses_current").kind == "execute"
    )
    regenerate = decide_report_prompt(
        _parts("应用当前配置生成整份报告", "regenerate"),
        session_id="ses_current",
    )
    assert regenerate.kind == "execute"
    assert regenerate.prompt is not None
    assert regenerate.prompt.action.operation == "regenerate"


def test_policy_keeps_conversation_rewrite_requests_as_modify():
    for text in (
        "重新生成",
        "重新生成一份报告",
        "请重新生成当前报告",
        "请从头撰写整份报告",
        "regenerate this report",
        "rewrite the report from scratch",
    ):
        decision = decide_report_prompt(_parts(text, "modify"), session_id="ses_current")
        assert decision.kind == "execute"
        assert decision.prompt is not None
        assert decision.prompt.action.operation == "modify"
