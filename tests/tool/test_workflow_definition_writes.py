"""File tools must never publish a broken executable definition."""

import json

import pytest

from flocks.tool import nofollow
from flocks.tool.file.apply_patch import apply_chunks, apply_patch_tool, parse_patch
from flocks.tool.file.edit import edit_tool
from flocks.tool.file.write import write_tool
from flocks.tool.registry import ToolContext
from flocks.workflow.definition_io import WorkflowDefinitionError, parse_definition, write_definition


def definition(code="outputs['ok'] = True"):
    return {"name": "Example", "start": "n1", "nodes": [{"id": "n1", "type": "python", "code": code}], "edges": []}


@pytest.fixture
def target(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "user"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "user" / "data"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "user" / "workspace"))
    path = tmp_path.resolve() / ".flocks" / "plugins" / "workflows" / "demo" / "workflow.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(definition(), indent=2), encoding="utf-8")
    return path


def context(target):
    return ToolContext(session_id="ses_definition", message_id="msg_definition",
                       extra={"workspace_dir": str(target.parents[4])})


def test_bare_hunk_locates_unique_context_away_from_first_line():
    original = '{\n  "name": "old"\n}\n'
    patch = '*** Begin Patch\n*** Update File: workflow.json\n@@\n-  "name": "old"\n+  "name": "new"\n*** End Patch'
    result = apply_chunks(original, parse_patch(patch)[0].chunks)
    assert json.loads(result) == {"name": "new"}


@pytest.mark.parametrize("original", ["a\na\n", "b\n"])
def test_bare_hunk_rejects_ambiguous_or_missing_context(original):
    patch = '*** Begin Patch\n*** Update File: note.txt\n@@\n-a\n+A\n*** End Patch'
    with pytest.raises(ValueError, match="exactly once"):
        apply_chunks(original, parse_patch(patch)[0].chunks)


