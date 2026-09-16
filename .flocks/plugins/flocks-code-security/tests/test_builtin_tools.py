from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import Tool, ToolContext, ToolRegistry, ToolResult
from flocks_code_security import runtime as runtime_module
from flocks_code_security.builtin_tools import COMMON_TOOL_NAMES
from flocks_code_security.tools import audit_submit_coverage
from flocks.session.message import Message
from flocks_code_security.execution import toolset_digest
from flocks_code_security.projection import AGENT_TOOLS, code_security_tool_projection
from flocks_code_security.runtime import build_runtime


@pytest.fixture
async def audit_workspace(tmp_path, monkeypatch, request):
    ToolRegistry.init()
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text(getattr(request, "param", "user = input()\nprint(user)\n"), encoding="utf-8")
    runtime = build_runtime(tmp_path / "audit")
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator", snapshot_id=snapshot.snapshot_id,
        mode="standard", ruleset_digest="test",
    )
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    from test_tool_pipeline import _complete_threat_model

    await _complete_threat_model(runtime, scan_id=scan_id, snapshot_id=snapshot.snapshot_id)
    unit = runtime.store.create_work_unit(scan_id=scan_id, phase="baseline", role="baseline", paths=["."])
    runtime.store.create_work_attempt(
        work_unit_id=unit, session_id="worker", agent_name="code-security-baseline",
        toolset_digest_value=toolset_digest(AGENT_TOOLS["code-security-baseline"]),
    )
    runtime.store.get_threat_model_for_binding(runtime.store.resolve_binding("worker"))
    ctx = ToolContext("worker", "message", agent="code-security-baseline", extra={
        "agent_execution_session": True,
        "model": {"providerID": None, "modelID": None},
        "turn_callable_tool_names": AGENT_TOOLS["code-security-baseline"],
        "workspace_dir": str(tmp_path),
    })
    ctx._test_transcript = []
    monkeypatch.setattr(Message, "list_with_parts", AsyncMock(return_value=ctx._test_transcript))
    yield runtime, ctx, Path(snapshot.root_path)
    from flocks.storage.storage import Storage

    await Storage.shutdown()


async def _execute(ctx, name, **arguments):
    result = await ToolRegistry.get(name).execute(ctx, **arguments)
    # The native session loop already stores ToolParts; no audit hook is involved.
    ctx._test_transcript.append(SimpleNamespace(
        info=SimpleNamespace(role="assistant", agent=ctx.agent),
        parts=[SimpleNamespace(id=f"part-{len(ctx._test_transcript)}", type="tool", sessionID=ctx.session_id, tool=name, state=SimpleNamespace(
            status="completed" if result.success else "error", input=arguments, output=result.output,
        ))],
    ))
    return result


@pytest.mark.asyncio
async def test_read_only_reads_and_coverage_is_recorded_on_submission(audit_workspace):
    runtime, ctx, root = audit_workspace
    binding = runtime.store.resolve_binding(ctx.session_id)
    first = await _execute(ctx, "read", filePath=str(root / "app.py"), offset=0, limit=1)
    assert first.success, first.error
    assert "audit_source" not in first.metadata
    assert "<audit_source>" not in first.output
    assert runtime.store.list_source_accesses(binding.attempt_id) == []

    partial = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert not partial.success
    reads = runtime.store.list_source_accesses(binding.attempt_id)
    assert [(item["start_line"], item["end_line"]) for item in reads] == [(1, 1)]

    second = await _execute(ctx, "read", filePath=str(root / "app.py"), offset=1, limit=1)
    assert second.success, second.error
    assert runtime.store.list_source_accesses(binding.attempt_id) == reads
    completed = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert completed.success, completed.error
    assert completed.output["records"][0]["state"] == "read_complete"
    assert len(runtime.store.list_source_accesses(binding.attempt_id)) == 2  # no duplicate receipts on retry
    Message.list_with_parts.assert_awaited_with(ctx.session_id, include_archived=True)


@pytest.mark.asyncio
async def test_truncated_lines_do_not_count_as_complete_reads(audit_workspace, monkeypatch):
    import flocks.tool.file.read as read_module

    runtime, ctx, root = audit_workspace
    monkeypatch.setattr(read_module, "get_read_max_line_length", lambda: 4)
    result = await _execute(ctx, "read", filePath=str(root / "app.py"))
    assert result.success, result.error
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert not submitted.success
    binding = runtime.store.resolve_binding(ctx.session_id)
    assert runtime.store.list_source_accesses(binding.attempt_id) == []


