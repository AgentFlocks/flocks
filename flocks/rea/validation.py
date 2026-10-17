"""Tier validation: fixed samples, real REA tool calls, and runs of the samples.

The tool calls go through the REA tools Flocks registered from the ``rea`` MCP
service (``rea_<tool>``), the same path Rex uses. Every capability records the
sample (path, SHA-256), each call with its arguments and result, the expected
and actual results, and separate static-analysis and runtime outcomes.

- light: JavaScript, .NET and ZIP. The tier passes when all three static
  analyses pass; a runtime check that cannot run is listed as unfinished.
- full: light plus a native C program decompiled with ``open_binary`` and
  ``procedure_pseudo_code``; it passes only when the call, the semantic check
  and the program run all succeed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from flocks.rea import runtime, samples
from flocks.utils.log import Log


log = Log.create(service="rea.validation")

LIGHT_CAPABILITIES = ("javascript", "dotnet", "archive")
FULL_CAPABILITIES = (*LIGHT_CAPABILITIES, "native")
_RUN_TIMEOUT_SECONDS = 60.0
_COMPILE_TIMEOUT_SECONDS = 120.0
_JS_EXPRESSION = re.compile(r"return\s+x\s*\*\s*3\s*\+\s*7")
_MUL3 = re.compile(r"\*\s*3\b|\b3\s*\*")
_ADD7 = re.compile(r"\+\s*7\b|\b7\s*\+")


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]
    ok: bool
    summary: str = ""


@dataclass
class CapabilityResult:
    key: str
    # passed | failed | incomplete
    status: str = "incomplete"
    static: str = "incomplete"
    # passed | failed | incomplete | not_applicable
    runtime: str = "incomplete"
    sample: dict[str, Any] = field(default_factory=dict)
    expected: str = ""
    actual: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["calls"] = [asdict(call) for call in self.calls]
        return data


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_json(text: Any) -> Any:
    """The first JSON object in a tool result (also inside an MCP error's text wrapper)."""

    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return None


def _error_text(error: Any) -> str:
    """``code: message (remediation)`` of an REA error object."""

    if not isinstance(error, dict):
        return str(error)
    parts = [str(error.get("code") or "error")]
    if error.get("message"):
        parts.append(str(error["message"]))
    remediation = error.get("remediation")
    if isinstance(remediation, dict) and remediation.get("action"):
        parts.append(str(remediation["action"]))
    return "：".join(parts[:2]) + (f"（{parts[2]}）" if len(parts) > 2 else "")


async def call_rea(
    result: CapabilityResult,
    tool: str,
    args: dict[str, Any],
    *,
    session_id: str,
) -> tuple[bool, Any, str]:
    """Call ``rea_<tool>`` through Flocks' tool registry; returns (ok, parsed JSON, raw text)."""

    from flocks.tool.registry import ToolContext, ToolRegistry

    ctx = ToolContext(session_id=session_id, message_id="rea-validation", agent="rex")
    name = f"{runtime.MCP_SERVER_NAME}_{tool}"
    try:
        outcome = await ToolRegistry.execute(name, ctx, **args)
    except Exception as exc:
        result.calls.append(ToolCall(name, args, False, str(exc)[:300]))
        return False, None, str(exc)
    text = outcome.output if outcome.success else outcome.error
    if outcome.success and outcome.truncated:
        # Large Evidence is saved whole next to the truncated preview.
        path = (outcome.metadata or {}).get("output_path")
        if path:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError:
                pass
    raw = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    data = _load_json(raw)
    error = (data or {}).get("error") if isinstance(data, dict) else None
    ok = bool(outcome.success) and not error
    summary = (_error_text(error) if error else raw)[:400]
    result.calls.append(ToolCall(name, args, ok, summary))
    return ok, data, raw


async def _run(command: list[str], *, cwd: Optional[Path] = None, timeout: float = _RUN_TIMEOUT_SECONDS) -> tuple[int, str]:
    try:
        return await runtime.run_process(command, timeout=timeout, cwd=cwd)
    except Exception as exc:  # missing interpreter, timeout
        return -1, str(exc) or type(exc).__name__


def _find_callable(node: Any, name: str) -> Optional[dict[str, Any]]:
    """The callable named ``name`` in a JavaScript application graph (with its source range)."""

    if isinstance(node, dict):
        identity = node.get("identity")
        if (
            node.get("label") == name
            and isinstance(identity, dict)
            and str(identity.get("role_key", "")).startswith("callable:")
            and isinstance(identity.get("source_range"), dict)
        ):
            return identity
        for value in node.values():
            found = _find_callable(value, name)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_callable(value, name)
            if found:
                return found
    return None


def _source_slice(text: str, source_range: dict[str, Any]) -> str:
    lines = text.splitlines()
    start = source_range.get("start") or {}
    end = source_range.get("end") or {}
    first = int(start.get("line", 1))
    last = int(end.get("line", first))
    return "\n".join(lines[first - 1:last])


async def validate_javascript(root: Path, *, session_id: str) -> CapabilityResult:
    result = CapabilityResult("javascript", expected=f"calc({samples.EXPECTED_INPUT}) = {samples.EXPECTED_OUTPUT}")
    app = root / "javascript"
    app.mkdir(parents=True, exist_ok=True)
    script = app / "index.js"
    script.write_text(samples.JS_SOURCE, encoding="utf-8")
    (app / "package.json").write_text(samples.JS_PACKAGE_JSON, encoding="utf-8")
    result.sample = {"path": str(script), "sha256": _sha256_file(script)}

    ok, data, _raw = await call_rea(
        result, "analyze_javascript_application",
        {"input_path": str(app), "format": "directory", "detail": "complete"},
        session_id=session_id,
    )
    identity = _find_callable(data, "calc") if ok else None
    if identity:
        recovered = _source_slice(samples.JS_SOURCE, identity["source_range"])
        located = identity["source_range"]
        result.notes.append(
            f"REA 定位 calc 于 {identity.get('module_path')}:{located['start']['line']}-{located['end']['line']}",
        )
        result.static = "passed" if _JS_EXPRESSION.search(recovered) else "failed"
        if result.static == "failed":
            result.notes.append("定位到的函数体与源码逻辑不一致")
    else:
        result.static = "failed"
        result.notes.append("REA 结果里没有找到 calc 函数")

    node = runtime.node_executable()
    if not node:
        result.runtime = "incomplete"
        result.notes.append("没有 Node.js，运行核对未完成")
    else:
        code, output = await _run([node, str(script), str(samples.EXPECTED_INPUT)])
        result.actual = output.strip()
        result.runtime = "passed" if code == 0 and result.actual == samples.EXPECTED_OUTPUT else "failed"
    result.status = "passed" if result.static == "passed" else "failed"
    return result


async def validate_dotnet(root: Path, *, session_id: str) -> CapabilityResult:
    result = CapabilityResult("dotnet", expected=f"Calc({samples.EXPECTED_INPUT}) = {samples.EXPECTED_OUTPUT}")
    folder = root / "dotnet"
    folder.mkdir(parents=True, exist_ok=True)
    assembly = folder / samples.DOTNET_ASSEMBLY_NAME
    assembly.write_bytes(samples.dotnet_assembly())
    (folder / "ReaCalc.runtimeconfig.json").write_text(samples.DOTNET_RUNTIMECONFIG, encoding="utf-8")
    (folder / "Program.cs").write_text(samples.DOTNET_SOURCE, encoding="utf-8")
    (folder / "ReaCalc.csproj").write_text(samples.DOTNET_PROJECT, encoding="utf-8")
    digest = _sha256_file(assembly)
    result.sample = {"path": str(assembly), "sha256": digest, "expectedSha256": samples.DOTNET_ASSEMBLY_SHA256}
    if digest != samples.DOTNET_ASSEMBLY_SHA256:
        result.static = "failed"
        result.notes.append("样例程序集 SHA-256 与源码构建记录不一致")
        result.status = "failed"
        return result

    await call_rea(result, "inspect_managed_artifact", {"path": str(assembly)}, session_id=session_id)
    ok, data, _raw = await call_rea(result, "inspect_managed_members", {"path": str(assembly)}, session_id=session_id)
    methods = []
    if ok and isinstance(data, dict):
        normalized = data.get("normalized_result") or {}
        methods = normalized.get("methods") or []
    calc = next(
        (m for m in methods if isinstance(m, dict) and m.get("name") == "Calc"
         and m.get("declaring_type") == "ReaValidation.Sample"),
        None,
    )
    if calc is None:
        result.static = "failed"
        result.notes.append("REA 结果里没有找到 ReaValidation.Sample.Calc")
    else:
        signature = calc.get("signature") or {}
        opcodes = (calc.get("body") or {}).get("opcode_counts") or {}
        same_signature = signature.get("return_type") == "i4" and signature.get("parameter_types") == ["i4"]
        same_logic = opcodes == samples.DOTNET_CALC_OPCODES
        result.notes.append(f"Calc IL: {', '.join(f'{k}×{v}' for k, v in opcodes.items())}")
        result.static = "passed" if same_signature and same_logic else "failed"

    dotnet = runtime.dotnet_executable()
    if not dotnet:
        result.runtime = "incomplete"
        result.notes.append("这台主机没有 .NET 运行时，运行核对未完成")
    else:
        code, output = await _run([dotnet, str(assembly), str(samples.EXPECTED_INPUT)])
        result.actual = output.strip()
        result.runtime = "passed" if code == 0 and result.actual == samples.EXPECTED_OUTPUT else "failed"
    result.status = "passed" if result.static == "passed" else "failed"
    return result


def _write_archive(path: Path) -> dict[str, str]:
    expected = {}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, text in samples.ARCHIVE_FILES.items():
            data = text.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 14, 0, 0, 0))
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data)
            expected[name] = _sha256_bytes(data)
    return expected