def test_hunks_are_order_independent_and_must_not_overlap():
    patch = '*** Begin Patch\n*** Update File: note.txt\n@@ -3,1 +3,1 @@\n-c\n+C\n@@ -1,1 +1,1 @@\n-a\n+A\n*** End Patch'
    chunks = parse_patch(patch)[0].chunks
    assert apply_chunks("a\nb\nc\n", chunks) == "A\nb\nC\n"
    with pytest.raises(ValueError, match="overlap"):
        apply_chunks("a\nb\nc\n", [chunks[0], chunks[0]])


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch"])
async def test_invalid_json_is_rejected_without_changing_file(target, tool):
    before = target.read_bytes()
    ctx = context(target)
    if tool == "write":
        result = await write_tool(ctx=ctx, filePath=str(target), content='{"nodes": [')
    elif tool == "edit":
        result = await edit_tool(ctx=ctx, filePath=str(target), edits=[{"oldString": '{\n  "name":', "newString": '  "name":'}])
    else:
        result = await apply_patch_tool(ctx=ctx, patchText=f'*** Begin Patch\n*** Update File: {target}\n@@ -1,1 +1,0 @@\n-{{\n*** End Patch')
    assert not result.success
    assert "Invalid workflow definition" in result.error
    assert target.read_bytes() == before
    assert not list(target.parent.glob("*.last-valid"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write", "edit", "apply_patch"])
async def test_valid_definition_and_previous_version_are_readable(target, tool):
    before = target.read_bytes()
    ctx = context(target)
    if tool == "write":
        result = await write_tool(ctx=ctx, filePath=str(target), content=json.dumps(definition('outputs["text"] = "first\\nsecond"')))
    elif tool == "edit":
        result = await edit_tool(ctx=ctx, filePath=str(target), edits=[{"oldString": '"Example"', "newString": '"Updated"'}])
    else:
        result = await apply_patch_tool(ctx=ctx, patchText=f'*** Begin Patch\n*** Update File: {target}\n@@\n-  "name": "Example",\n+  "name": "Updated",\n*** End Patch')
    assert result.success, result.error
    parse_definition(target.read_text(), "workflow.json", validate_graph=True)
    assert target.with_name("workflow.json.last-valid").read_bytes() == before


@pytest.mark.parametrize("payload", [[], {"start": "missing", "nodes": [], "edges": []}, definition("if broken"), {"group": []}])
def test_invalid_shapes_graphs_python_and_metadata_leave_original(target, payload):
    filename = "meta.json" if isinstance(payload, dict) and "group" in payload else "workflow.json"
    path = target.with_name(filename)
    if filename == "meta.json":
        path.write_text('{"group":"Original"}')
    before = path.read_bytes()
    with pytest.raises(WorkflowDefinitionError):
        write_definition(path, json.dumps(payload), validate_graph=True)
    assert path.read_bytes() == before


def test_atomic_write_failure_keeps_definition_and_cleans_temporary(target, monkeypatch):
    before = target.read_bytes()
    monkeypatch.setattr("flocks.workflow.definition_io.os.fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        write_definition(target, json.dumps(definition()))
    assert target.read_bytes() == before
    assert not list(target.parent.glob("*.tmp"))


def test_stale_write_does_not_touch_definition_or_backup(target):
    before = nofollow.read_text(str(target))
    target.write_text(json.dumps(definition("outputs['newer'] = True")))
    newer = target.read_bytes()
    with pytest.raises(nofollow.StaleFile):
        write_definition(target, json.dumps(definition()), before=before)
    assert target.read_bytes() == newer
    assert not target.with_name("workflow.json.last-valid").exists()


def test_first_write_has_backup_and_repair_preserves_last_valid(target):
    target.unlink()
    write_definition(target, json.dumps(definition()), create_only=True)
    saved = target.with_name("workflow.json.last-valid").read_bytes()
    target.write_text("invalid")
    write_definition(target, json.dumps(definition("outputs['repaired'] = True")))
    assert target.with_name("workflow.json.last-valid").read_bytes() == saved


def test_workflow_bom_and_crlf_are_accepted_and_preserved(target):
    raw = '\ufeff' + json.dumps(definition(), indent=2).replace('\n', '\r\n')
    target.write_bytes(raw.encode())
    before = nofollow.read_text(str(target), newline="")
    nofollow.replace_text(str(target), raw.replace("Example", "Updated"), before=before, read_newline="", write_newline="")
    assert target.read_bytes().startswith(b'\xef\xbb\xbf{\r\n')
    assert b'\r\r\n' not in target.read_bytes()


def test_symlink_backup_cannot_be_followed(target, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("private")
    try:
        target.with_name("workflow.json.last-valid").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    before = target.read_bytes()
    with pytest.raises(nofollow.LinkOnTheWay):
        write_definition(target, json.dumps(definition()))
    assert outside.read_text() == "private"
    assert target.read_bytes() == before


def test_two_writers_from_the_same_snapshot_cannot_overwrite_each_other(target):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    before = nofollow.read_text(str(target))
    barrier = Barrier(2)

    def update(index):
        barrier.wait(timeout=5)
        try:
            write_definition(target, json.dumps(definition(f"outputs['writer'] = {index}")), before=before)
            return "saved"
        except nofollow.StaleFile:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, [1, 2]))
    assert sorted(outcomes) == ["saved", "stale"]
    assert target.with_name("workflow.json.last-valid").read_text() == before.text


def test_atomic_publish_works_with_path_based_platform_fallback(target, monkeypatch):
    monkeypatch.setattr(nofollow, "SUPPORTED", False)
    write_definition(target, json.dumps(definition("outputs['fallback'] = True")))
    assert json.loads(target.read_text())["nodes"][0]["code"] == "outputs['fallback'] = True"


def test_failure_staging_current_file_preserves_original_after_backup_succeeds(target, monkeypatch):
    before = target.read_bytes()
    real_fsync = __import__('os').fsync
    calls = 0

    def fail_second(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("current staging failed")
        return real_fsync(fd)

    monkeypatch.setattr("flocks.workflow.definition_io.os.fsync", fail_second)
    with pytest.raises(OSError, match="current staging failed"):
        write_definition(target, json.dumps(definition()))
    assert target.read_bytes() == before
    assert target.with_name("workflow.json.last-valid").read_bytes() == before
    assert not list(target.parent.glob("*.tmp"))
