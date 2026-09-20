"""Static completeness checks for shipped definitions, never user/Hub installs.

Read native YAML/JSON/frontmatter and Python AST only: importing tool modules can
register handlers, initialize application state, or require optional SDKs.
"""

import ast
import json
import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / ".flocks" / "plugins"
REGISTRY = ROOT / "flocks" / "tool" / "registry.py"
ASSISTANT_GROUPS = {"系统辅助", "安全研判", "威胁情报", "平台集成"}
TOOL_GROUPS = {
    "文件操作", "终端执行", "代码分析", "检索", "任务与工作流", "代理协作", "系统管理", "企业协作",
}
DEVICE_GROUPS = {"NDR", "EDR/HIDS", "SIEM", "XDR", "WAF", "网络防护", "身份访问", "威胁情报"}
MCP_GROUPS = {"威胁情报", "SIEM 与日志分析", "安全运营", "数据处理", "合规", "代码安全"}


@pytest.fixture(autouse=True)
def _cleanup_runtime_singletons_after_test():
    # Override the global runtime-cleanup fixture: this static suite creates no
    # singletons, and must not import application/storage modules at teardown.
    yield


def _assert_group(value, source, allowed):
    assert isinstance(value, str), source
    assert value == value.strip() and 0 < len(value) <= 32, source
    assert not any(ord(char) < 32 or ord(char) == 127 for char in value), source
    assert value in allowed, (source, value)