async def validate_archive(root: Path, *, session_id: str) -> CapabilityResult:
    result = CapabilityResult("archive", runtime="not_applicable")
    folder = root / "archive"
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "sample.zip"
    expected = _write_archive(archive)
    result.sample = {"path": str(archive), "sha256": _sha256_file(archive), "files": expected}
    result.expected = ", ".join(sorted(expected))

    ok, _data, _raw = await call_rea(result, "open_binary", {"path": str(archive)}, session_id=session_id)
    extracted: dict[str, str] = {}
    output_root = None
    if ok:
        ok, data, _raw = await call_rea(result, "extract_artifact", {}, session_id=session_id)
        normalized = (data.get("normalized_result") or {}) if isinstance(data, dict) else {}
        output_root = normalized.get("output_root")
        for item in normalized.get("artifacts") or []:
            if isinstance(item, dict) and item.get("relative_path"):
                extracted[item["relative_path"]] = item.get("sha256") or ""
        await call_rea(result, "close_binary", {}, session_id=session_id)
    result.actual = ", ".join(sorted(extracted))
    missing = sorted(set(expected) - set(extracted))
    extra = sorted(set(extracted) - set(expected))
    mismatched = sorted(name for name in expected if name in extracted and extracted[name] != expected[name])
    contents_ok = True
    if output_root and not (missing or extra or mismatched):
        for name, digest in expected.items():
            path = Path(output_root) / name
            if not path.is_file() or _sha256_file(path) != digest:
                contents_ok = False
                result.notes.append(f"提取出的 {name} 内容不一致")
    # The extraction folder is REA's (a temporary folder it chose); nothing in it is deleted here,
    # since its answer cannot prove which files belong to this check.
    if missing:
        result.notes.append(f"缺少：{', '.join(missing)}")
    if extra:
        result.notes.append(f"多出：{', '.join(extra)}")
    if mismatched:
        result.notes.append(f"SHA-256 不一致：{', '.join(mismatched)}")
    passed = bool(ok and extracted) and not (missing or extra or mismatched) and contents_ok
    result.static = "passed" if passed else "failed"
    result.status = result.static
    return result


