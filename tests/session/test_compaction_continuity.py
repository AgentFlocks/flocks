"""What an agent still knows after an automatic compaction.

Covers the pieces that decide whether the agent can resume its task:
- prune() does not treat the compaction continuation as a new turn
- the verbatim tail kept by a compaction stays within its token budget
- the continuation message re-injects archived user requests and the todo list
- the continuation keeps the turn's agent / model / execution mode
- summary input keeps user messages and the assistant's own text
"""

import pytest

from flocks.provider.provider import ChatMessage
from flocks.session.execution_mode import SessionExecutionMode
from flocks.session.features.todo import Todo, TodoInfo
from flocks.session.lifecycle.compaction import CompactionPolicy, SessionCompaction
from flocks.session.lifecycle.compaction.compaction import (
    _compress_message_for_summary,
    _prune_chat_messages_for_summary,
)
from flocks.session.message import (
    Message,
    MessageRole,
    ToolPart,
    ToolStateCompleted,
)


def _policy(**overrides) -> CompactionPolicy:
    return CompactionPolicy.from_model(128_000, 4_096, overrides=overrides)


async def _user(session_id: str, text: str, **kwargs):
    return await Message.create(
        session_id=session_id,
        role=MessageRole.USER,
        content=text,
        agent=kwargs.pop("agent", "rex"),
        model=kwargs.pop("model", {"providerID": "gateway", "modelID": "gpt-test"}),
        **kwargs,
    )


async def _assistant_with_read(session_id: str, parent_id: str, output: str):
    assistant = await Message.create(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content="",
        parent_id=parent_id,
        model_id="gpt-test",
        provider_id="gateway",
        finish="tool-calls",
    )
    await Message.store_part(
        session_id,
        assistant.id,
        ToolPart(
            sessionID=session_id,
            messageID=assistant.id,
            callID=f"call_{assistant.id}",
            tool="read",
            state=ToolStateCompleted(
                input={"filePath": "/tmp/doc.md"},
                output=output,
                title="read",
                metadata={},
                time={"start": 1, "end": 2},
            ),
        ),
    )
    return assistant


async def _read_part_compacted(session_id: str, message_id: str) -> bool:
    parts = await Message.parts(message_id, session_id)
    tool = next(part for part in parts if getattr(part, "type", None) == "tool")
    return bool((tool.state.time or {}).get("compacted"))


class TestPruneTurnCounting:
    @pytest.mark.asyncio
    async def test_synthetic_continuation_does_not_expire_current_turn_reads(self):
        """Regression: the prune right after an auto-compaction counted the
        synthetic continuation message as a new user turn and expired the
        ``read`` output of the step the agent was in the middle of."""
        session_id = "ses_prune_synthetic_turn"
        user = await _user(session_id, "summarize the CRM doc")
        assistant = await _assistant_with_read(session_id, user.id, "d" * 4_000)
        await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="The conversation history before this point was compacted.",
            synthetic=True,
            part_metadata={"compactionContinuation": True},
        )

        await SessionCompaction.prune(session_id, policy=_policy(prune_minimum=10))

        assert not await _read_part_compacted(session_id, assistant.id)

    @pytest.mark.asyncio
    async def test_other_synthetic_prompts_still_start_a_turn(self):
        # Goal continuations and background results keep their existing
        # retention behaviour; only the compaction continuation is exempt.
        session_id = "ses_prune_goal_turn"
        user = await _user(session_id, "keep improving the doc")
        assistant = await _assistant_with_read(session_id, user.id, "d" * 4_000)
        await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="Continue working toward the goal.",
            synthetic=True,
            part_metadata={"goalContinuation": True},
        )

        await SessionCompaction.prune(session_id, policy=_policy(prune_minimum=10))

        assert await _read_part_compacted(session_id, assistant.id)

    @pytest.mark.asyncio
    async def test_real_user_message_still_expires_previous_turn_reads(self):
        session_id = "ses_prune_real_turn"
        user = await _user(session_id, "summarize the CRM doc")
        assistant = await _assistant_with_read(session_id, user.id, "d" * 4_000)
        await _user(session_id, "now write it to workspace/crm")

        await SessionCompaction.prune(session_id, policy=_policy(prune_minimum=10))

        assert await _read_part_compacted(session_id, assistant.id)


