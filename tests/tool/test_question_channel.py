from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.system import question as question_module
from flocks.tool.system.question import normalize_question_option, question_tool


def test_normalize_question_option_accepts_common_llm_shapes() -> None:
    assert normalize_question_option({"value": "NVD", "desc": "Public CVE feed"}) == {
        "label": "NVD",
        "description": "Public CVE feed",
    }
    assert normalize_question_option({"text": "Internal scanner"}) == {
        "label": "Internal scanner",
        "description": "",
    }
    assert normalize_question_option({"description": "Only descriptive text"}) == {
        "label": "Only descriptive text",
        "description": "",
    }
    assert normalize_question_option({"label": ""}) is None


@pytest.mark.asyncio
async def test_question_tool_falls_back_to_text_when_choice_has_no_valid_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_questions: list[dict] = []

    async def fake_handler(_session_id: str, questions: list[dict]) -> list[list[str]]:
        captured_questions.extend(questions)
        return [["manual answer"]]

    monkeypatch.setattr(question_module, "_question_handler", fake_handler)
    monkeypatch.setattr(question_module, "_send_channel_question_if_applicable", AsyncMock(return_value=None))

    result = await question_module.question_tool(
        ToolContext(session_id="ses_question_fallback", message_id="msg_1"),
        questions=[
            {
                "question": "漏洞数据源用什么?",
                "type": "choice",
                "options": [{"label": ""}],
            }
        ],
    )

    assert result.success is True
    assert captured_questions[0]["type"] == "text"
    assert captured_questions[0]["options"] == []


@pytest.mark.asyncio
async def test_question_tool_sends_plain_text_for_channel_session() -> None:
    binding = SimpleNamespace(
        channel_id="feishu",
        account_id="default",
        chat_id="chat_1",
        chat_type=SimpleNamespace(value="group"),
        thread_id=None,
        session_id="ses_channel",
    )
    svc = SimpleNamespace(
        get_bindings_by_session=AsyncMock(return_value=[binding]),
    )

    with patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(return_value=[]),
    ) as deliver:
        result = await question_tool(
            ToolContext(session_id="ses_channel", message_id="msg_1"),
            questions=[
                {
                    "question": "请选择目标 session",
                    "type": "choice",
                    "options": [
                        {"label": "研发群", "description": "session_id=ses_1"},
                        {"label": "运维群", "description": "session_id=ses_2"},
                    ],
                }
            ],
        )

    assert result.success is True
    assert result.metadata["deferred"] is True
    assert result.metadata["channel_session"] is True
    deliver.assert_awaited_once()
    outbound_ctx = deliver.await_args.args[0]
    assert outbound_ctx.channel_id == "feishu"
    assert outbound_ctx.to == "chat_1"
    assert "请选择目标 session" in outbound_ctx.text
    assert "1. 研发群 - session_id=ses_1" in outbound_ctx.text
    assert "2. 运维群 - session_id=ses_2" in outbound_ctx.text