@pytest.mark.asyncio
async def test_snapshot_validation_belongs_to_submission_not_read(audit_workspace):
    runtime, ctx, root = audit_workspace
    path = root / "app.py"
    path.chmod(0o600)
    path.write_text("user = input()\neval(user)\n", encoding="utf-8")
    result = await _execute(ctx, "read", filePath=str(path))
    assert result.success, result.error
    assert "eval(user)" in result.output
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert not submitted.success
    assert "mismatch" in submitted.error
    binding = runtime.store.resolve_binding(ctx.session_id)
    assert runtime.store.list_source_accesses(binding.attempt_id) == []


@pytest.mark.asyncio
async def test_discovery_receipts_are_derived_only_at_submission(audit_workspace):
    runtime, ctx, root = audit_workspace
    for name, arguments in (
        ("glob", {"pattern": "**/*.py", "path": str(root)}),
        ("grep", {"pattern": "input", "path": str(root)}),
    ):
        result = await _execute(ctx, name, **arguments)
        assert result.success, result.error
    binding = runtime.store.resolve_binding(ctx.session_id)
    assert runtime.store.list_source_accesses(binding.attempt_id) == []
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert not submitted.success
    accesses = runtime.store.list_source_accesses(binding.attempt_id)
    assert {item["operation"] for item in accesses} == {"inventory", "search"}


@pytest.mark.asyncio
async def test_untrusted_text_and_failed_tool_calls_do_not_prove_reading(audit_workspace):
    runtime, ctx, root = audit_workspace
    result = await _execute(ctx, "read", filePath=str(root / "app.py"))
    assert result.success
    original = ctx._test_transcript[0]
    original.info.role = "user"
    failed = SimpleNamespace(info=SimpleNamespace(role="assistant", agent=ctx.agent), parts=[
        SimpleNamespace(id="failed-read", type="tool", sessionID=ctx.session_id, tool="read", state=SimpleNamespace(
            status="error", input={"filePath": str(root / "app.py")}, output=result.output,
        )),
    ])
    ctx._test_transcript.append(failed)
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert not submitted.success
    binding = runtime.store.resolve_binding(ctx.session_id)
    assert runtime.store.list_source_accesses(binding.attempt_id) == []


@pytest.mark.asyncio
async def test_standard_file_and_command_tools_work_in_scratch(audit_workspace, tmp_path):
    runtime, ctx, root = audit_workspace
    scratch = tmp_path / "scratch"
    calls = [
        ("mkdir", {"path": str(scratch)}),
        ("write", {"filePath": str(scratch / "draft.txt"), "content": "before\n"}),
        ("edit", {"filePath": str(scratch / "draft.txt"), "oldString": "before", "newString": "after"}),
        ("copy", {"sourcePath": str(scratch / "draft.txt"), "targetPath": str(scratch / "copy.txt")}),
        ("move", {"sourcePath": str(scratch / "copy.txt"), "targetPath": str(scratch / "moved.txt")}),
        ("apply_patch", {"patchText": f"*** Begin Patch\n*** Update File: {scratch / 'moved.txt'}\n@@\n-after\n+patched\n*** End Patch"}),
        ("read", {"filePath": str(scratch / "moved.txt")}),
        ("bash", {"command": "pwd", "workdir": str(scratch)}),
        ("delete", {"path": str(scratch / "moved.txt")}),
    ]
    for name, arguments in calls:
        result = await ToolRegistry.get(name).execute(ctx, **arguments)
        assert result.success, (name, result.error)
        if name == "read":
            assert "patched" in result.output
            assert "audit_source" not in result.metadata
    assert not (scratch / "moved.txt").exists()
    assert (root / "app.py").read_text() == "user = input()\nprint(user)\n"
    binding = runtime.store.resolve_binding(ctx.session_id)
    assert runtime.store.list_source_accesses(binding.attempt_id) == []


