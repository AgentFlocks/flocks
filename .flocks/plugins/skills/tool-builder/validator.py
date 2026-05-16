#!/usr/bin/env python3
"""
Tool plugin validator for the tool-builder skill.

Audits a freshly created Flocks tool for missing or inconsistent
metadata and handler information. Designed to be invoked by the
tool-builder skill's Verification Protocol *before* declaring a tool
ready for use.

Detects, among other things:
  * Missing tool name / description / category / enabled
  * Invalid category (silently coerced to ``custom`` by the loader)
  * Missing inputSchema / parameters
  * Parameter properties without ``type`` or ``description``
  * For YAML-HTTP tools: missing handler section, missing url/method,
    URL/header/body placeholders that are not declared as parameters,
    secret references, ``response.error_mapping`` keys that are not int
  * For YAML-script tools: missing ``script_file``, file not on disk,
    missing or non-async / non-callable ``function`` symbol
  * For tools under ``api/{provider}/``: missing or incomplete
    ``_provider.yaml`` (name / description / description_cn /
    defaults.base_url, auth.secret/inject_as)
  * For Python tools: missing ``@ToolRegistry.register_function``
    decorator, missing decorator kwargs (name/description/parameters),
    parameter list that does not match the function signature, function
    that is not ``async def``

Usage:
    uv run python validator.py <path-to-tool-file>

Exit codes:
    0 — no FAIL items (WARN allowed)
    1 — at least one FAIL item OR validator could not run
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    import yaml
except ImportError:
    print("FAIL: pyyaml not installed. Run `uv pip install pyyaml`.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants mirrored from flocks.tool.registry / flocks.tool.tool_loader
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {
    "file", "terminal", "browser", "code",
    "search", "system", "custom",
}

VALID_PARAMETER_TYPES = {
    "string", "integer", "number", "boolean", "array", "object",
}

# Tool name collisions with built-ins or reserved words.
RESERVED_TOOL_NAMES = {
    "read", "write", "edit", "apply_patch", "glob",
    "doc_parser",
    "bash", "grep", "lsp_tool",
    "webfetch", "websearch",
    "delegate_task",
    "task", "schedule_task_center", "todo", "plan",
    "run_workflow", "run_workflow_node",
    "echo", "get_time",
    "skill", "question",
}

PARAM_PATTERN = re.compile(r"\{([^}]+)\}")
SECRET_PATTERN = re.compile(r"\{secret:([^}]+)\}")
SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Diagnostic record
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    level: str   # "FAIL" | "WARN" | "PASS"
    section: str
    message: str


@dataclass
class Report:
    target: Path
    mode: str = ""
    issues: List[Issue] = field(default_factory=list)

    def add(self, level: str, section: str, message: str) -> None:
        self.issues.append(Issue(level=level, section=section, message=message))

    def fail(self, section: str, message: str) -> None:
        self.add("FAIL", section, message)

    def warn(self, section: str, message: str) -> None:
        self.add("WARN", section, message)

    def ok(self, section: str, message: str) -> None:
        self.add("PASS", section, message)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "WARN")

    def render(self) -> str:
        lines = [f"=== Validation report: {self.target} ==="]
        if self.mode:
            lines.append(f"Mode: {self.mode}")
        lines.append("")

        sections: Dict[str, List[Issue]] = {}
        for issue in self.issues:
            sections.setdefault(issue.section, []).append(issue)

        for section, items in sections.items():
            lines.append(f"[{section}]")
            for it in items:
                lines.append(f"  {it.level:<4}  {it.message}")
            lines.append("")

        lines.append(
            f"Summary: {self.fail_count} FAIL, {self.warn_count} WARN"
        )
        if self.fail_count == 0 and self.warn_count == 0:
            lines[-1] += " — looks good."
        elif self.fail_count == 0:
            lines[-1] += " — fix WARN items if you want a clean report."
        else:
            lines[-1] += " — fix FAIL items before declaring the tool ready."
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# YAML tool validation
# ---------------------------------------------------------------------------


def _looks_like_yaml(path: Path) -> bool:
    return path.suffix.lower() in {".yaml", ".yml"}


def _tool_under_api(yaml_path: Path) -> bool:
    """Return True if this YAML lives under an ``api/`` directory.

    Accepts both the canonical install location
    (``~/.flocks/plugins/tools/api/...``) and ad-hoc layouts during
    development where the user runs the validator directly on a file
    that simply has ``api`` as one of its parent directories.
    """
    for parent in yaml_path.resolve().parents:
        if parent.name == "api":
            return True
    return False


def _provider_dir(yaml_path: Path) -> Optional[Path]:
    """If the YAML is under ``api/{provider}/foo.yaml``, return the provider dir."""
    parent = yaml_path.parent
    if parent.name == "api":
        # Standalone tool directly under api/ — no provider dir.
        return None
    grandparent = parent.parent
    if grandparent.name == "api":
        return parent
    return None


def validate_yaml_tool(yaml_path: Path) -> Report:
    report = Report(target=yaml_path)
    try:
        raw_text = yaml_path.read_text(encoding="utf-8")
    except OSError as e:
        report.fail("File", f"Cannot read file: {e}")
        return report

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        report.fail("Syntax", f"YAML parse error: {e}")
        return report

    if not isinstance(data, dict):
        report.fail("Syntax", "Top-level YAML must be a mapping/dict")
        return report

    handler_raw = data.get("handler")
    handler_type = (
        handler_raw.get("type", "http").lower()
        if isinstance(handler_raw, dict) else None
    )
    report.mode = (
        f"YAML-HTTP (Mode A, handler.type={handler_type})"
        if handler_type else "YAML (handler missing)"
    )

    _validate_yaml_metadata(data, report, yaml_path)
    parameter_names = _validate_yaml_parameters(data, report)
    _validate_yaml_handler(data, report, yaml_path, parameter_names)
    _validate_provider_yaml(yaml_path, data, report)

    return report


def _validate_yaml_metadata(
    data: Dict[str, Any], report: Report, yaml_path: Path,
) -> None:
    section = "Metadata"

    name = data.get("name")
    if not name or not isinstance(name, str):
        report.fail(section, "missing required field 'name'")
    else:
        if not SNAKE_CASE_RE.match(name):
            report.warn(section, f"name '{name}' is not snake_case")
        if name in RESERVED_TOOL_NAMES:
            report.fail(
                section,
                f"name '{name}' collides with a built-in tool — pick another",
            )
        stem = yaml_path.stem
        if stem != name:
            report.warn(
                section,
                f"YAML filename '{stem}.yaml' does not match name '{name}'",
            )
        report.ok(section, f"name = {name}")

    description = data.get("description")
    if not description or not str(description).strip():
        report.fail(section, "missing or empty 'description'")
    elif len(str(description).strip()) < 20:
        report.warn(
            section,
            f"description is only {len(str(description).strip())} chars — "
            "the LLM uses this to decide when to invoke the tool",
        )
    else:
        report.ok(section, f"description present ({len(str(description))} chars)")

    category = data.get("category")
    if category is None:
        # API tools commonly inherit category from _provider.yaml; only warn.
        report.warn(
            section,
            "no 'category' set — loader will fall back to 'custom' "
            "(or provider defaults if present)",
        )
    elif category not in VALID_CATEGORIES:
        report.fail(
            section,
            f"category '{category}' is invalid; loader silently coerces "
            f"to 'custom'. Valid: {sorted(VALID_CATEGORIES)}",
        )
    else:
        report.ok(section, f"category = {category}")

    enabled = data.get("enabled")
    if enabled is None:
        report.warn(
            section,
            "no 'enabled' field — defaults to true; set explicitly so "
            "the tool is unambiguously activated",
        )
    elif enabled is not True:
        report.warn(
            section,
            f"enabled = {enabled!r} — tool will NOT be active immediately. "
            "Set 'enabled: true' unless the user asked for it disabled.",
        )
    else:
        report.ok(section, "enabled = true")

    # provider field — required for API service card display
    if _tool_under_api(yaml_path):
        provider = data.get("provider")
        prov_dir = _provider_dir(yaml_path)
        if not provider and not prov_dir:
            report.warn(
                section,
                "tool is under api/ but neither 'provider' field nor "
                "a provider subdirectory with _provider.yaml is present "
                "— it will not appear as an API service card",
            )
        elif provider:
            report.ok(section, f"provider = {provider}")


def _validate_yaml_parameters(data: Dict[str, Any], report: Report) -> Set[str]:
    """Validate inputSchema/parameters and return the set of declared param names."""
    section = "Parameters"
    declared: Set[str] = set()

    input_schema = data.get("inputSchema")
    params_list = data.get("parameters")

    if input_schema is None and params_list is None:
        report.warn(
            section,
            "no inputSchema or parameters declared — "
            "the LLM will not be able to pass arguments",
        )
        return declared

    if input_schema is not None and params_list is not None:
        report.warn(
            section,
            "both 'inputSchema' and 'parameters' are present; "
            "'inputSchema' wins — drop 'parameters' to avoid confusion",
        )

    if isinstance(input_schema, dict):
        if input_schema.get("type") != "object":
            report.warn(
                section,
                f"inputSchema.type = {input_schema.get('type')!r}; "
                "should be 'object' for tool inputs",
            )
        properties = input_schema.get("properties") or {}
        if not isinstance(properties, dict) or not properties:
            report.warn(section, "inputSchema.properties is empty")
        else:
            required = set(input_schema.get("required") or [])
            for pname, pinfo in properties.items():
                declared.add(pname)
                _validate_param_entry(
                    pname, pinfo, in_input_schema=True,
                    is_required=pname in required, report=report,
                )
            unknown_required = required - declared
            for pname in unknown_required:
                report.fail(
                    section,
                    f"required parameter '{pname}' is not defined in properties",
                )
            if declared:
                report.ok(section, f"inputSchema declares: {sorted(declared)}")
        return declared

    if isinstance(params_list, list):
        if not params_list:
            report.warn(section, "'parameters' list is empty")
        for item in params_list:
            if not isinstance(item, dict):
                report.fail(section, f"parameter entry is not a dict: {item!r}")
                continue
            pname = item.get("name")
            if not pname:
                report.fail(section, f"parameter entry missing 'name': {item!r}")
                continue
            declared.add(pname)
            _validate_param_entry(
                pname, item, in_input_schema=False,
                is_required=item.get("required", True), report=report,
            )
        if declared:
            report.ok(section, f"parameters declares: {sorted(declared)}")
        return declared

    report.fail(
        section,
        f"inputSchema/parameters has unexpected type: "
        f"{type(input_schema or params_list).__name__}",
    )
    return declared


def _validate_param_entry(
    pname: str,
    pinfo: Dict[str, Any],
    in_input_schema: bool,
    is_required: bool,
    report: Report,
) -> None:
    section = "Parameters"
    if not isinstance(pinfo, dict):
        report.fail(section, f"parameter '{pname}' is not a mapping")
        return

    ptype = pinfo.get("type")
    if not ptype:
        report.warn(
            section,
            f"parameter '{pname}' missing 'type' "
            "(loader falls back to 'string')",
        )
    elif ptype not in VALID_PARAMETER_TYPES:
        report.fail(
            section,
            f"parameter '{pname}' has invalid type '{ptype}'. "
            f"Valid: {sorted(VALID_PARAMETER_TYPES)}",
        )

    description = pinfo.get("description")
    if not description or not str(description).strip():
        report.warn(
            section,
            f"parameter '{pname}' missing 'description' — "
            "the LLM cannot reliably fill it in",
        )

    if is_required and "default" in pinfo:
        report.warn(
            section,
            f"parameter '{pname}' is required but also has a default — "
            "default is ignored when required=true",
        )


def _validate_yaml_handler(
    data: Dict[str, Any],
    report: Report,
    yaml_path: Path,
    parameter_names: Set[str],
) -> None:
    section = "Handler"
    handler = data.get("handler")
    execution = data.get("execution")

    if not isinstance(handler, dict):
        if isinstance(execution, dict):
            report.fail(
                section,
                "uses inline 'execution' block — disabled for safety. "
                "Use handler.type=script with a separate handler file.",
            )
        else:
            report.fail(
                section,
                "missing 'handler' section — loader will refuse to register "
                "the tool",
            )
        return

    htype = handler.get("type", "http")
    if htype not in {"http", "script"}:
        report.fail(
            section,
            f"handler.type = {htype!r}; must be 'http' or 'script'",
        )
        return

    if htype == "http":
        _validate_http_handler(handler, report, parameter_names, yaml_path)
    else:
        _validate_script_handler(handler, report, parameter_names, yaml_path)


def _validate_http_handler(
    handler: Dict[str, Any],
    report: Report,
    parameter_names: Set[str],
    yaml_path: Path,
) -> None:
    section = "Handler"

    method = handler.get("method")
    if not method:
        report.warn(section, "no 'method' set — loader defaults to GET")
    elif str(method).upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        report.warn(section, f"unusual HTTP method: {method!r}")

    url = handler.get("url")
    if not url:
        report.fail(section, "handler.url is empty — request would target ''")
    else:
        report.ok(section, f"handler.url = {url}")
        prov_dir = _provider_dir(yaml_path)
        # When {base_url} is used, _provider.yaml MUST supply defaults.base_url.
        if "{base_url}" in url and prov_dir is None:
            report.fail(
                section,
                "url uses {base_url} but the tool is not under "
                "api/{provider}/ — there is no _provider.yaml to inject it",
            )

    # Collect placeholders across url/headers/query_params/body
    declarable: Set[str] = set(parameter_names)
    declarable.add("base_url")
    referenced = set()

    def _scan(value: Any) -> None:
        if isinstance(value, str):
            for m in PARAM_PATTERN.findall(value):
                if not m.startswith("secret:"):
                    referenced.add(m)
        elif isinstance(value, dict):
            for v in value.values():
                _scan(v)
        elif isinstance(value, list):
            for v in value:
                _scan(v)

    _scan(handler.get("url"))
    _scan(handler.get("headers"))
    _scan(handler.get("query_params"))
    _scan(handler.get("body"))

    undeclared = referenced - declarable
    for name in sorted(undeclared):
        report.fail(
            section,
            f"placeholder '{{{name}}}' is referenced in url/headers/"
            f"query_params/body but not declared as a parameter — "
            "loader will substitute an empty string",
        )

    unused = parameter_names - referenced - {"base_url"}
    for name in sorted(unused):
        report.warn(
            section,
            f"parameter '{name}' is declared but never used in "
            "url/headers/query_params/body",
        )

    response = handler.get("response")
    if isinstance(response, dict):
        error_mapping = response.get("error_mapping") or {}
        if isinstance(error_mapping, dict):
            for k in error_mapping.keys():
                try:
                    int(k)
                except (TypeError, ValueError):
                    report.fail(
                        section,
                        f"response.error_mapping key {k!r} is not an int",
                    )

    # Detect secret refs and remind user.
    secret_refs: Set[str] = set()

    def _scan_secret(value: Any) -> None:
        if isinstance(value, str):
            for m in SECRET_PATTERN.findall(value):
                secret_refs.add(m)
        elif isinstance(value, dict):
            for v in value.values():
                _scan_secret(v)
        elif isinstance(value, list):
            for v in value:
                _scan_secret(v)

    _scan_secret(handler)
    for s in sorted(secret_refs):
        report.warn(
            "Secrets",
            f"references {{secret:{s}}} — confirm it exists in "
            "~/.flocks/config/.secret.json",
        )


def _validate_script_handler(
    handler: Dict[str, Any],
    report: Report,
    parameter_names: Set[str],
    yaml_path: Path,
) -> None:
    section = "Handler"

    script_file = handler.get("script_file")
    function_name = handler.get("function") or "handle"

    if not script_file:
        report.fail(section, "handler.script_file is missing")
        return

    script_path = (yaml_path.parent / script_file).resolve()
    if not script_path.is_file():
        report.fail(
            section,
            f"handler script file not found: {script_path}",
        )
        return
    report.ok(section, f"script_file resolved to {script_path}")

    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as e:
        report.fail(section, f"cannot parse script file: {e}")
        return

    target_fn: Optional[ast.AST] = None
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                target_fn = node
                break

    if target_fn is None:
        report.fail(
            section,
            f"function '{function_name}' not found in {script_path.name}",
        )
        return

    if not isinstance(target_fn, ast.AsyncFunctionDef):
        report.fail(
            section,
            f"function '{function_name}' must be 'async def' so the loader "
            "can await it",
        )
    else:
        report.ok(section, f"function = {function_name} (async)")

    args = target_fn.args
    pos_args = [a.arg for a in args.args]
    if not pos_args or pos_args[0] != "ctx":
        report.fail(
            section,
            f"function '{function_name}' first parameter must be 'ctx' "
            f"(got {pos_args!r})",
        )

    kwarg_names = set(pos_args[1:]) | {a.arg for a in args.kwonlyargs}
    has_var_kw = args.kwarg is not None

    if not has_var_kw:
        missing = parameter_names - kwarg_names
        for name in sorted(missing):
            report.fail(
                section,
                f"parameter '{name}' is declared in the YAML but not "
                f"accepted by '{function_name}({', '.join(pos_args)})'. "
                "Add it to the signature or accept **kwargs.",
            )

    # Detect imports of ToolResult — warn if missing.
    has_toolresult_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "flocks.tool.registry"
        and any(alias.name == "ToolResult" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not has_toolresult_import:
        report.warn(
            section,
            "script does not import ToolResult from flocks.tool.registry; "
            "the loader will wrap the return value but explicit ToolResult "
            "is recommended",
        )


def _validate_provider_yaml(
    yaml_path: Path, data: Dict[str, Any], report: Report,
) -> None:
    section = "Provider"
    prov_dir = _provider_dir(yaml_path)
    if prov_dir is None:
        return

    provider_file = prov_dir / "_provider.yaml"
    if not provider_file.is_file():
        report.fail(
            section,
            f"_provider.yaml is missing at {provider_file} — required for "
            "the API service card to render",
        )
        return
    report.ok(section, f"_provider.yaml found at {provider_file}")

    try:
        prov_data = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        report.fail(section, f"_provider.yaml parse error: {e}")
        return
    if not isinstance(prov_data, dict):
        report.fail(section, "_provider.yaml must be a mapping/dict")
        return

    if not prov_data.get("name"):
        report.fail(section, "_provider.yaml missing 'name'")
    if not prov_data.get("description"):
        report.fail(section, "_provider.yaml missing 'description' (English)")
    if not prov_data.get("description_cn"):
        report.warn(
            section,
            "_provider.yaml missing 'description_cn' — Chinese UI will fall "
            "back to English",
        )

    defaults = prov_data.get("defaults") or {}
    if not isinstance(defaults, dict):
        report.fail(section, "_provider.yaml.defaults must be a mapping")
        defaults = {}
    if not defaults.get("base_url"):
        report.fail(
            section,
            "_provider.yaml.defaults.base_url is missing — handler urls "
            "using {base_url} will resolve to '/path'",
        )
    if "category" not in defaults and not data.get("category"):
        report.warn(
            section,
            "_provider.yaml.defaults.category is missing and the tool also "
            "has no category — loader falls back to 'custom'",
        )

    auth = prov_data.get("auth")
    if auth is None:
        report.warn(
            section,
            "_provider.yaml has no 'auth' block — that is fine for "
            "open APIs, but most providers need a credential",
        )
    elif isinstance(auth, dict):
        if not auth.get("secret"):
            report.fail(section, "_provider.yaml.auth.secret is missing")
        inject_as = auth.get("inject_as")
        if inject_as not in {"header", "query_param", None}:
            report.fail(
                section,
                f"_provider.yaml.auth.inject_as = {inject_as!r}; "
                "must be 'header' or 'query_param'",
            )
        if inject_as == "query_param" and not auth.get("param_name"):
            report.warn(
                section,
                "_provider.yaml.auth.param_name missing — defaults to 'api_key'",
            )


# ---------------------------------------------------------------------------
# Python tool validation
# ---------------------------------------------------------------------------


def validate_python_tool(py_path: Path) -> Report:
    report = Report(target=py_path, mode="Python (Mode B)")
    try:
        source = py_path.read_text(encoding="utf-8")
    except OSError as e:
        report.fail("File", f"Cannot read file: {e}")
        return report

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        report.fail("Syntax", f"SyntaxError: {e}")
        return report

    has_registry_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "flocks.tool.registry"
        and any(
            alias.name in {"ToolRegistry", "ToolResult", "ToolContext"}
            for alias in node.names
        )
        for node in ast.walk(tree)
    )
    if not has_registry_import:
        report.fail(
            "Imports",
            "missing `from flocks.tool.registry import ...` "
            "(need at least ToolRegistry, ToolResult, ToolContext)",
        )
    else:
        report.ok("Imports", "imports flocks.tool.registry")

    decorated = list(_find_register_function_targets(tree))
    if not decorated:
        report.fail(
            "Decorator",
            "no @ToolRegistry.register_function decorator found — "
            "the tool will not be registered on import",
        )
        return report
    report.ok(
        "Decorator",
        f"found {len(decorated)} @ToolRegistry.register_function "
        f"target(s)",
    )

    for fn_node, decorator_call in decorated:
        _validate_python_decorated_function(fn_node, decorator_call, report)

    return report


def _find_register_function_targets(tree: ast.AST) -> Iterable:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "register_function"
                and isinstance(func.value, ast.Name)
                and func.value.id == "ToolRegistry"
            ):
                yield node, decorator


def _kwarg_value(call: ast.Call, name: str) -> Optional[ast.AST]:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _validate_python_decorated_function(
    fn_node: ast.AST, call: ast.Call, report: Report,
) -> None:
    section = f"Function {getattr(fn_node, 'name', '<unknown>')}"

    name = _const_str(_kwarg_value(call, "name"))
    description = _const_str(_kwarg_value(call, "description"))
    category_node = _kwarg_value(call, "category")
    parameters_node = _kwarg_value(call, "parameters")

    if not name:
        report.fail(section, "decorator missing or non-string 'name='")
    else:
        if not SNAKE_CASE_RE.match(name):
            report.warn(section, f"name '{name}' is not snake_case")
        if name in RESERVED_TOOL_NAMES:
            report.fail(
                section,
                f"name '{name}' collides with a built-in tool — pick another",
            )
        report.ok(section, f"name = {name}")

    if not description or not description.strip():
        report.fail(section, "decorator missing or empty 'description='")
    elif len(description.strip()) < 20:
        report.warn(
            section,
            f"description is only {len(description.strip())} chars — "
            "the LLM uses this to decide when to invoke the tool",
        )
    else:
        report.ok(section, f"description present ({len(description.strip())} chars)")

    if category_node is None:
        report.warn(
            section,
            "no 'category=' set — defaults to ToolCategory.CUSTOM",
        )
    elif isinstance(category_node, ast.Attribute):
        # ToolCategory.SOMETHING — accept; full validation requires runtime.
        report.ok(section, f"category = ToolCategory.{category_node.attr}")
    elif isinstance(category_node, ast.Constant) and isinstance(category_node.value, str):
        if category_node.value not in VALID_CATEGORIES:
            report.fail(
                section,
                f"category={category_node.value!r} is not in "
                f"{sorted(VALID_CATEGORIES)}",
            )
        else:
            report.ok(section, f"category = {category_node.value!r}")

    declared_params: List[str] = []
    if parameters_node is None:
        report.warn(
            section,
            "no 'parameters=' provided — the tool exposes zero arguments",
        )
    elif isinstance(parameters_node, ast.List):
        if not parameters_node.elts:
            report.warn(section, "'parameters=[]' is empty")
        for elt in parameters_node.elts:
            if isinstance(elt, ast.Call) and _is_tool_parameter_call(elt):
                pname = _const_str(_kwarg_value(elt, "name"))
                if not pname:
                    # Try positional first arg.
                    if elt.args and isinstance(elt.args[0], ast.Constant):
                        pname = elt.args[0].value
                if not pname:
                    report.fail(section, "ToolParameter() entry without 'name'")
                    continue
                declared_params.append(pname)

                ptype_node = _kwarg_value(elt, "type")
                if ptype_node is None:
                    report.warn(
                        section,
                        f"parameter '{pname}' missing 'type=' "
                        "(defaults will not work — type is required)",
                    )

                pdesc = _const_str(_kwarg_value(elt, "description"))
                if not pdesc or not pdesc.strip():
                    report.warn(
                        section,
                        f"parameter '{pname}' missing 'description=' — "
                        "the LLM cannot reliably fill it in",
                    )
        if declared_params:
            report.ok(section, f"parameters = {declared_params}")

    # Function signature checks
    args = fn_node.args
    pos_args = [a.arg for a in args.args]
    is_async = isinstance(fn_node, ast.AsyncFunctionDef)
    if not is_async:
        report.fail(
            section,
            f"function '{fn_node.name}' must be 'async def'",
        )
    else:
        report.ok(section, f"function '{fn_node.name}' is async def")
    if not pos_args or pos_args[0] != "ctx":
        report.fail(
            section,
            f"function '{fn_node.name}' first parameter must be 'ctx' "
            f"(got {pos_args!r})",
        )
    else:
        report.ok(section, f"signature = ({', '.join(pos_args)})")

    kwarg_names = set(pos_args[1:]) | {a.arg for a in args.kwonlyargs}
    has_var_kw = args.kwarg is not None
    if not has_var_kw and declared_params:
        missing = set(declared_params) - kwarg_names
        for name in sorted(missing):
            report.fail(
                section,
                f"parameter '{name}' is declared in the decorator but not "
                f"accepted by '{fn_node.name}({', '.join(pos_args)})'",
            )
        unused = kwarg_names - set(declared_params)
        for name in sorted(unused):
            if name in {"self", "cls"}:
                continue
            report.warn(
                section,
                f"function arg '{name}' is not declared as a ToolParameter",
            )

    # Detect ToolResult return.
    returns_toolresult = False
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "ToolResult":
                returns_toolresult = True
                break
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "ToolResult"
            ):
                returns_toolresult = True
                break
    if not returns_toolresult:
        report.warn(
            section,
            "no 'return ToolResult(...)' detected — loader will wrap the "
            "return value, but explicit ToolResult is recommended",
        )
    else:
        report.ok(section, "returns ToolResult(...)")


def _is_tool_parameter_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name) and func.id == "ToolParameter":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "ToolParameter":
        return True
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a Flocks tool YAML or Python file for missing "
            "metadata and handler information."
        )
    )
    parser.add_argument(
        "path",
        help="Path to the tool YAML or Python file to validate",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat WARN as failure (exit code 1 when any WARN exists)",
    )
    args = parser.parse_args(argv)

    target = Path(args.path).expanduser()
    if not target.exists():
        print(f"FAIL: file not found: {target}", file=sys.stderr)
        return 1

    if _looks_like_yaml(target):
        report = validate_yaml_tool(target)
    elif target.suffix == ".py":
        report = validate_python_tool(target)
    else:
        print(
            f"FAIL: unsupported file type {target.suffix!r}; "
            "expected .yaml/.yml/.py",
            file=sys.stderr,
        )
        return 1

    print(report.render())
    if report.fail_count > 0:
        return 1
    if args.strict and report.warn_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