async def validate_native(root: Path, *, session_id: str) -> CapabilityResult:
    result = CapabilityResult("native", expected=f"calc({samples.EXPECTED_INPUT}) = {samples.EXPECTED_OUTPUT}")
    folder = root / "native"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "calc.c"
    source.write_text(samples.C_SOURCE, encoding="utf-8")
    compiler = runtime.c_compiler()
    if not compiler:
        result.notes.append("没有 C 编译器，原生反编译验证未完成")
        return result
    binary = folder / "calc"
    flags = ["-O0", "-g", "-o", str(binary), str(source)]
    code, output = await _run([compiler, *flags], timeout=_COMPILE_TIMEOUT_SECONDS)
    if code != 0 or not binary.is_file():
        result.notes.append(f"编译失败：{output.strip()[-300:]}")
        return result
    result.sample = {
        "path": str(binary), "sha256": _sha256_file(binary), "source": str(source),
        "compile": " ".join([Path(compiler).name, *flags[:2]]),
    }

    ok, opened, _raw = await call_rea(result, "open_binary", {"path": str(binary)}, session_id=session_id)
    if ok and isinstance(opened, dict):
        result.sample["format"] = (opened.get("result") or {}).get("format")
    pseudo = ""
    if ok:
        for name in ("calc", "_calc"):
            called, data, raw = await call_rea(
                result, "procedure_pseudo_code", {"procedure": name}, session_id=session_id,
            )
            if called:
                normalized = (data or {}).get("normalized_result") if isinstance(data, dict) else None
                # REA returns the decompiled function as text; other shapes are searched as JSON.
                pseudo = normalized if isinstance(normalized, str) else (
                    json.dumps(normalized, ensure_ascii=False) if normalized is not None else raw
                )
                break
        await call_rea(result, "close_binary", {}, session_id=session_id)
    if not pseudo:
        result.static = "failed"
        failed = [call for call in result.calls if call.tool.endswith("procedure_pseudo_code") and not call.ok]
        reason = f"：{failed[-1].summary}" if failed else ""
        result.notes.append(f"procedure_pseudo_code 没有返回 calc 的伪代码{reason}")
    else:
        same_logic = bool(_MUL3.search(pseudo) and _ADD7.search(pseudo))
        result.static = "passed" if same_logic else "failed"
        result.notes.append("伪代码含 ×3 与 +7" if same_logic else "伪代码与源码逻辑不一致")
        result.notes.append("伪代码：" + " ".join(pseudo.split())[:300])

    code, output = await _run([str(binary), str(samples.EXPECTED_INPUT)])
    result.actual = output.strip()
    result.runtime = "passed" if code == 0 and result.actual == samples.EXPECTED_OUTPUT else "failed"
    passed = result.static == "passed" and result.runtime == "passed"
    result.status = "passed" if passed else ("failed" if result.static == "failed" or result.runtime == "failed" else "incomplete")
    return result