class TestContinuationMessage:
    @pytest.mark.asyncio
    async def test_continuation_reinjects_archived_requests_and_todos(self):
        session_id = "ses_continuation_context"
        first = await _user(session_id, "设计 Flocks CRM：客户与商机管理的 Skill + 存储方案")
        correction = await _user(session_id, "文件目录最好就放到workspace/crm下，不要放到data/crm/files下")
        kept = await _user(session_id, "继续")
        for message in (first, correction):
            await Message.archive(session_id, message.id)
        await Todo.update(session_id, [
            TodoInfo(id="1", content="读完现有 skill 文档", status="completed"),
            TodoInfo(id="2", content="写 CRM 设计文档", status="in_progress"),
            TodoInfo(id="3", content="实现存储层", status="pending"),
        ])

        text = await SessionCompaction._build_post_compaction_context(
            session_id,
            policy=_policy(),
        )

        # Keeps the marker the runner uses to recognise system continuations.
        assert "Continue if you have next steps" in text
        assert "do not re-plan work that is already done" in text
        assert "设计 Flocks CRM：客户与商机管理的 Skill + 存储方案" in text
        assert "文件目录最好就放到workspace/crm下，不要放到data/crm/files下" in text
        # Still-visible messages are not repeated.
        assert "- 继续" not in text
        assert kept.id
        assert "- [completed] 读完现有 skill 文档" in text
        assert "- [in_progress] 写 CRM 设计文档" in text
        assert "- [pending] 实现存储层" in text

    @pytest.mark.asyncio
    async def test_archived_requests_keep_first_and_latest_within_budget(self):
        session_id = "ses_continuation_budget"
        texts = [f"request {index} " + "x" * 900 for index in range(6)]
        for text in texts:
            message = await _user(session_id, text)
            await Message.archive(session_id, message.id)

        rendered = await SessionCompaction._archived_user_requests_text(
            session_id,
            max_chars=3_000,
        )

        assert rendered.startswith(f"- (earliest request in this session) {texts[0]}")
        assert texts[-1] in rendered
        assert texts[1] not in rendered
        assert "more request(s) in between" in rendered

    @pytest.mark.asyncio
    async def test_small_models_get_todos_but_not_request_history(self):
        session_id = "ses_continuation_small"
        message = await _user(session_id, "the original task")
        await Message.archive(session_id, message.id)
        await Todo.update(session_id, [TodoInfo(id="1", content="step one", status="pending")])

        text = await SessionCompaction._build_post_compaction_context(
            session_id,
            policy=CompactionPolicy.from_model(8_000, 1_000),
        )

        assert "- [pending] step one" in text
        assert "the original task" not in text

    @pytest.mark.asyncio
    async def test_continuation_keeps_turn_agent_model_and_mode(self):
        """Regression: the continuation used Message.create defaults, so after
        an auto-compaction the loop saw agent "rex", the fallback model and
        Build mode even for a plan-mode turn of another agent."""
        session_id = "ses_continuation_turn_fields"
        model = {"providerID": "gateway", "modelID": "gpt-test"}
        user = await _user(
            session_id,
            "plan the CRM storage",
            agent="analyst",
            model=model,
            executionMode=SessionExecutionMode.PLAN,
        )
        for _ in range(3):
            await _assistant_with_read(session_id, user.id, "r" * 200)

        result = await SessionCompaction._archive_and_write_summary(
            session_id=session_id,
            parent_id=user.id,
            summary="## Current Task\nplanning",
            model_id="gpt-test",
            provider_id="gateway",
            auto=True,
            policy=_policy(),
        )

        assert result == "continue"
        continuation = (await Message.list(session_id))[-1]
        assert continuation.role == "user"
        assert continuation.agent == "analyst"
        assert continuation.model == model
        assert continuation.executionMode == SessionExecutionMode.PLAN
        parts = await Message.parts(continuation.id, session_id)
        assert parts[0].synthetic is True
        assert parts[0].metadata == {"compactionContinuation": True}

    @pytest.mark.asyncio
    async def test_preserved_tail_is_bounded_by_token_budget(self):
        """A tail of huge tool outputs must not survive compaction whole, or
        the next step overflows again and compaction repeats until it gives
        up.  The newest step is always kept."""
        session_id = "ses_compaction_tail_budget"
        user = await _user(session_id, "read three large docs")
        older = [
            await _assistant_with_read(session_id, user.id, "a" * 20_000)
            for _ in range(2)
        ]
        newest = await _assistant_with_read(session_id, user.id, "b" * 20_000)
        # 12_000 usable tokens -> 3_600-token tail budget; each step ~5_000.
        policy = CompactionPolicy.from_model(16_000, 4_000)

        await SessionCompaction._archive_and_write_summary(
            session_id=session_id,
            parent_id=user.id,
            summary="## Current Task\nreading docs",
            model_id="gpt-test",
            provider_id="gateway",
            auto=False,
            policy=policy,
        )

        active_ids = {message.id for message in await Message.list(session_id)}
        assert newest.id in active_ids
        assert not any(message.id in active_ids for message in older)
        # The kept step is also the tail's first assistant ("boundary"); its
        # output is what the agent resumes from, so it must stay readable.
        assert not await _read_part_compacted(session_id, newest.id)


