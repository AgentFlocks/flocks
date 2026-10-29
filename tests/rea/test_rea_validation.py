"""Tier validation: real samples, REA tool calls through Flocks' registry, static and runtime results apart."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from flocks.rea import runtime, samples, validation
from flocks.tool.registry import ToolRegistry, ToolResult


class FakeRea:
    """Answers ``rea_<tool>`` calls the way the REA MCP tools do (JSON text)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict = {}

    async def execute(self, name, ctx, **kwargs):
        self.calls.append((name, kwargs))
        answer = self.answers.get(name)
        if callable(answer):
            answer = answer(**kwargs)
        if answer is None:
            return ToolResult(success=False, error=f"Tool not found: {name}")
        if isinstance(answer, ToolResult):
            return answer
        return ToolResult(success=True, output=json.dumps(answer))


@pytest.fixture
def rea(monkeypatch):
    fake = FakeRea()
    monkeypatch.setattr(ToolRegistry, "execute", fake.execute)
    return fake


@pytest.fixture
def runs(monkeypatch):
    """Sample runs: ``outputs[program name]`` is (exit code, stdout)."""

    state = {"outputs": {}, "commands": []}

    async def run(command, *, cwd=None, timeout=60.0):
        state["commands"].append(command)
        key = Path(command[0]).name
        return state["outputs"].get(key, (0, samples.EXPECTED_OUTPUT + "\n"))

    monkeypatch.setattr(validation, "_run", run)
    return state