_VALIDATORS: dict[str, Callable[..., Awaitable[CapabilityResult]]] = {
    "javascript": validate_javascript,
    "dotnet": validate_dotnet,
    "archive": validate_archive,
    "native": validate_native,
}


# ── what a conversation keeps of the evidence ──
#
# A conversation (shared, read later) keeps only the texts written here or evidence whose
# characters cannot carry a host path; anything quoting another program's output is
# shortened to what happened. The full notes stay in the install record, which only
# administrators read (plugin square).

_FIXED_NOTES = frozenset({
    "定位到的函数体与源码逻辑不一致",
    "REA 结果里没有找到 calc 函数",
    "没有 Node.js，运行核对未完成",
    "样例程序集 SHA-256 与源码构建记录不一致",
    "REA 结果里没有找到 ReaValidation.Sample.Calc",
    "这台主机没有 .NET 运行时，运行核对未完成",
    "没有 C 编译器，原生反编译验证未完成",
    "伪代码含 ×3 与 +7",
    "伪代码与源码逻辑不一致",
})
_LOCATED_NOTE = re.compile(r"^REA 定位 calc 于 [A-Za-z0-9_.-]+:\d+-\d+$")
_IL_NOTE = re.compile(r"^Calc IL: [A-Za-z0-9_.]+×\d+(?:, [A-Za-z0-9_.]+×\d+)*$")
_PSEUDO_NOTE = re.compile(r"^伪代码：[A-Za-z0-9_ (){};*+=,.-]{1,300}$")
_EXTRACTED_NOTE = re.compile(r"^提取出的 (.+) 内容不一致$")
_LISTING_NOTES = ("缺少：", "SHA-256 不一致：")
_SHORTENED_NOTES = (
    ("编译失败：", "编译样例 C 程序失败"),
    ("procedure_pseudo_code 没有返回 calc 的伪代码", "procedure_pseudo_code 没有返回 calc 的伪代码"),
    ("多出：", "REA 列出了样例里没有的文件"),
)