class TestSummaryInputPrePrune:
    def test_user_messages_are_never_compressed(self):
        text = "文件目录放到 workspace/crm 下。" * 40
        assert _compress_message_for_summary("user", text) == text

    def test_assistant_keeps_own_text_and_collapses_tool_blocks(self):
        content = (
            "Plan: write the CRM doc under workspace/crm, then add storage.\n"
            "[tool: read] input: {'filePath': '/tmp/a.md'}\n"
            "[tool: read] output: " + "z" * 3_000
        )

        compressed = _compress_message_for_summary("assistant", content)

        assert compressed.startswith("Plan: write the CRM doc under workspace/crm")
        assert "z" * 100 not in compressed
        assert "[assistant|tools: read]" in compressed

    def test_long_assistant_text_is_capped(self):
        compressed = _compress_message_for_summary("assistant", "p" * 5_000)
        assert len(compressed) <= 1_201
        assert compressed.endswith("…")

    def test_middle_user_request_survives_pre_prune(self):
        requirement = "请把文件目录放到 workspace/crm 下，不要放到 data/crm/files 下。" * 10
        messages = [
            ChatMessage(role="user", content="start"),
            ChatMessage(role="assistant", content="ok"),
            ChatMessage(role="assistant", content="ok again"),
            ChatMessage(role="user", content=requirement),
            ChatMessage(role="assistant", content="[tool: read] output: " + "y" * 5_000),
            ChatMessage(role="user", content="latest"),
            # Fills the protected tail so indexes 3-4 are "old" messages.
            ChatMessage(role="assistant", content="w" * 6_000),
            ChatMessage(role="assistant", content="done"),
        ]
        policy = CompactionPolicy.from_model(8_000, 1_000)

        pruned = _prune_chat_messages_for_summary(messages, policy)

        assert pruned[3].content == requirement
        assert "y" * 100 not in pruned[4].content

    def test_transcript_is_capped_at_half_the_usable_context(self):
        """Keeping each old step's own text must not let a long session
        outgrow the model window: the summary call itself would then fail
        and compaction would give up after three attempts."""
        from flocks.session.prompt import SessionPrompt

        first = ChatMessage(role="user", content="设计 CRM 存储方案")
        latest = ChatMessage(role="user", content="继续写设计文档")
        middle_request = ChatMessage(role="user", content="目录放 workspace/crm。" * 400)
        steps = [
            ChatMessage(
                role="assistant",
                content=f"第 {index} 步思考：" + "先比较客户表和商机表的字段。" * 80
                + "\n[tool: read] output: " + "x" * 3_000,
            )
            for index in range(120)
        ]
        messages = [
            first,
            ChatMessage(role="assistant", content="ok"),
            ChatMessage(role="assistant", content="ok"),
            *steps[:60],
            middle_request,
            *steps[60:],
            latest,
            ChatMessage(role="assistant", content="done"),
        ]
        policy = CompactionPolicy.from_model(128_000, 4_096)
        budget = int(policy.usable_context * 0.5)

        pruned = _prune_chat_messages_for_summary(messages, policy)
        total = sum(SessionPrompt.count_tokens(message.content) for message in pruned)

        assert total <= budget
        assert pruned[0].content == first.content
        assert pruned[-2].content == latest.content
        # Oldest steps go back to one line before any request is touched.
        assert pruned[3].content.startswith("[assistant|tools: read]")

    def test_follow_up_turn_pulled_into_the_tail_is_still_capped(self):
        """On a follow-up turn, anchoring the latest user message drags the
        whole turn into the protected tail; its older steps must still
        shrink or the summary call itself can exceed the model window."""
        from flocks.session.prompt import SessionPrompt

        follow_up = ChatMessage(role="user", content="接着把存储层也设计完")
        steps = [
            ChatMessage(
                role="assistant",
                content=f"第 {index} 步思考：" + "对照客户和商机两张表检查字段。" * 80
                + "\n[tool: read] output: " + "y" * 3_000,
            )
            for index in range(120)
        ]
        messages = [
            ChatMessage(role="user", content="设计 CRM"),
            ChatMessage(role="assistant", content="ok"),
            ChatMessage(role="assistant", content="ok"),
            ChatMessage(role="assistant", content="first turn done"),
            follow_up,
            *steps,
        ]
        policy = CompactionPolicy.from_model(128_000, 4_096)
        budget = int(policy.usable_context * 0.5)

        pruned = _prune_chat_messages_for_summary(messages, policy)
        total = sum(SessionPrompt.count_tokens(message.content) for message in pruned)

        assert total <= budget
        assert pruned[4].content == follow_up.content
        assert pruned[-1].content == steps[-1].content  # the latest step stays verbatim