JS_GRAPH = {
    "graph": {
        "nodes": [
            {"label": "index.js", "identity": {"role_key": "module:index.js"}},
            {
                "label": "calc",
                "identity": {
                    "role_key": "callable:index.js#calc",
                    "module_path": "index.js",
                    "source_range": {"start": {"line": 2, "column": 0}, "end": {"line": 4, "column": 1}},
                },
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_javascript_logic_is_recovered_and_run(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "node_executable", lambda: "/usr/bin/node")
    rea.answers["rea_analyze_javascript_application"] = JS_GRAPH
    result = await validation.validate_javascript(tmp_path, session_id="s1")
    assert (result.status, result.static, result.runtime) == ("passed", "passed", "passed")
    assert result.actual == "22" and result.expected == "calc(5) = 22"
    assert rea.calls == [(
        "rea_analyze_javascript_application",
        {"input_path": str(tmp_path / "javascript"), "format": "directory", "detail": "complete"},
    )]
    assert result.sample["sha256"] == hashlib.sha256(samples.JS_SOURCE.encode()).hexdigest()
    assert [call.tool for call in result.calls] == ["rea_analyze_javascript_application"]
    assert runs["commands"][0][1:] == [str(tmp_path / "javascript" / "index.js"), "5"]


@pytest.mark.asyncio
async def test_javascript_fails_when_the_located_code_is_not_calc(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "node_executable", lambda: None)
    graph = json.loads(json.dumps(JS_GRAPH))
    graph["graph"]["nodes"][1]["identity"]["source_range"] = {"start": {"line": 6}, "end": {"line": 7}}
    rea.answers["rea_analyze_javascript_application"] = graph
    result = await validation.validate_javascript(tmp_path, session_id="s1")
    assert (result.status, result.static, result.runtime) == ("failed", "failed", "incomplete")

    rea.answers["rea_analyze_javascript_application"] = {"graph": {"nodes": []}}
    result = await validation.validate_javascript(tmp_path / "again", session_id="s1")
    assert result.static == "failed"


@pytest.mark.asyncio
async def test_a_tool_error_counts_as_a_failed_call(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "node_executable", lambda: "/usr/bin/node")
    rea.answers["rea_analyze_javascript_application"] = {"error": {"code": "provider_unavailable"}}
    result = await validation.validate_javascript(tmp_path, session_id="s1")
    assert result.static == "failed" and result.status == "failed"
    assert result.runtime == "passed"  # the sample itself still runs
    assert result.calls[0].ok is False and "provider_unavailable" in result.calls[0].summary

    rea.answers.pop("rea_analyze_javascript_application")  # tool not registered at all
    result = await validation.validate_javascript(tmp_path / "missing", session_id="s1")
    assert result.calls[0].ok is False and result.static == "failed"


@pytest.mark.asyncio
async def test_truncated_tool_output_is_read_from_the_saved_file(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "node_executable", lambda: None)
    saved = tmp_path / "full-output.json"
    saved.write_text(json.dumps(JS_GRAPH), encoding="utf-8")
    rea.answers["rea_analyze_javascript_application"] = ToolResult(
        success=True, output='{"graph": {"nodes": [ … truncated', truncated=True, metadata={"output_path": str(saved)},
    )
    result = await validation.validate_javascript(tmp_path / "t", session_id="s1")
    assert result.static == "passed"


DOTNET_MEMBERS = {
    "normalized_result": {
        "methods": [
            {"name": "Main", "declaring_type": "ReaValidation.Sample", "signature": {}, "body": {}},
            {
                "name": "Calc",
                "declaring_type": "ReaValidation.Sample",
                "signature": {"return_type": "i4", "parameter_types": ["i4"]},
                "body": {"opcode_counts": dict(samples.DOTNET_CALC_OPCODES)},
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_dotnet_calc_is_found_in_the_shipped_assembly(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "dotnet_executable", lambda: None)
    rea.answers["rea_inspect_managed_artifact"] = {"ok": True}
    rea.answers["rea_inspect_managed_members"] = DOTNET_MEMBERS
    result = await validation.validate_dotnet(tmp_path, session_id="s1")
    assert (result.status, result.static, result.runtime) == ("passed", "passed", "incomplete")
    assert result.sample["sha256"] == samples.DOTNET_ASSEMBLY_SHA256
    assert any("没有 .NET 运行时" in note for note in result.notes)

    monkeypatch.setattr(runtime, "dotnet_executable", lambda: "/usr/local/bin/dotnet")
    result = await validation.validate_dotnet(tmp_path / "with-runtime", session_id="s1")
    assert result.runtime == "passed" and result.actual == "22"


@pytest.mark.asyncio
async def test_dotnet_other_logic_fails(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "dotnet_executable", lambda: None)
    members = json.loads(json.dumps(DOTNET_MEMBERS))
    members["normalized_result"]["methods"][1]["body"]["opcode_counts"] = {"ldarg.0": 1, "ret": 1}
    rea.answers["rea_inspect_managed_artifact"] = {"ok": True}
    rea.answers["rea_inspect_managed_members"] = members
    result = await validation.validate_dotnet(tmp_path, session_id="s1")
    assert result.static == "failed"


def test_the_shipped_assembly_matches_its_recorded_checksum():
    assert hashlib.sha256(samples.dotnet_assembly()).hexdigest() == samples.DOTNET_ASSEMBLY_SHA256


def _extract_answer(files: dict[str, str], output_root: Path):
    def answer(**_kwargs):
        artifacts = []
        for name, text in files.items():
            path = output_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            artifacts.append({"relative_path": name, "sha256": hashlib.sha256(text.encode()).hexdigest()})
        return {"normalized_result": {"output_root": str(output_root), "artifacts": artifacts}}
    return answer


@pytest.mark.asyncio
async def test_archive_listing_contents_and_checksums_match(rea, runs, tmp_path):
    output_root = tmp_path / "extracted"
    rea.answers["rea_open_binary"] = {"result": {"format": "zip"}}
    rea.answers["rea_extract_artifact"] = _extract_answer(samples.ARCHIVE_FILES, output_root)
    rea.answers["rea_close_binary"] = {"ok": True}
    result = await validation.validate_archive(tmp_path, session_id="s1")
    assert (result.status, result.static, result.runtime) == ("passed", "passed", "not_applicable")
    assert [name for name, _ in rea.calls] == ["rea_open_binary", "rea_extract_artifact", "rea_close_binary"]
    assert result.actual == "docs/readme.txt, manifest.txt, src/sample.js"
    assert output_root.exists()  # REA's own temporary folder: not ours to delete


@pytest.mark.asyncio
async def test_nothing_is_deleted_where_rea_points(rea, runs, tmp_path):
    # REA names the extraction folder; a wrong answer (a folder with the user's own files of the same
    # names, reached through a symlink) must not cost any of them.
    real = tmp_path / "user-project" / "reports"
    (real / "src").mkdir(parents=True)
    (real / "keep.txt").write_text("mine", encoding="utf-8")
    (real / "manifest.txt").write_text("the user's manifest", encoding="utf-8")
    (real / "src" / "sample.js").write_text("the user's code", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    artifacts = [
        {"relative_path": name, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        for name, text in samples.ARCHIVE_FILES.items()
    ]
    rea.answers["rea_open_binary"] = {"result": {"format": "zip"}}
    rea.answers["rea_extract_artifact"] = {"normalized_result": {"output_root": str(link), "artifacts": artifacts}}
    rea.answers["rea_close_binary"] = {"ok": True}
    result = await validation.validate_archive(tmp_path, session_id="s1")
    assert result.status == "failed" and any("内容不一致" in note for note in result.notes)
    assert (real / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert (real / "manifest.txt").read_text(encoding="utf-8") == "the user's manifest"
    assert (real / "src" / "sample.js").read_text(encoding="utf-8") == "the user's code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [
        {k: v for k, v in samples.ARCHIVE_FILES.items() if k != "docs/readme.txt"},
        {**samples.ARCHIVE_FILES, "extra.txt": "x"},
        {**samples.ARCHIVE_FILES, "src/sample.js": "changed"},
    ],
    ids=["missing", "extra", "changed"],
)
async def test_archive_differences_fail(rea, runs, tmp_path, files):
    rea.answers["rea_open_binary"] = {"result": {"format": "zip"}}
    rea.answers["rea_extract_artifact"] = _extract_answer(files, tmp_path / "extracted")
    rea.answers["rea_close_binary"] = {"ok": True}
    result = await validation.validate_archive(tmp_path, session_id="s1")
    assert result.status == "failed" and result.notes


@pytest.mark.asyncio
async def test_native_without_a_compiler_is_unfinished(rea, runs, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "c_compiler", lambda: None)
    result = await validation.validate_native(tmp_path, session_id="s1")
    assert (result.status, result.static, result.runtime) == ("incomplete", "incomplete", "incomplete")
    assert rea.calls == []


def _compiled(monkeypatch, runs, tmp_path):
    monkeypatch.setattr(runtime, "c_compiler", lambda: "/usr/bin/cc")

    original = runs["outputs"]

    async def run(command, *, cwd=None, timeout=60.0):
        runs["commands"].append(command)
        if Path(command[0]).name == "cc":
            Path(command[command.index("-o") + 1]).write_bytes(b"\x7fELF fake")
            return 0, ""
        return original.get(Path(command[0]).name, (0, "22\n"))

    monkeypatch.setattr(validation, "_run", run)


@pytest.mark.asyncio
async def test_native_decompilation_matches_the_source(rea, runs, tmp_path, monkeypatch):
    _compiled(monkeypatch, runs, tmp_path)
    rea.answers["rea_open_binary"] = {"result": {"format": "elf"}}
    # REA's shape: the decompiled function as text.
    rea.answers["rea_procedure_pseudo_code"] = {"normalized_result": "\nint _calc(int param_1)\n\n{\n  return param_1 * 3 + 7;\n}\n"}
    rea.answers["rea_close_binary"] = {"ok": True}
    result = await validation.validate_native(tmp_path, session_id="s1")
    assert (result.status, result.static, result.runtime) == ("passed", "passed", "passed")
    assert [name for name, _ in rea.calls] == ["rea_open_binary", "rea_procedure_pseudo_code", "rea_close_binary"]
    assert rea.calls[1][1] == {"procedure": "calc"}
    assert result.sample["format"] == "elf"
    assert result.sample["compile"] == "cc -O0 -g"
    assert result.notes[-1] == "伪代码：int _calc(int param_1) { return param_1 * 3 + 7; }"


@pytest.mark.asyncio
async def test_native_needs_both_semantics_and_the_run(rea, runs, tmp_path, monkeypatch):
    _compiled(monkeypatch, runs, tmp_path)
    rea.answers["rea_open_binary"] = {"result": {"format": "elf"}}
    rea.answers["rea_procedure_pseudo_code"] = {"normalized_result": {"pseudo_code": "return param_1 + 1;"}}
    rea.answers["rea_close_binary"] = {"ok": True}
    result = await validation.validate_native(tmp_path, session_id="s1")
    assert result.static == "failed" and result.status == "failed"

    rea.answers["rea_procedure_pseudo_code"] = {"error": {"code": "provider_unavailable", "message": "GHIDRA_INSTALL_DIR"}}
    result = await validation.validate_native(tmp_path / "no-ghidra", session_id="s1")
    assert result.static == "failed"
    # Both spellings of the symbol were tried before giving up.
    assert [args for name, args in rea.calls if name == "rea_procedure_pseudo_code"][-2:] == [
        {"procedure": "calc"}, {"procedure": "_calc"},
    ]

    rea.answers["rea_procedure_pseudo_code"] = {"normalized_result": {"pseudo_code": "return x * 3 + 7;"}}
    runs["outputs"]["calc"] = (0, "21\n")
    result = await validation.validate_native(tmp_path / "wrong-run", session_id="s1")
    assert (result.static, result.runtime, result.status) == ("passed", "failed", "failed")


@pytest.mark.asyncio
async def test_light_tier_passes_on_static_analysis_and_lists_runtime_gaps(monkeypatch):
    async def passed(key, runtime_status):
        async def validator(root, *, session_id):
            return validation.CapabilityResult(key, status="passed", static="passed", runtime=runtime_status)
        return validator

    monkeypatch.setitem(validation._VALIDATORS, "javascript", await passed("javascript", "passed"))
    monkeypatch.setitem(validation._VALIDATORS, "dotnet", await passed("dotnet", "incomplete"))
    monkeypatch.setitem(validation._VALIDATORS, "archive", await passed("archive", "not_applicable"))
    seen = []

    async def on_result(result):
        seen.append(result.key)

    outcome = await validation.run_validation("light", session_id="s1", on_result=on_result)
    assert outcome["ok"] is True and outcome["tier"] == "light"
    assert seen == ["javascript", "dotnet", "archive"]
    assert "native" not in outcome["capabilities"]
    assert Path(outcome["root"]).parent == runtime.validation_root()


@pytest.mark.asyncio
async def test_one_failing_capability_fails_the_tier_without_hiding_the_others(monkeypatch):
    async def crash(root, *, session_id):
        raise RuntimeError("boom")

    async def ok(root, *, session_id):
        return validation.CapabilityResult("archive", status="passed", static="passed", runtime="not_applicable")

    async def ok_js(root, *, session_id):
        return validation.CapabilityResult("javascript", status="passed", static="passed", runtime="passed")

    monkeypatch.setitem(validation._VALIDATORS, "javascript", ok_js)
    monkeypatch.setitem(validation._VALIDATORS, "dotnet", crash)
    monkeypatch.setitem(validation._VALIDATORS, "archive", ok)
    outcome = await validation.run_validation("light", session_id="s1")
    assert outcome["ok"] is False
    assert outcome["capabilities"]["dotnet"]["status"] == "failed"
    assert outcome["capabilities"]["archive"]["status"] == "passed"


def test_full_tier_adds_native_decompilation():
    assert validation.tier_capabilities("light") == ("javascript", "dotnet", "archive")
    assert validation.tier_capabilities("full") == ("javascript", "dotnet", "archive", "native")
    results = {key: {"status": "passed"} for key in ("javascript", "dotnet", "archive")}
    assert validation.tier_passed("light", results) is True
    assert validation.tier_passed("full", results) is False
    assert validation.tier_passed("full", {**results, "native": {"status": "incomplete"}}) is False


@pytest.mark.asyncio
async def test_an_rea_error_inside_the_mcp_wrapper_is_read(rea, runs, tmp_path, monkeypatch):
    """Failed MCP calls come back as the text wrapper; the note names REA's code, message and fix."""

    _compiled(monkeypatch, runs, tmp_path)
    error = {
        "error": {
            "code": "provider_unavailable", "category": "unavailable",
            "message": "The selected analysis provider is unavailable.",
            "remediation": {"action": "Run rea doctor, then retry."},
        }
    }
    wrapped = f"[TextContent(type='text', text='{json.dumps(error)}', annotations=None)]"
    rea.answers["rea_open_binary"] = {"result": {"format": "mach-o"}}
    rea.answers["rea_procedure_pseudo_code"] = ToolResult(success=False, error=wrapped)
    rea.answers["rea_close_binary"] = {"ok": True}
    result = await validation.validate_native(tmp_path, session_id="s1")
    assert result.static == "failed"
    failed = [call for call in result.calls if call.tool == "rea_procedure_pseudo_code"]
    assert failed[0].summary == "provider_unavailable：The selected analysis provider is unavailable.（Run rea doctor, then retry.）"
    assert result.notes[-1].endswith("provider_unavailable：The selected analysis provider is unavailable.（Run rea doctor, then retry.）")


def test_conversations_keep_the_validations_own_notes_only():
    kept, hidden = validation.public_notes([
        "REA 定位 calc 于 index.js:2-4",
        "Calc IL: ldarg.0×1, ldc.i4.3×1, mul×1",
        "伪代码含 ×3 与 +7",
        "伪代码：int calc(int x) { return x * 3 + 7; }",
        "缺少：docs/readme.txt",
        "提取出的 src/sample.js 内容不一致",
        # Quoting another program: shortened to what happened.
        "编译失败：'/srv/my private tools/cc': not found",
        "procedure_pseudo_code 没有返回 calc 的伪代码：open failed at \\\\fileserver\\share",
        "多出：../../home/ops/.ssh/known_hosts",
        # Shapes our notes have, filled with something else: left out.
        "REA 定位 calc 于 /srv/app/index.js:2-4",
        "伪代码：/* WARNING: /srv/x */",
        "缺少：/srv/secret.txt",
        "提取出的 /srv/x 内容不一致",
        "sample at /srv/x",
        None, "",
    ])
    assert kept == [
        "REA 定位 calc 于 index.js:2-4",
        "Calc IL: ldarg.0×1, ldc.i4.3×1, mul×1",
        "伪代码含 ×3 与 +7",
        "伪代码：int calc(int x) { return x * 3 + 7; }",
        "缺少：docs/readme.txt",
        "提取出的 src/sample.js 内容不一致",
        "编译样例 C 程序失败",
        "procedure_pseudo_code 没有返回 calc 的伪代码",
        "REA 列出了样例里没有的文件",
    ]
    assert hidden == 8  # 3 shortened, 5 left out; empty notes are nothing to hide


def test_conversations_keep_an_observed_result_only_in_the_samples_own_terms():
    assert validation.public_actual(samples.EXPECTED_OUTPUT) == samples.EXPECTED_OUTPUT
    names = ", ".join(sorted(samples.ARCHIVE_FILES))
    assert validation.public_actual(names) == names
    assert validation.public_actual("Error: Cannot find module '/srv/rea/index.js'") is None
    assert validation.public_actual("docs/readme.txt, ../../home/ops/.ssh/known_hosts") is None
    assert validation.public_actual(None) is None


def test_hidden_details_count_what_conversations_leave_out():
    assert validation.hidden_details({
        "notes": ["伪代码含 ×3 与 +7", "sample at /srv/x", "编译失败：/srv/cc"],
        "actual": "Error: Cannot find module '/srv/rea/index.js'",
        "calls": [
            {"tool": "rea_open_binary", "ok": True, "summary": "{...}"},
            {"tool": "rea_procedure_pseudo_code", "ok": False, "summary": "provider_unavailable at /srv/ghidra"},
            {"tool": "rea_close_binary", "ok": False, "summary": ""},
        ],
    }) == 4
    assert validation.hidden_details({"notes": ["伪代码含 ×3 与 +7"], "actual": samples.EXPECTED_OUTPUT, "calls": []}) == 0
    assert validation.hidden_details(None) == 0


@pytest.mark.asyncio
async def test_a_validation_run_uses_and_then_closes_an_rea_connection_of_its_own(monkeypatch):
    # Opening the samples in a conversation's connection would switch away its target and,
    # with close_binary, clear its evidence.
    from flocks.mcp import MCP

    scopes, closed = [], []

    async def execute(name, ctx, **kwargs):
        scopes.append((ctx.session_id, (ctx.extra or {}).get("mcp_scope")))
        return ToolResult(success=True, output=json.dumps({"ok": True}))

    async def close_scope(name, scope):
        closed.append((name, scope))

    async def validator(root, *, session_id):
        result = validation.CapabilityResult("javascript", status="passed", static="passed", runtime="passed")
        await validation.call_rea(result, "open_binary", {"path": "x"}, session_id=session_id)
        await validation.call_rea(result, "close_binary", {}, session_id=session_id)
        return result

    async def skipped(root, *, session_id):
        return validation.CapabilityResult("x", status="passed", static="passed", runtime="not_applicable")

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    monkeypatch.setattr(MCP, "close_scope", close_scope)
    monkeypatch.setitem(validation._VALIDATORS, "javascript", validator)
    monkeypatch.setitem(validation._VALIDATORS, "dotnet", skipped)
    monkeypatch.setitem(validation._VALIDATORS, "archive", skipped)

    await validation.run_validation("light", session_id="ses_install")

    assert len(scopes) == 2 and len({scope for _session, scope in scopes}) == 1
    session_id, scope = scopes[0]
    assert session_id == "ses_install" and scope.startswith("rea-validation:")
    assert closed == [("rea", scope)]
