"""Synthetic state/intent regression tests; no real reports or model calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.input.events import UserInputEvent
from flocks.situation_report.product import orchestrator
from flocks.situation_report.product.contracts import ReportAction, build_report_prompt_text
from flocks.situation_report.product.files import atomic_write_json, session_root
from flocks.situation_report.product.policy import decide_report_prompt
from flocks.situation_report.product.session_state import ensure_session_state, save_session_state

SID = "ses_followup_test"


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("SITUATION_REPORT_PRODUCT_ROOT", str(tmp_path))


def parts(text):
    action = ReportAction(
        name="situation_report.modify",
        version="1",
        requestID="req_new",
        generationID="gen_new",
        baseBackendReportVersion=0,
    )
    return [{"type": "text", "text": build_report_prompt_text(action=action, user_instruction=text)}]


def task(status, *, language="zh-CN"):
    state = ensure_session_state(SID)
    state.report_state = {"language": language}
    save_session_state(state)
    atomic_write_json(
        session_root(SID) / "runs/gen_previous/event_state.json",
        {"lastStatus": status, "updatedAt": "2026-09-17T00:00:00+00:00"},
    )


@pytest.mark.parametrize(
    "text",
    ["继续", "继续吧", "好的，继续", "继续生成", "继续生成报告", "请继续。", "重试", "接着写", "continue", "please resume the task", "retry"],
)
def test_short_continuations_are_not_unrelated_or_silently_restarted(text):
    task("cancelled")
    decision = decide_report_prompt(parts(text), session_id=SID)
    assert decision.kind == "direct" and decision.prompt is None
    assert decision.metadata["policy"] == {
        "category": "continuation",
        "rejected": False,
        "reason": "previous_task_cancelled",
        "previousGenerationID": "gen_previous",
        "previousStatus": "cancelled",
    }
    assert "已取消" in decision.text


@pytest.mark.parametrize("status", ["running", "failed", "succeeded", "cancelled"])
@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_guidance_uses_previous_status_and_report_language(status, language):
    task(status, language=language)
    decision = decide_report_prompt(parts("继续"), session_id=SID)
    assert decision.metadata["policy"]["reason"] == f"previous_task_{status}"
    assert ("上一报告任务" in decision.text) == (language == "zh-CN")


def test_no_previous_task_and_missing_report_get_guidance_without_creating_state():
    for text in ["继续", "简短一点"]:
        decision = decide_report_prompt(parts(text), session_id=SID)
        assert decision.metadata["policy"]["reason"] == "previous_task_unknown"
    assert not (session_root(SID) / "index.json").exists()


def test_latest_run_is_selected_and_malformed_state_does_not_crash_policy():
    task("failed")
    atomic_write_json(
        session_root(SID) / "runs/gen_later/event_state.json",
        {"lastStatus": "succeeded", "updatedAt": "2026-09-17T00:01:00+00:00"},
    )
    atomic_write_json(session_root(SID) / "runs/gen_invalid/event_state.json", [])
    assert decide_report_prompt(parts("继续"), session_id=SID).metadata["policy"]["previousGenerationID"] == "gen_later"


@pytest.mark.parametrize("text", ["简短一点", "短一点", "更详细一点", "换个说法", "make it shorter"])
def test_specific_short_revisions_keep_modify_and_preserve_original_instruction(text):
    state = ensure_session_state(SID)
    state.report_state = {"language": "zh-CN", "currentFlocksReportVersion": "frv_001"}
    save_session_state(state)
    decision = decide_report_prompt(parts(text), session_id=SID)
    assert decision.kind == "execute"
    assert decision.prompt.action.operation == "modify" and decision.prompt.text == text


@pytest.mark.parametrize("text", ["继续讲量子力学", "continue writing unrelated poetry", "重试天气查询"])
def test_continuation_prefix_does_not_bypass_scope(text):
    assert decide_report_prompt(parts(text), session_id=SID).metadata["policy"]["rejected"] is True


@pytest.mark.asyncio
async def test_guidance_is_persisted_without_model_or_terminal_failure(monkeypatch):
    task("cancelled")
    decision = decide_report_prompt(parts("继续"), session_id=SID)
    direct, runner, publish = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(orchestrator, "persist_direct_response", direct)
    monkeypatch.setattr(orchestrator, "publish_report_status", publish)
    await orchestrator.run_managed_report_turn(
        session=SimpleNamespace(id=SID),
        event=UserInputEvent(source_type="webui", sessionID=SID, text="继续", parts=parts("继续")),
        decision=decision,
        working_directory=".",
        generic_runner=runner,
    )
    direct.assert_awaited_once()
    runner.assert_not_awaited()
    publish.assert_not_awaited()