@pytest.mark.parametrize("agent", list(AGENT_TOOLS))
def test_every_audit_stage_exposes_canonical_standard_tools(agent):
    ToolRegistry.init()
    infos = [ToolRegistry.get(name).info for name in COMMON_TOOL_NAMES]
    projected = code_security_tool_projection(infos, {"agent": agent})
    assert [info.name for info in projected] == list(COMMON_TOOL_NAMES)


def test_projection_rejects_replaced_builtin_handler():
    ToolRegistry.init()
    original = ToolRegistry.get("read")

    async def replacement_handler(_ctx, **_kwargs):
        return ToolResult(success=True)

    replacement = Tool(info=original.info, handler=replacement_handler)
    ToolRegistry.register(replacement)
    try:
        assert code_security_tool_projection([replacement.info], {"agent": "code-security-baseline"}) == []
    finally:
        ToolRegistry.register(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("argument", ["file_path", "FilePath"])
async def test_read_parameter_aliases_establish_coverage(audit_workspace, argument):
    runtime, ctx, root = audit_workspace
    result = await _execute(ctx, "read", **{argument: str(root / "app.py")})
    assert result.success, result.error
    binding = runtime.store.resolve_binding(ctx.session_id)
    assert runtime.store.list_source_accesses(binding.attempt_id) == []
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert submitted.success, submitted.error


@pytest.mark.asyncio
@pytest.mark.parametrize("pass_number", [1, 2])
async def test_compaction_preserves_read_evidence(audit_workspace, monkeypatch, pass_number):
    from flocks.session.lifecycle.compaction import pruning
    from flocks.session.session import Session
    from flocks_code_security.source_receipts import register_source_receipt_preserver
    import flocks.tool.truncation as truncation

    runtime, ctx, root = audit_workspace
    assert (await _execute(ctx, "read", filePath=str(root / "app.py"))).success
    read_part = ctx._test_transcript[0].parts[0]
    original = read_part.state.output
    if pass_number == 1:
        monkeypatch.setattr(truncation, "calculate_max_tool_result_chars", lambda _: 30)
    else:
        for _ in range(5):
            ctx._test_transcript.append(SimpleNamespace(
                info=SimpleNamespace(role="assistant", agent=ctx.agent),
                parts=[SimpleNamespace(id=f"bash-{_}", type="tool", sessionID=ctx.session_id, tool="bash",
                    state=SimpleNamespace(status="completed", input={}, output="x" * 2000))],
            ))
    for index, message in enumerate(ctx._test_transcript):
        message.info.id = str(index)
    monkeypatch.setattr(Message, "list", AsyncMock(return_value=[m.info for m in ctx._test_transcript]))
    monkeypatch.setattr(Message, "parts", AsyncMock(side_effect=lambda mid, sid: ctx._test_transcript[int(mid)].parts))
    monkeypatch.setattr(Message, "_persist_parts", AsyncMock())
    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(return_value=SimpleNamespace(directory=ctx.extra["workspace_dir"], agent=ctx.agent)))
    monkeypatch.setattr(pruning, "_tool_output_preservers", {})
    register_source_receipt_preserver()
    assert await pruning.truncate_oversized_tool_outputs(ctx.session_id, 1000) > 0
    assert read_part.state.output != original
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert submitted.success, submitted.error


@pytest.mark.asyncio
async def test_compaction_ignores_finished_worker_attempt(audit_workspace):
    from flocks_code_security.source_receipts import sync_worker_source_receipts

    runtime, ctx, root = audit_workspace
    assert (await _execute(ctx, "read", filePath=str(root / "app.py"))).success
    binding = runtime.store.resolve_binding(ctx.session_id)
    runtime.store.finish_work_attempt(binding.attempt_id, status="failed")
    await sync_worker_source_receipts(ctx.session_id)
    Message.list_with_parts.assert_not_awaited()
    assert runtime.store.list_source_accesses(binding.attempt_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_workspace", [
    "a\fb\nc\n", "a\u2028b\nc", "a\u0085b\nc", "a\r\nb\r\n", "a\rb\r", "a\f\n\nb",
], indirect=True)
async def test_native_line_numbers_map_to_snapshot_lines(audit_workspace):
    runtime, ctx, root = audit_workspace
    first = await _execute(ctx, "read", filePath=str(root / "app.py"), offset=0, limit=1)
    assert first.success
    partial = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert not partial.success
    rest = await _execute(ctx, "read", filePath=str(root / "app.py"), offset=1)
    assert rest.success
    completed = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert completed.success, completed.error