def _yaml(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def _ast(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _native_yaml(directory, max_depth, exclude=()):
    """Match scan_directory's depth/underscore rules without importing plugins."""
    for path in sorted(directory.iterdir()):
        if path.name.startswith("_"):
            continue
        if path.is_file() and path.suffix in {".yaml", ".yml"}:
            yield path
        elif max_depth and path.is_dir() and path.name not in exclude:
            yield from _native_yaml(path, max_depth - 1)


def _tool_yaml_paths():
    # Take the scan depth and top-level exclusions from the actual extension
    # declaration, so coverage follows the native loader rather than rglob'ing
    # fixtures, generated tools, or the separately managed MCP templates.
    declaration = next(
        node for node in ast.walk(_ast(REGISTRY))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "ExtensionPoint"
        and any(
            kw.arg == "attr_name" and isinstance(kw.value, ast.Constant) and kw.value.value == "TOOLS"
            for kw in node.keywords
        )
    )
    options = {kw.arg: kw.value for kw in declaration.keywords}
    assert ast.literal_eval(options["recursive"]) is True
    depth = ast.literal_eval(options["max_depth"])
    excluded = options["exclude_subdirs"]
    assert isinstance(excluded, ast.Call) and isinstance(excluded.func, ast.Name)
    assert excluded.func.id == "frozenset"
    return list(_native_yaml(PLUGINS / "tools", depth, ast.literal_eval(excluded.args[0])))


def _tool_declarations(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Name) and node.func.id == "ToolInfo"
            or isinstance(node.func, ast.Attribute) and node.func.attr == "register_function"
        ):
            yield node


@pytest.mark.parametrize("directory", [ROOT / "flocks" / "agent" / "agents", PLUGINS / "agents"])
def test_builtin_agent_groups(directory):
    # _iter_agent_dirs supports immediate agents and one nested collection level.
    paths = [path for path in _native_yaml(directory, 2) if path.name == "agent.yaml"]
    assert len(paths) == 9
    for path in paths:
        data = _yaml(path)
        assert data.get("name"), path
        _assert_group(data.get("group"), path, ASSISTANT_GROUPS)


def _skill_headers():
    headers = {}
    for path in sorted((PLUGINS / "skills").rglob("SKILL.md")):
        content = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", content, re.M | re.S)
        if match:
            headers[path] = match[1]
    return headers


def test_builtin_skill_frontmatter_groups():
    headers = _skill_headers()
    assert len(headers) == 19
    for path, header in headers.items():
        # Inspect the literal scalar lines instead of reparsing the description:
        # tool-builder has a legacy unquoted ': ' scalar supported by the native
        # compatibility decoder. Do not rewrite it or any Markdown body here.
        fields = {}
        for key in ("name", "group"):
            lines = re.findall(rf"^{key}:[^\r\n]*$", header, re.M)
            assert len(lines) == 1, (path, key)
            fields.update(yaml.safe_load(lines[0]))
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", fields["name"]), path
        assert re.search(r"^description:[ \t]+\S", header, re.M), path
        _assert_group(fields.get("group"), path, ASSISTANT_GROUPS)


def _workflow_dirs():
    # read_workflow_dir also accepts Markdown-only drafts; meta.json alone never
    # makes an empty directory discoverable.
    return [
        directory for directory in sorted((PLUGINS / "workflows").iterdir())
        if directory.is_dir() and any(
            (directory / name).is_file() for name in ("workflow.json", "workflow.md", "workflow.edit.md")
        )
    ]


def test_builtin_workflow_groups():
    directories = _workflow_dirs()
    assert len(directories) == 2
    for directory in directories:
        meta = directory / "meta.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        _assert_group(data.get("group"), meta, {"安全研判"})


def test_core_python_tool_groups():
    registry = _ast(REGISTRY)
    registry_class = next(node for node in registry.body if isinstance(node, ast.ClassDef) and node.name == "ToolRegistry")
    modules = next(
        ast.literal_eval(node.value) for node in registry_class.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_builtin_module_groups" for target in node.targets)
    )
    paths = [ROOT.joinpath(*package.split("."), f"{module}.py") for package, names in modules for module in names]
    assert len(paths) == 38
    declarations = [(path, node) for path in paths for node in _tool_declarations(_ast(path))]
    assert len(declarations) == 40
    builtin_registration = next(
        node for node in registry_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_register_builtin_tools"
    )
    time_declarations = list(_tool_declarations(builtin_registration))
    assert len(time_declarations) == 1
    assert ast.literal_eval(next(kw.value for kw in time_declarations[0].keywords if kw.arg == "name")) == "get_time"
    declarations.append((REGISTRY, time_declarations[0]))

    names = set()
    non_native = set()
    for path, declaration in declarations:
        keywords = {kw.arg: kw.value for kw in declaration.keywords}
        name = ast.literal_eval(keywords["name"])
        assert name not in names, (path, name)
        names.add(name)
        assert "group" in keywords, (path, name)
        assert isinstance(keywords["group"], ast.Constant), (path, name)
        _assert_group(ast.literal_eval(keywords["group"]), (path, name), TOOL_GROUPS)
        if "native" in keywords and ast.literal_eval(keywords["native"]) is False:
            non_native.add(name)
    assert len(names) == 41
    assert {"task", "lsp", "list_providers", "add_provider", "add_model"} <= non_native
    assert "invalid" not in names  # Internal failure sentinel, not a registered builtin.


@pytest.mark.parametrize("kind,tool_count,provider_count,allowed", [
    ("api", 37, 6, {"网络测绘", "威胁情报"}),
    ("device", 80, 12, DEVICE_GROUPS),
])
def test_builtin_yaml_tools_and_providers(kind, tool_count, provider_count, allowed):
    root = PLUGINS / "tools" / kind
    all_tools = _tool_yaml_paths()
    assert len(all_tools) == 117
    tools = [path for path in all_tools if path.is_relative_to(root)]
    assert len(tools) == tool_count
    providers = sorted(root.glob("*/_provider.yaml"))
    assert len(providers) == provider_count
    by_identity = {}
    for path in providers:
        data = _yaml(path)
        _assert_group(data.get("group"), path, allowed)
        identity = data.get("service_id") or data["name"]
        assert identity not in by_identity, path
        by_identity[identity] = data
    for path in tools:
        data = _yaml(path)
        assert data.get("name") and isinstance(data.get("handler"), dict), path
        _assert_group(data.get("group"), path, allowed)
        # Also checks VirusTotal's lowercase identity and the standalone device
        # asset-inventory tool, neither of which should fall through defaults.
        assert data["provider"] in by_identity, path
        assert data["group"] == by_identity[data["provider"]]["group"], path
        if kind == "api":
            assert data["group"] == ("网络测绘" if data["provider"] == "fofa" else "威胁情报"), path


def test_builtin_mcp_catalog_groups():
    path = ROOT / ".flocks" / "mcp_list.json.example"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert len(catalog["servers"]) == 11
    for item in catalog["servers"]:
        _assert_group(item.get("group"), (path, item["id"]), MCP_GROUPS)


def test_builtin_mcp_template_groups():
    paths = list(_native_yaml(PLUGINS / "tools" / "mcp", 0))
    assert len(paths) == 3
    for path in paths:
        _assert_group(_yaml(path).get("group"), path, {"威胁情报"})


def test_inactive_placeholders_are_not_activated():
    assert PLUGINS / "skills" / "detect-malicious-skill" / "SKILL.md" not in _skill_headers()
    assert PLUGINS / "workflows" / "wf-1" not in _workflow_dirs()
    assert not (PLUGINS / "workflows" / "wf-1" / "meta.json").exists()
