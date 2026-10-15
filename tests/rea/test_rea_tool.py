"""The rea_analysis tool Rex calls: the install guide with tier plan and confirmation, status and result cards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.auth.context import AuthUser
from flocks.rea import runtime, service
from flocks.rea.installer import ReaInstaller, ReaInstallJob
from flocks.tool import rea as rea_tool
from flocks.tool.rea import REA_TOOL_DESCRIPTION, rea_analysis_tool
from flocks.tool.registry import ToolContext
from tests.rea.conftest import write_skill

SESSION = "ses_tool_test"


@pytest.fixture
def ctx():
    return ToolContext(session_id=SESSION, message_id="msg_1", agent="rex")


@pytest.fixture(autouse=True)
def quiet_events(monkeypatch):
    monkeypatch.setattr("flocks.server.routes.event.publish_event", AsyncMock())


@pytest.fixture
def as_user(monkeypatch):
    def apply(role: str, user_id: str | None = None):
        user = AuthUser(id=user_id or f"{role}-1", username=role, role=role)
        monkeypatch.setattr("flocks.auth.context.get_current_auth_user", lambda: user)
        return user

    return apply


def _message(role, parts):
    return SimpleNamespace(info=SimpleNamespace(role=role), parts=parts)


def _plan_part():
    return SimpleNamespace(
        type="tool", tool="rea_analysis",
        state=SimpleNamespace(status="completed", metadata={"rea": {"action": "install", "phase": "plan"}}),
    )


def _tasks_part(meta=None):
    """The 「tasks」 card as the conversation keeps it (``meta``: what the tool returned)."""

    rea = meta if meta is not None else {"action": "install", "phase": "tasks", "tasks": []}
    return SimpleNamespace(type="tool", tool="rea_analysis", state=SimpleNamespace(status="completed", metadata={"rea": rea}))


@pytest.fixture
def in_use(monkeypatch):
    """Other conversations using REA: ``busy`` is what the check finds, ``stopped`` what was stopped."""

    state = {"busy": [{"kind": "session", "id": "ses_other", "turn": "msg_b1", "unknown": False}], "stopped": [], "still": []}

    async def using(*, exclude=None):
        assert exclude == SESSION  # the install conversation itself does not count
        return [task for task in state["busy"] if task["id"] not in state["stopped"]]

    async def stop(tasks):
        state["stopped"] += [task["id"] for task in tasks]
        return list(state["still"])

    monkeypatch.setattr(rea_tool, "sessions_using_rea", using)
    monkeypatch.setattr(rea_tool, "stop_tasks", stop)
    return state


def _text(text, synthetic=False):
    return SimpleNamespace(type="text", text=text, synthetic=synthetic)


@pytest.fixture
def conversation(monkeypatch):
    """The messages of SESSION as Message.list_with_parts returns them."""

    from flocks.session.message import Message

    messages: list = []

    async def list_with_parts(session_id, include_archived=False):
        assert session_id == SESSION
        return list(messages)

    monkeypatch.setattr(Message, "list_with_parts", list_with_parts)
    return messages


@pytest.fixture
def jobs(monkeypatch):
    """ReaInstaller.start/wait without running anything; ``result`` is the finished job."""

    state = {"started": [], "result": None}

    async def start(tier="light", *, user_id=None, session_id=None):
        state["started"].append((tier, user_id, session_id))
        return state["result"]

    async def wait():
        return state["result"]

    monkeypatch.setattr(ReaInstaller, "start", start)
    monkeypatch.setattr(ReaInstaller, "wait", wait)
    return state


def _job(**changes) -> ReaInstallJob:
    values = {
        "id": "reainst_1", "status": "succeeded", "stage": "done", "progress": 100, "tier": "light",
        "startedAt": 1, "finishedAt": 2, "registration": "written",
        "components": {
            "skill": {"key": "skill", "status": "installed", "attempts": 2, "detail": "/home/u/.flocks/plugins/skills/x"},
            "rea": {"key": "rea", "status": "reused", "attempts": 1, "detail": "rea-agents@6.4.0"},
        },
        "service": {"ok": True, "server_version": "6.4.0", "tools_expected": 170, "tools_observed": 170,
                    "request_tool": "binary_session", "request_ok": True},
        "validation": {
            "javascript": {"key": "javascript", "status": "passed", "static": "passed", "runtime": "passed",
                           "expected": "calc(5) = 22", "actual": "22",
                           "sample": {"path": "/home/u/.flocks/data/rea/validation/r/javascript/index.js", "sha256": "a" * 64},
                           "calls": [{"tool": "rea_analyze_javascript_application", "args": {}, "ok": True, "summary": "{}"}],
                           "notes": []},
            "dotnet": {"key": "dotnet", "status": "passed", "static": "passed", "runtime": "incomplete",
                       "expected": "Calc(5) = 22", "actual": "", "sample": {}, "calls": [], "notes": ["no .NET runtime"]},
            "archive": {"key": "archive", "status": "passed", "static": "passed", "runtime": "not_applicable",
                        "expected": "a, b", "actual": "a, b", "sample": {}, "calls": [], "notes": []},
        },
    }
    values.update(changes)
    return ReaInstallJob(**values)


def test_description_puts_the_install_guide_on_this_tool():
    text = REA_TOOL_DESCRIPTION
    assert "REA 安装指引" in text and 'call action="install" right away' in text
    assert "never run the guide's commands" in text
    assert "轻量档" in text and "完整档" in text and "confirmed=true" in text
    assert "do not ask for files or analysis goals" in text
    assert "tool_search" in text and "rea_<tool>" in text
    assert "when it is enabled" in text  # the skill is optional
    assert "never look for them on the host, in other conversations' uploads" in text
    assert "Do not call it for terminology questions" in text


@pytest.mark.asyncio
async def test_members_cannot_install(ctx, as_user, jobs):
    as_user("member")
    result = await rea_analysis_tool(ctx, action="install")
    assert result.success is False and "administrator" in result.error
    assert result.metadata["rea"]["errorCode"] == "forbidden"
    assert jobs["started"] == []


@pytest.mark.asyncio
async def test_plan_mode_cannot_install(ctx, as_user, jobs):
    as_user("admin")
    ctx.extra["execution_mode"] = "plan"
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.success is False and "Build mode" in result.error
    assert result.metadata["rea"]["errorCode"] == "plan_mode"
    assert jobs["started"] == []


@pytest.mark.asyncio
async def test_no_install_while_uninstalling(ctx, as_user, jobs, monkeypatch):
    from flocks.rea.uninstaller import ReaUninstaller

    as_user("admin")
    monkeypatch.setattr(ReaUninstaller, "is_running", classmethod(lambda cls: True))
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.success is False and result.metadata["rea"]["errorCode"] == "uninstalling"


@pytest.mark.asyncio
async def test_the_first_install_call_returns_both_tier_plans(ctx, as_user, jobs, fake_toolchain, fake_mcp):
    as_user("admin")
    result = await rea_analysis_tool(ctx, action="install")
    assert result.success is True and jobs["started"] == []
    meta = result.metadata["rea"]
    assert meta["phase"] == "plan" and meta["tier"] is None
    light, full = meta["plans"]["light"], meta["plans"]["full"]
    assert set(light["components"]) == {"skill", "rea"}
    assert set(full["components"]) == {"skill", "rea", "jdk", "ghidra"}
    assert light["registration"] == "write" and light["needsConfirmation"] is True
    assert "mcp" in light["writes"] and "skill" in light["writes"]
    assert full["components"]["jdk"]["action"] in ("blocked", "install")
    assert "Ask in one short sentence which tier" in result.output and "Do not ask for analysis material" in result.output
    # This conversation is now the account's install conversation.
    assert service.recent_install_session(service._user_key(AuthUser(id="admin-1", username="a", role="admin"))) == SESSION


@pytest.mark.asyncio
async def test_confirmed_needs_a_user_answer_after_the_plan(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp):
    as_user("admin")
    jobs["result"] = _job()
    # Rex claims a confirmation, but the user never saw a plan.
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.metadata["rea"]["phase"] == "plan" and jobs["started"] == []

    # The plan was shown, but nobody answered yet (synthetic text does not count).
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text("continue", synthetic=True)]))
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.metadata["rea"]["phase"] == "plan" and jobs["started"] == []

    # Without confirmed=true the plan comes back even after the confirmation.
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["light"][0])]))
    result = await rea_analysis_tool(ctx, action="install", tier="light")
    assert result.metadata["rea"]["phase"] == "plan" and jobs["started"] == []

    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.success is True
    assert jobs["started"] == [("light", "admin-1", SESSION)]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "先不要安装，解释一下两档的区别",
    "安装轻量档",  # free text is not the card's confirmation
    rea_tool.CONFIRM_PHRASES["light"][0],  # the other tier
])
async def test_only_the_cards_confirmation_of_that_tier_counts(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp, reply):
    as_user("admin")
    jobs["result"] = _job(tier="full")
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(reply)]))
    result = await rea_analysis_tool(ctx, action="install", tier="full", confirmed=True)
    assert result.metadata["rea"]["phase"] == "plan" and jobs["started"] == []


@pytest.mark.asyncio
async def test_a_confirmation_the_user_wrote_after_is_stale(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp):
    as_user("admin")
    jobs["result"] = _job(tier="full")
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["full"][1])]))
    conversation.append(_message("assistant", [_text("好的")]))
    conversation.append(_message("user", [_text("等一下，先别装")]))
    result = await rea_analysis_tool(ctx, action="install", tier="full", confirmed=True)
    assert result.metadata["rea"]["phase"] == "plan" and jobs["started"] == []

    # The retry button sends the confirmation again: that is consent for this install.
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["full"][0])]))
    result = await rea_analysis_tool(ctx, action="install", tier="full", confirmed=True)
    assert jobs["started"] == [("full", "admin-1", SESSION)]


@pytest.mark.asyncio
async def test_a_file_the_user_sends_after_the_confirmation_ends_it(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp):
    as_user("admin")
    jobs["result"] = _job(tier="full")
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["full"][0])]))
    conversation.append(_message("user", [SimpleNamespace(type="file", filename="sample.bin", synthetic=False)]))
    result = await rea_analysis_tool(ctx, action="install", tier="full", confirmed=True)
    assert result.metadata["rea"]["phase"] == "plan" and jobs["started"] == []


@pytest.mark.asyncio
async def test_conversations_using_rea_are_counted_and_stopped_only_after_the_card(
    ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp, in_use,
):
    as_user("admin")
    jobs["result"] = _job()
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["light"][0])]))
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    meta = result.metadata["rea"]
    assert meta["phase"] == "tasks" and [sorted(task) for task in meta["tasks"]] == [["ref", "unknown"]]
    assert "ses_other" not in str(meta) and "msg_b1" not in str(meta)  # an opaque reference only
    assert "1 other conversation(s)" in result.output and "Nothing was changed yet" in result.output
    assert jobs["started"] == [] and in_use["stopped"] == []

    # Any other reply leaves them running.
    conversation += [_message("assistant", [_tasks_part(meta)]), _message("user", [_text("它们什么时候结束？")])]
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.metadata["rea"]["phase"] in ("tasks", "plan") and in_use["stopped"] == [] and jobs["started"] == []

    # The card's choice stops them, then the install goes on (it is also consent to the tier's writes).
    conversation += [_message("assistant", [_tasks_part(meta)]), _message("user", [_text(rea_tool.STOP_PHRASES["light"][0])])]
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert in_use["stopped"] == ["ses_other"]
    assert jobs["started"] == [("light", "admin-1", SESSION)] and result.success is True


@pytest.mark.asyncio
async def test_a_repair_that_writes_nothing_still_asks_before_interrupting(
    ctx, as_user, jobs, conversation, monkeypatch, in_use,
):
    as_user("admin")
    jobs["result"] = _job()

    async def plan(tier):
        return {"tier": tier, "components": {}, "registration": "reuse", "writes": [], "blockers": [], "needsConfirmation": False}

    monkeypatch.setattr(rea_tool, "_tier_plan", plan)
    monkeypatch.setattr(runtime, "recorded_tier", lambda: "light")
    conversation.append(_message("user", [_text("继续排查 REA 插件的安装或验证问题")]))
    result = await rea_analysis_tool(ctx, action="install", confirmed=True)
    meta = result.metadata["rea"]
    assert meta["phase"] == "tasks" and jobs["started"] == []

    in_use["still"] = ["ses_other"]
    conversation += [_message("assistant", [_tasks_part(meta)]), _message("user", [_text(rea_tool.STOP_PHRASES["light"][1])])]
    result = await rea_analysis_tool(ctx, action="install", confirmed=True)
    failed = result.metadata["rea"]
    assert result.success is False and failed["errorCode"] == "tasks_running"
    assert failed["tier"] == "light"  # a retry from this card keeps the tier
    assert failed["tasks"] == meta["tasks"]
    assert "1 other conversation(s) did not stop" in result.error and jobs["started"] == []  # nothing changed meanwhile


@pytest.mark.asyncio
async def test_only_the_conversations_the_card_listed_are_stopped(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp, in_use):
    as_user("admin")
    jobs["result"] = _job()
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["light"][0])]))
    meta = (await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)).metadata["rea"]
    # While the card waits, A finished and B started using REA.
    in_use["busy"] = [{"kind": "session", "id": "ses_b", "turn": "msg_c1", "unknown": False}]
    conversation += [_message("assistant", [_tasks_part(meta)]), _message("user", [_text(rea_tool.STOP_PHRASES["light"][0])])]
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert result.metadata["rea"]["phase"] == "tasks"  # listed again, to be confirmed again
    assert result.metadata["rea"]["tasks"] != meta["tasks"]
    assert in_use["stopped"] == [] and jobs["started"] == []


@pytest.mark.asyncio
async def test_a_new_reply_in_a_listed_conversation_is_confirmed_again(
    ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp, in_use,
):
    as_user("admin")
    jobs["result"] = _job()
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["light"][0])]))
    meta = (await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)).metadata["rea"]
    # The card listed B's reply to its message msg_b1; meanwhile B's user sent msg_b2 and REA runs for that.
    in_use["busy"] = [{"kind": "session", "id": "ses_other", "turn": "msg_b2", "unknown": False}]
    conversation += [_message("assistant", [_tasks_part(meta)]), _message("user", [_text(rea_tool.STOP_PHRASES["light"][0])])]
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    relisted = result.metadata["rea"]
    assert relisted["phase"] == "tasks" and relisted["tasks"] != meta["tasks"]
    assert in_use["stopped"] == [] and jobs["started"] == []

    # Confirming the new card stops that reply.
    conversation += [_message("assistant", [_tasks_part(relisted)]), _message("user", [_text(rea_tool.STOP_PHRASES["light"][0])])]
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    assert in_use["stopped"] == ["ses_other"] and result.success is True


@pytest.mark.asyncio
async def test_conversations_are_counted_never_named(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp, in_use):
    as_user("admin")
    jobs["result"] = _job()
    # Even if the check handed over titles, the conversation would not keep them.
    in_use["busy"] = [
        {"kind": "session", "id": "ses_mine", "turn": "m1:s1", "title": "我的分析", "unknown": False},
        {"kind": "session", "id": "ses_private", "turn": "m2:s2", "title": "别人的分析", "unknown": False},
    ]
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["light"][0])]))
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    tasks = result.metadata["rea"]["tasks"]
    assert [task["unknown"] for task in tasks] == [False, False]
    assert all(sorted(task) == ["ref", "unknown"] for task in tasks)
    assert "2 other conversation(s)" in result.output
    kept = f"{result.output} {result.metadata}"
    assert "我的分析" not in kept and "别人的分析" not in kept


@pytest.mark.asyncio
async def test_a_busy_conversation_that_cannot_be_named_is_never_stopped(
    ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp, in_use,
):
    as_user("admin")
    jobs["result"] = _job()
    conversation += [_message("user", [_text("REA 安装指引…")]), _message("assistant", [_plan_part()])]
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["light"][0])]))
    meta = (await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)).metadata["rea"]
    assert meta["phase"] == "tasks"  # ses_other, readable
    # Before the card is answered, the same conversation can no longer be read: nothing tells whether
    # it still runs the reply the card listed, so the card's choice does not cover it.
    in_use["busy"] = [{"kind": "session", "id": "ses_other", "turn": "", "title": "分析样本 B", "unknown": True}]
    conversation += [_message("assistant", [_tasks_part(meta)]), _message("user", [_text(rea_tool.STOP_PHRASES["light"][0])])]
    result = await rea_analysis_tool(ctx, action="install", tier="light", confirmed=True)
    failed = result.metadata["rea"]
    assert result.success is False and failed["errorCode"] == "tasks_unreadable" and failed["tier"] == "light"
    assert failed["tasks"][0]["unknown"] is True and "title" not in failed["tasks"][0]
    assert "1 busy conversation(s) whose state could not be read" in result.error and "分析样本 B" not in result.error
    assert in_use["stopped"] == [] and jobs["started"] == []





def test_confirmation_phrases_are_what_the_cards_send():
    import json
    from pathlib import Path

    locales = Path(__file__).resolve().parents[2] / "webui" / "src" / "locales"
    for index, locale in enumerate(("zh-CN", "en-US")):
        texts = json.loads((locales / locale / "session.json").read_text(encoding="utf-8"))["rea"]
        for tier in ("light", "full"):
            assert rea_tool.CONFIRM_PHRASES[tier][index] == texts["confirm"][tier]
            assert rea_tool.STOP_PHRASES[tier][index] == texts["stop"][tier]


@pytest.mark.asyncio
async def test_nothing_to_write_validates_without_asking(ctx, as_user, jobs, fake_toolchain, fake_mcp, fake_skill_source):
    from flocks.config.config_writer import ConfigWriter
    from flocks.rea import components

    as_user("admin")
    write_skill(runtime.installed_skill_dir())
    await components.ensure_rea_cli()
    ConfigWriter.add_mcp_server(runtime.MCP_SERVER_NAME, runtime.mcp_server_config("light"))
    runtime.write_install_record({"version": runtime.REA_VERSION, "tier": "light", "verification": {"ok": False}})
    jobs["result"] = _job()
    result = await rea_analysis_tool(ctx, action="install")  # repair: the recorded tier, nothing written
    assert result.success is True
    assert jobs["started"] == [("light", "admin-1", SESSION)]


@pytest.mark.asyncio
async def test_a_running_install_is_joined_not_planned(ctx, as_user, jobs, monkeypatch):
    as_user("admin", "admin-2")
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
    jobs["result"] = _job()
    result = await rea_analysis_tool(ctx, action="install")
    assert result.success is True and len(jobs["started"]) == 1


@pytest.mark.asyncio
async def test_success_reports_components_registration_and_capabilities(ctx, as_user, jobs, monkeypatch):
    as_user("admin")
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
    jobs["result"] = _job()
    result = await rea_analysis_tool(ctx, action="install")
    out = result.output
    assert "light tier (轻量档) validation passed" in out
    assert "skill reverse-engineer-anything: installed, 2 attempts" in out
    assert 'MCP service "rea": written (registered once' in out
    assert "170/170 tools listed" in out and "binary_session succeeded" in out
    assert "JavaScript: passed (static analysis passed, runtime check passed" in out
    assert ".NET: passed (static analysis passed, runtime check incomplete" in out
    assert "Native ELF/PE decompilation: not verified" in out
    assert "ask for the target" in out
    meta = result.metadata["rea"]
    assert meta["phase"] == "done" and meta["ok"] is True and meta["tier"] == "light"
    assert meta["unverified"] == ["native", "dotnet:runtime"]
    javascript = meta["capabilities"]["javascript"]
    assert javascript["tools"] == ["rea_analyze_javascript_application"]
    assert javascript["calls"] == [{"tool": "rea_analyze_javascript_application", "ok": True}]  # no returned text
    assert javascript["sample"] == {"name": "index.js", "sha256": "a" * 64, "format": None}
    assert javascript["actual"] == "22" and javascript["hiddenDetails"] == 0
    # A note that is not one of the validation's own texts stays in the plugin square.
    assert meta["capabilities"]["dotnet"]["notes"] == [] and meta["capabilities"]["dotnet"]["hiddenDetails"] == 1
    assert "1 more detail(s) only in the plugin square" in out
    assert meta["components"]["skill"] == {"status": "installed", "attempts": 2, "blocker": None, "evidence": None}
    assert "/home/u" not in str(meta)


@pytest.mark.asyncio
async def test_failure_explains_blockers_and_retry(ctx, as_user, jobs, monkeypatch):
    from pathlib import Path

    as_user("admin")
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
    jobs["result"] = _job(
        status="failed", stage="components", tier="full", errorCode="blocked",
        errorDetail=f"jdk: Root is required; at {Path.home()}/x",
        blockers=[{"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}],
        components={"jdk": {"key": "jdk", "status": "blocked", "attempts": 1,
                            "blocker": {"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}}},
        service=None, validation={},
    )
    result = await rea_analysis_tool(ctx, action="install")
    assert result.success is False
    assert "run `dnf install -y java-21-openjdk-devel` as root" in result.error
    assert 'call action="install" again' in result.error
    assert "no first install was recorded" in result.error
    meta = result.metadata["rea"]
    assert meta["phase"] == "failed" and meta["errorCode"] == "blocked"
    assert meta["blockers"] == [{"code": "jdk_needs_root", "command": "dnf install -y java-21-openjdk-devel"}]
    # The installer's own words stay in the plugin square.
    assert "errorDetail" not in meta and "Root is required; at" not in result.error
    assert str(Path.home()) not in f"{result.error} {meta}"


@pytest.mark.asyncio
@pytest.mark.parametrize("detail, expected", [
    ("node:unsupported:v18.2.0", ("node", "unsupported")),
    ("storage:not_writable:/srv/flocks/data", ("storage", "not_writable")),
    ("npx:missing", ("npx", "missing")),
    ("something else: /srv/x", None),
])
async def test_a_failed_prerequisite_is_named_by_its_code(ctx, as_user, jobs, monkeypatch, detail, expected):
    as_user("admin")
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
    jobs["result"] = _job(status="failed", stage="check", errorCode="environment", errorDetail=detail,
                          components={}, service=None, validation={})
    result = await rea_analysis_tool(ctx, action="install")
    failure = result.metadata["rea"]["environment"]
    assert (None if failure is None else (failure["check"], failure["code"])) == expected
    assert "/srv/" not in f"{result.error} {result.metadata}" and "v18.2.0" not in result.error
    if expected:
        assert "Failed prerequisite:" in result.error


@pytest.mark.asyncio
async def test_what_the_install_conversation_keeps_has_no_host_paths(ctx, as_user, jobs, monkeypatch):
    as_user("admin")
    monkeypatch.setattr(ReaInstaller, "is_running", classmethod(lambda cls: True))
    blocker = {"code": "ghidra_decompiler_missing", "platform": "linux_arm_64",
               "command": "cd /srv/tools/ghidra/support/gradle && ./gradlew buildNatives"}
    jobs["result"] = _job(
        status="failed", stage="validate", tier="full", errorCode="validation_failed",
        errorDetail="native: open_binary failed for /srv/flocks/data/rea/validation/r/native/calc",
        blockers=[blocker],
        components={"ghidra": {"key": "ghidra", "status": "blocked", "attempts": 1, "detail": "/srv/tools/ghidra",
                               "blocker": blocker}},
        service=None,
        validation={"native": {"key": "native", "status": "failed", "static": "failed", "runtime": "incomplete",
                               "actual": "open failed: '/srv/my private tools/out'",
                               "calls": [{"tool": "rea_open_binary", "ok": False,
                                          "summary": '{"path":"\\\\\\\\fileserver\\\\share\\\\calc"}'}],
                               "notes": ["sample at /srv/flocks/data/rea/validation/r/native/calc",
                                         "编译失败：'/srv/my private tools/cc' exited 1"]}},
    )
    result = await rea_analysis_tool(ctx, action="install")
    kept = f"{result.error} {result.metadata}"
    assert "/srv/" not in kept and "<path>" in kept
    # Free text of other programs is not kept at all, so no path shape slips through.
    assert "private tools" not in kept and "fileserver" not in kept and "open_binary failed" not in kept
    assert "编译样例 C 程序失败" in kept  # what happened, in the validation's own words
    # Two notes, the output and the failed call's reply are left out: the card says how many.
    assert result.metadata["rea"]["capabilities"]["native"]["hiddenDetails"] == 4
    assert "./gradlew buildNatives" in kept and "linux_arm_64" in kept  # the action stays readable
    assert "plugin square" in result.error  # where the administrator finds the full paths


@pytest.mark.asyncio
async def test_members_get_the_verdicts_without_the_evidence(ctx, as_user, monkeypatch):
    runtime.write_install_record({
        "version": runtime.REA_VERSION, "tier": "light",
        "verification": {"ok": True, "capabilities": {
            "javascript": {"status": "passed", "static": "passed", "runtime": "passed", "expected": "calc(5) = 22",
                           "actual": "22", "calls": [{"tool": "rea_analyze_javascript_application"}],
                           "notes": ["sample at /srv/flocks/data/rea/validation/r/javascript/index.js"]},
        }},
    })
    monkeypatch.setattr(service, "install_state", lambda: "ready")
    as_user("member")
    member = (await rea_analysis_tool(ctx, action="status")).output
    assert "JavaScript: passed (static analysis passed, runtime check passed)" in member
    assert "sample at" not in member and "/srv/" not in member
    as_user("admin")
    admin = (await rea_analysis_tool(ctx, action="status")).output
    assert "sample at" not in admin and "/srv/" not in admin
    assert "1 more detail(s) only in the plugin square" in admin


@pytest.mark.asyncio
async def test_status_texts(ctx, as_user, monkeypatch):
    as_user("admin")
    for state, phrase in (
        ("not_installed", 'call action="install" now'),
        ("installing", "Do not start another install"),
        ("validating", "Do not start another install"),
        ("uninstalling", "Do not start an analysis"),
        ("uninstall_failed", "retry the uninstall"),
        ("install_failed", "did not pass"),
    ):
        monkeypatch.setattr(service, "install_state", lambda state=state: state)
        result = await rea_analysis_tool(ctx, action="status")
        assert phrase in result.output, state
        assert result.metadata["rea"] == {"action": "status", "state": state, "canInstall": True}

    monkeypatch.setattr(service, "install_state", lambda: "degraded")
    asked = []

    def reasons(*, details=True):
        asked.append(details)
        return ['MCP service "rea" is failed'] + (["(/srv/x)"] if details else [])

    monkeypatch.setattr(service, "degraded_reasons", reasons)
    result = await rea_analysis_tool(ctx, action="status")
    assert "Repair it first" in result.output and 'is failed' in result.output
    assert asked == [False] and "/srv/x" not in result.output and "plugin square" in result.output

    as_user("member")
    result = await rea_analysis_tool(ctx, action="status")
    assert "administrator has to repair" in result.output and "is failed" not in result.output
    monkeypatch.setattr(service, "install_state", lambda: "not_installed")
    result = await rea_analysis_tool(ctx, action="status")
    assert "not an administrator" in result.output


@pytest.mark.asyncio
async def test_status_when_ready_lists_the_validated_capabilities(ctx, as_user, monkeypatch):
    as_user("member")
    runtime.write_install_record({
        "version": runtime.REA_VERSION, "tier": "light",
        "verification": {"ok": True, "capabilities": {
            "javascript": {"status": "passed", "static": "passed", "runtime": "passed", "expected": "calc(5) = 22",
                           "actual": "22", "calls": [{"tool": "rea_analyze_javascript_application"}], "notes": []},
        }},
    })
    monkeypatch.setattr(service, "install_state", lambda: "ready")
    result = await rea_analysis_tool(ctx, action="status")
    assert "light tier" in result.output and "JavaScript: passed" in result.output
    assert "Native ELF/PE decompilation is not verified" in result.output
    # The model repeats what it reads: no tool parameters for it to pass on to the user.
    assert 'tier="full"' not in result.output and "完整档" in result.output
    assert "placeholders in the prompt are not input" in result.output
    assert "do not search the host or other conversations" in result.output


@pytest.mark.asyncio
async def test_report_carries_conclusion_evidence_and_the_three_kinds_of_statement(ctx):
    result = await rea_analysis_tool(
        ctx,
        action="report",
        title="样本分析",
        summary="样本把 C2 地址做了 XOR 0x5A 混淆。",
        entries=[{"label": "关键原理", "value": "decode() 对 .rodata 的 24 字节异或 0x5A"}],
        facts=["运行原程序输出 c2.example.invalid"],
        inferences=["可能还有备用地址"],
        open_items=["未在 Windows 上运行"],
    )
    assert result.success is True
    meta = result.metadata["rea"]
    assert meta["action"] == "report" and meta["title"] == "样本分析"
    assert meta["entries"] == [{"label": "关键原理", "value": "decode() 对 .rodata 的 24 字节异或 0x5A"}]
    assert meta["facts"] == ["运行原程序输出 c2.example.invalid"]
    assert meta["inferences"] == ["可能还有备用地址"]
    assert meta["openItems"] == ["未在 Windows 上运行"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"entries": []},
        {"entries": [{"label": "", "value": "x"}]},
        {"entries": [{"label": "a", "value": "b"}] * 13},
        {"entries": [{"label": "a", "value": "b"}], "facts": "not a list"},
    ],
)
async def test_invalid_report_is_returned_to_rex(ctx, kwargs):
    result = await rea_analysis_tool(ctx, action="report", **kwargs)
    assert result.success is False and result.metadata == {"code": "invalid_report"}


@pytest.mark.asyncio
async def test_unknown_action_and_tier(ctx, as_user, jobs):
    result = await rea_analysis_tool(ctx, action="verify")
    assert result.success is False and "Unsupported action" in result.error
    as_user("admin")
    result = await rea_analysis_tool(ctx, action="install", tier="huge")
    assert result.success is False and "tier must be one of" in result.error


def test_tool_parameters_expose_tier_and_confirmation():
    from flocks.tool.registry import ToolRegistry

    tool = ToolRegistry.get(rea_tool.REA_TOOL_NAME)
    names = [parameter.name for parameter in tool.info.parameters]
    assert names[:3] == ["action", "tier", "confirmed"]
    action = tool.info.parameters[0]
    assert action.enum == ["install", "status", "report"]


@pytest.mark.asyncio
async def test_a_retry_keeps_the_tier_of_the_last_attempt(ctx, as_user, jobs, conversation, fake_toolchain, fake_mcp):
    as_user("admin")
    ReaInstaller._job = _job(status="failed", stage="components", tier="full", errorCode="component_failed")
    conversation += [_message("assistant", [_plan_part()]), _message("user", [_text("继续排查 REA 插件的安装或验证问题")])]
    jobs["result"] = _job(tier="full")
    # Asking to troubleshoot is not consent to write: the plan of that tier comes back.
    result = await rea_analysis_tool(ctx, action="install", confirmed=True)
    assert result.metadata["rea"]["phase"] == "plan" and result.metadata["rea"]["tier"] == "full"
    assert jobs["started"] == []
    # The failed card's retry sends the confirmation of the attempt's tier.
    conversation.append(_message("user", [_text(rea_tool.CONFIRM_PHRASES["full"][0])]))
    result = await rea_analysis_tool(ctx, action="install", confirmed=True)
    assert result.success is True
    assert jobs["started"] == [("full", "admin-1", SESSION)]