@pytest.mark.asyncio
async def test_native_path_normalization_is_shared(audit_workspace):
    _, ctx, root = audit_workspace
    read = await _execute(ctx, "read", filePath="  " + str(root / "app.py") + "  ")
    assert read.success
    submitted = await audit_submit_coverage(ctx, dispositions=[{"path": "app.py", "claim": "analyzed"}])
    assert submitted.success, submitted.error


@pytest.mark.asyncio
async def test_ordinary_compaction_does_not_access_audit_database(audit_workspace, monkeypatch):
    from flocks.session.lifecycle.compaction import pruning
    from flocks.session.session import Session
    from flocks_code_security.source_receipts import register_source_receipt_preserver
    from unittest.mock import Mock
    import sqlite3

    runtime, _, _ = audit_workspace
    part = SimpleNamespace(type="tool", state=SimpleNamespace(status="completed", output="x" * 100000))
    monkeypatch.setattr(Message, "list", AsyncMock(return_value=[SimpleNamespace(id="m", role="assistant")]))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[part]))
    monkeypatch.setattr(Message, "_persist_parts", AsyncMock())
    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(return_value=SimpleNamespace(agent="rex")))
    monkeypatch.setattr(pruning, "_tool_output_preservers", {})
    lookup = Mock(side_effect=sqlite3.OperationalError("audit database unavailable"))
    monkeypatch.setattr(runtime.store, "resolve_binding", lookup)
    register_source_receipt_preserver()
    assert await pruning.truncate_oversized_tool_outputs("ordinary-session", 1000) > 0
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_synced_parts_are_not_reparsed_after_store_reopen(audit_workspace, monkeypatch):
    from flocks_code_security.source_receipts import sync_source_receipts
    from flocks_code_security.store import ScanStore
    from unittest.mock import Mock

    runtime, ctx, root = audit_workspace
    assert (await _execute(ctx, "read", filePath=str(root / "app.py"))).success
    binding = runtime.store.resolve_binding(ctx.session_id)
    await sync_source_receipts(ctx, runtime, binding)
    reopened = ScanStore(runtime.store.database_path)
    assert reopened.processed_source_parts(binding.attempt_id) == {ctx._test_transcript[0].parts[0].id}
    validate = Mock(side_effect=AssertionError("old source must not be reread"))
    monkeypatch.setattr(runtime.source, "verified_bytes", validate)
    await sync_source_receipts(ctx, runtime, binding)
    validate.assert_not_called()
    assert len(reopened.list_source_accesses(binding.attempt_id)) == 1


@pytest.mark.asyncio
async def test_receipt_checkpoint_rolls_back_with_failed_insert(audit_workspace):
    runtime, ctx, _ = audit_workspace
    binding = runtime.store.resolve_binding(ctx.session_id)
    with pytest.raises(KeyError):
        runtime.store.record_source_accesses(binding, [{"operation": "read"}], processed_part_ids=["bad-part"])
    assert runtime.store.processed_source_parts(binding.attempt_id) == set()


@pytest.mark.asyncio
async def test_receipt_table_migrates_existing_database(audit_workspace):
    runtime, _, _ = audit_workspace
    with runtime.store._connect() as connection:
        connection.execute("DROP TABLE source_receipt_parts")
        connection.execute("PRAGMA user_version = 14")
    runtime.store.initialize()
    assert runtime.store.processed_source_parts("missing-attempt") == set()


@pytest.mark.asyncio
async def test_concurrent_store_instances_commit_each_part_once(audit_workspace):
    import asyncio
    from flocks_code_security.store import ScanStore

    runtime, ctx, _ = audit_workspace
    binding = runtime.store.resolve_binding(ctx.session_id)
    record = runtime.store.get_snapshot_file(binding.snapshot_id, "app.py")
    access = {"operation": "read", "relative_path": "app.py", "blob_digest": record.blob_digest,
              "start_line": 1, "end_line": 2, "source_part_id": "shared-part"}
    other = ScanStore(runtime.store.database_path)
    await asyncio.gather(*[
        asyncio.to_thread(store.record_source_accesses, binding, [access], processed_part_ids=["shared-part"])
        for store in (runtime.store, other)
    ])
    assert len(other.list_source_accesses(binding.attempt_id)) == 1