def _sample_names(text: str) -> bool:
    return bool(text) and all(name in samples.ARCHIVE_FILES for name in text.split(", "))


def _public_note(text: str) -> Optional[str]:
    if text in _FIXED_NOTES or _LOCATED_NOTE.match(text) or _IL_NOTE.match(text) or _PSEUDO_NOTE.match(text):
        return text
    for prefix, fixed in _SHORTENED_NOTES:
        if text.startswith(prefix):
            return fixed
    for prefix in _LISTING_NOTES:
        if text.startswith(prefix) and _sample_names(text[len(prefix):]):
            return text
    extracted = _EXTRACTED_NOTE.match(text)
    if extracted and extracted.group(1) in samples.ARCHIVE_FILES:
        return text
    return None


def public_notes(notes: Any) -> tuple[list[str], int]:
    """The notes a conversation may keep, and how many are left out or shortened."""

    kept: list[str] = []
    held = 0
    for note in notes or []:
        if not isinstance(note, str) or not note.strip():
            continue
        text = note
        public = _public_note(text)
        if public != text:
            held += 1
        if public is not None:
            kept.append(public)
    return kept, held


def hidden_details(item: Any) -> int:
    """How much of a capability's evidence a conversation leaves out (the plugin square shows it):
    notes it does not keep as they are, an observed result it does not keep, what failed calls returned."""

    if not isinstance(item, dict):
        return 0
    _kept, hidden = public_notes(item.get("notes"))
    if item.get("actual") and public_actual(item.get("actual")) is None:
        hidden += 1
    return hidden + sum(
        1 for call in item.get("calls") or []
        if isinstance(call, dict) and call.get("ok") is False and call.get("summary")
    )


def public_actual(actual: Any) -> Optional[str]:
    """The observed result when it is the sample's expected output or a list of its own files."""

    if not isinstance(actual, str):
        return None
    if actual == samples.EXPECTED_OUTPUT or _sample_names(actual):
        return actual
    return None


def tier_capabilities(tier: str) -> tuple[str, ...]:
    return FULL_CAPABILITIES if tier == "full" else LIGHT_CAPABILITIES


def tier_passed(tier: str, results: dict[str, dict[str, Any]]) -> bool:
    return all((results.get(key) or {}).get("status") == "passed" for key in tier_capabilities(tier))


async def run_validation(
    tier: str,
    *,
    session_id: str,
    on_result: Optional[Callable[[CapabilityResult], Awaitable[None]]] = None,
) -> dict[str, Any]:
    """Run the tier's samples one after another (REA keeps one active target per service)."""

    root = runtime.validation_root() / time.strftime("%Y%m%d-%H%M%S")
    root.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    for key in tier_capabilities(tier):
        try:
            outcome = await _VALIDATORS[key](root, session_id=session_id)
        except Exception as exc:  # one failing sample must not hide the others
            log.error("rea.validation.crashed", {"capability": key, "error": str(exc)})
            outcome = CapabilityResult(key, status="failed", static="failed", notes=[f"验证出错：{exc}"])
        results[key] = outcome.to_dict()
        if on_result is not None:
            await on_result(outcome)
    return {"tier": tier, "ok": tier_passed(tier, results), "root": str(root), "capabilities": results}
